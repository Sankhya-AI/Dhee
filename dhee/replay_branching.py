"""Deterministic SWE-Replay branch and checkpoint planning.

The replay branching engine is a planner, not a git executor.  It creates the
branch graph, rollback anchors, checkpoints, and audit events a caller can use
to replay an SWE-style repair attempt in an isolated worktree or harness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


REPLAY_BRANCHING_SCHEMA = "dhee.replay_branching.v1"
BRANCH_METADATA_SCHEMA = "dhee.replay_branch.v1"
CHECKPOINT_SCHEMA = "dhee.replay_checkpoint.v1"
ROLLBACK_POINT_SCHEMA = "dhee.replay_rollback_point.v1"
AUDIT_EVENT_SCHEMA = "dhee.replay_audit_event.v1"

BRANCH_LOCALIZATION = "localization"
BRANCH_PATCH = "patch"
BRANCH_FAILED_TEST = "failed_test"
BRANCH_RECOVERY = "recovery"
BRANCH_SUBMIT = "submit"

BRANCH_SEQUENCE: Tuple[str, ...] = (
    BRANCH_LOCALIZATION,
    BRANCH_PATCH,
    BRANCH_FAILED_TEST,
    BRANCH_RECOVERY,
    BRANCH_SUBMIT,
)

BRANCH_PURPOSES = {
    BRANCH_LOCALIZATION: "Localize candidate files, symbols, and failure evidence before editing.",
    BRANCH_PATCH: "Apply the smallest candidate patch from the localized evidence.",
    BRANCH_FAILED_TEST: "Preserve failed verifier evidence without losing the patch attempt.",
    BRANCH_RECOVERY: "Recover from failed tests by rolling back to a safe checkpoint and patching again.",
    BRANCH_SUBMIT: "Run final verification gates and prepare a submit-ready proof bundle.",
}

BRANCH_ACTIONS = {
    BRANCH_LOCALIZATION: (
        "collect_issue_evidence",
        "rank_relevant_files",
        "bind_fail_to_pass_tests",
        "checkpoint_localization_context",
    ),
    BRANCH_PATCH: (
        "create_patch_candidate",
        "record_patch_files",
        "checkpoint_patch_candidate",
    ),
    BRANCH_FAILED_TEST: (
        "run_fail_to_pass_tests",
        "capture_failure_signature",
        "checkpoint_failed_test_evidence",
    ),
    BRANCH_RECOVERY: (
        "rollback_to_patch_checkpoint",
        "revise_patch_candidate",
        "checkpoint_recovery_candidate",
    ),
    BRANCH_SUBMIT: (
        "run_required_verification",
        "record_proof_bundle",
        "submit_patch",
    ),
}

BRANCH_PREFIXES = {
    BRANCH_LOCALIZATION: "loc",
    BRANCH_PATCH: "patch",
    BRANCH_FAILED_TEST: "fail",
    BRANCH_RECOVERY: "rec",
    BRANCH_SUBMIT: "sub",
}

READ_ONLY_GIT_COMMANDS = {
    ("rev-parse", "--show-toplevel"),
    ("rev-parse", "--short", "HEAD"),
    ("branch", "--show-current"),
    ("status", "--porcelain=v1", "--untracked-files=all"),
}

_LOCAL_PATH_RE = re.compile(r"(/Users/[^\s\"']+|/home/[^\s\"']+|[A-Za-z]:\\\\[^\s\"']+)")
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class ReplayBranchingEngine:
    """Build deterministic replay branch plans without mutating the repo."""

    branch_namespace: str = "swe-replay"
    actor: str = "codex"

    def build_plan(
        self,
        goal: str,
        *,
        repo: Optional[os.PathLike[str] | str] = None,
        task_id: Optional[str] = None,
        base_ref: Optional[str] = None,
        current_branch: Optional[str] = None,
        relevant_files: Optional[Iterable[str]] = None,
        patch_files: Optional[Iterable[str]] = None,
        test_commands: Optional[Iterable[str]] = None,
        failing_tests: Optional[Iterable[Any]] = None,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        return build_replay_branch_plan(
            goal,
            repo=repo,
            task_id=task_id,
            base_ref=base_ref,
            current_branch=current_branch,
            relevant_files=relevant_files,
            patch_files=patch_files,
            test_commands=test_commands,
            failing_tests=failing_tests,
            branch_namespace=self.branch_namespace,
            actor=self.actor,
            created_at=created_at,
        )


def build_replay_branch_plan(
    goal: str,
    *,
    repo: Optional[os.PathLike[str] | str] = None,
    task_id: Optional[str] = None,
    base_ref: Optional[str] = None,
    current_branch: Optional[str] = None,
    relevant_files: Optional[Iterable[str]] = None,
    patch_files: Optional[Iterable[str]] = None,
    test_commands: Optional[Iterable[str]] = None,
    failing_tests: Optional[Iterable[Any]] = None,
    branch_namespace: str = "swe-replay",
    actor: str = "codex",
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a deterministic SWE-Replay branch/checkpoint plan.

    The returned structure is intentionally executable-by-someone-else: it
    contains branch names and rollback hints, but this function never creates,
    switches, resets, or deletes git branches.
    """

    normalized_goal = _clean_text(goal)
    if not normalized_goal:
        raise ValueError("goal is required")

    repo_root = _resolve_repo_root(repo)
    repo_label = _repo_label(repo_root)
    observed = _observe_git_state(repo_root)
    observed_base_ref = base_ref or observed.get("head_commit") or "workspace"
    observed_branch = current_branch or observed.get("branch") or "main"
    files = _normalize_list(relevant_files)
    patch_targets = _normalize_list(patch_files) or _patch_targets(files)
    tests = _normalize_list(test_commands)
    failure_refs = _normalize_failures(failing_tests)
    created = _normalize_created_at(created_at)
    task = _clean_id(task_id) if task_id else "task_" + _stable_hash(
        {"goal": normalized_goal, "repo": repo_label},
        12,
    )

    seed = {
        "schema_version": REPLAY_BRANCHING_SCHEMA,
        "task_id": task,
        "goal": normalized_goal,
        "repo": repo_label,
        "base_ref": observed_base_ref,
        "current_branch": observed_branch,
        "relevant_files": files,
        "patch_files": patch_targets,
        "test_commands": tests,
        "failing_tests": failure_refs,
        "branch_namespace": _safe_namespace(branch_namespace),
    }
    plan_id = "rpl_" + _stable_hash(seed, 16)
    task_slug = _slugify(task if task_id else normalized_goal, limit=36)

    branches: List[Dict[str, Any]] = []
    checkpoints: List[Dict[str, Any]] = []
    rollback_points: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    audit: List[Dict[str, Any]] = []
    seq = 1

    def add_event(
        event_type: str,
        subject_id: str,
        *,
        branch_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        nonlocal seq
        event = _audit_event(
            plan_id,
            seq,
            event_type,
            subject_id,
            actor=actor,
            occurred_at=created,
            branch_id=branch_id,
            details=details or {},
        )
        audit.append(event)
        seq += 1

    add_event(
        "plan.created",
        plan_id,
        details={
            "task_id": task,
            "repo": repo_label,
            "branch_namespace": _safe_namespace(branch_namespace),
        },
    )
    add_event(
        "base.observed",
        plan_id,
        details={
            "base_ref": observed_base_ref,
            "current_branch": observed_branch,
            "dirty": bool(observed.get("dirty")),
            "changed_paths": list(observed.get("changed_paths") or []),
        },
    )

    previous_branch: Optional[Dict[str, Any]] = None
    previous_checkpoint_id: Optional[str] = None
    branch_index: Dict[str, Dict[str, Any]] = {}

    for index, kind in enumerate(BRANCH_SEQUENCE, start=1):
        branch_hash = _stable_hash({"plan_id": plan_id, "kind": kind, "index": index}, 10)
        prefix = BRANCH_PREFIXES[kind]
        branch_id = f"br_{prefix}_{branch_hash}"
        branch_name = "/".join(
            [
                _safe_namespace(branch_namespace),
                task_slug,
                f"{index:02d}-{kind.replace('_', '-')}-{branch_hash[:7]}",
            ]
        )
        checkpoint_id = f"cp_{prefix}_" + _stable_hash(
            {"plan_id": plan_id, "kind": kind, "checkpoint": "exit"},
            12,
        )
        rollback_id = f"rb_{prefix}_" + _stable_hash(
            {"plan_id": plan_id, "kind": kind, "rollback": "entry"},
            12,
        )
        parent_id = previous_branch["branch_id"] if previous_branch else None
        parent_name = previous_branch["name"] if previous_branch else observed_branch
        parent_checkpoint = previous_checkpoint_id
        rollback_ref = observed_base_ref if previous_checkpoint_id is None else f"checkpoint:{previous_checkpoint_id}"
        branch = {
            "schema_version": BRANCH_METADATA_SCHEMA,
            "branch_id": branch_id,
            "kind": kind,
            "name": branch_name,
            "purpose": BRANCH_PURPOSES[kind],
            "index": index,
            "parent_id": parent_id,
            "parent_branch_name": parent_name,
            "parent_checkpoint_id": parent_checkpoint,
            "child_ids": [],
            "child_branch_names": [],
            "checkpoint_id": checkpoint_id,
            "rollback_point_id": rollback_id,
            "rollback_to": rollback_ref,
            "expected_actions": list(BRANCH_ACTIONS[kind]),
            "metadata": _branch_metadata(
                kind,
                relevant_files=files,
                patch_files=patch_targets,
                test_commands=tests,
                failing_tests=failure_refs,
            ),
        }
        checkpoint = _checkpoint(
            checkpoint_id,
            branch_id,
            kind,
            parent_checkpoint_id=parent_checkpoint,
            rollback_point_id=rollback_id,
            relevant_files=files,
            patch_files=patch_targets,
            test_commands=tests,
            failing_tests=failure_refs,
        )
        rollback_point = _rollback_point(
            rollback_id,
            branch_id,
            kind,
            ref=rollback_ref,
            checkpoint_id=checkpoint_id,
            base_ref=observed_base_ref,
        )
        branches.append(branch)
        checkpoints.append(checkpoint)
        rollback_points.append(rollback_point)
        branch_index[branch_id] = branch

        add_event(
            "rollback_point.planned",
            rollback_id,
            branch_id=branch_id,
            details={
                "kind": kind,
                "ref": rollback_ref,
                "checkpoint_id": checkpoint_id,
            },
        )
        add_event(
            "checkpoint.planned",
            checkpoint_id,
            branch_id=branch_id,
            details={
                "kind": kind,
                "parent_checkpoint_id": parent_checkpoint,
                "rollback_point_id": rollback_id,
            },
        )
        add_event(
            "branch.planned",
            branch_id,
            branch_id=branch_id,
            details={
                "kind": kind,
                "name": branch_name,
                "parent_id": parent_id,
                "parent_branch_name": parent_name,
            },
        )

        if previous_branch is not None:
            edge_id = "edge_" + _stable_hash(
                {"plan_id": plan_id, "parent": previous_branch["branch_id"], "child": branch_id},
                12,
            )
            edge = {
                "edge_id": edge_id,
                "parent_id": previous_branch["branch_id"],
                "child_id": branch_id,
                "relationship": "successor",
                "parent_checkpoint_id": previous_checkpoint_id,
                "child_rollback_point_id": rollback_id,
            }
            edges.append(edge)
            previous_branch["child_ids"].append(branch_id)
            previous_branch["child_branch_names"].append(branch_name)
            add_event(
                "branch.linked",
                edge_id,
                branch_id=branch_id,
                details=edge,
            )

        previous_branch = branch
        previous_checkpoint_id = checkpoint_id

    patch_branch = _find_branch(branches, BRANCH_PATCH)
    failed_branch = _find_branch(branches, BRANCH_FAILED_TEST)
    recovery_branch = _find_branch(branches, BRANCH_RECOVERY)
    submit_branch = _find_branch(branches, BRANCH_SUBMIT)
    if failed_branch and patch_branch:
        failed_branch["metadata"]["failure_rollback_target_branch_id"] = patch_branch["branch_id"]
        failed_branch["metadata"]["failure_rollback_target_checkpoint_id"] = patch_branch["checkpoint_id"]
    if recovery_branch and patch_branch:
        recovery_branch["metadata"]["recovers_from_branch_id"] = failed_branch["branch_id"] if failed_branch else None
        recovery_branch["metadata"]["recovery_starts_at_checkpoint_id"] = patch_branch["checkpoint_id"]
    if submit_branch and recovery_branch:
        submit_branch["metadata"]["requires_successful_branch_id"] = recovery_branch["branch_id"]
        submit_branch["metadata"]["submit_gates"] = _submit_gates(tests)

    add_event(
        "plan.ready",
        plan_id,
        details={
            "branch_count": len(branches),
            "checkpoint_count": len(checkpoints),
            "rollback_point_count": len(rollback_points),
            "audit_event_count": len(audit) + 1,
        },
    )

    return {
        "schema_version": REPLAY_BRANCHING_SCHEMA,
        "plan_id": plan_id,
        "task_id": task,
        "goal": normalized_goal,
        "repo": {
            "path": str(repo_root) if repo_root else "",
            "label": repo_label,
            "current_branch": observed_branch,
            "base_ref": observed_base_ref,
            "dirty": bool(observed.get("dirty")),
            "changed_paths": list(observed.get("changed_paths") or []),
        },
        "branch_namespace": _safe_namespace(branch_namespace),
        "branch_sequence": list(BRANCH_SEQUENCE),
        "branches": branches,
        "edges": edges,
        "checkpoints": checkpoints,
        "rollback_points": rollback_points,
        "audit_events": audit,
        "execution_policy": {
            "planner_only": True,
            "git_mutations_executed": False,
            "destructive_git_operations": [],
            "allowed_git_observation_commands": sorted(" ".join(cmd) for cmd in READ_ONLY_GIT_COMMANDS),
        },
    }


def build_swe_replay_plan(goal: str, **kwargs: Any) -> Dict[str, Any]:
    """Compatibility alias for callers that use SWE-Replay terminology."""

    return build_replay_branch_plan(goal, **kwargs)


def _branch_metadata(
    kind: str,
    *,
    relevant_files: Sequence[str],
    patch_files: Sequence[str],
    test_commands: Sequence[str],
    failing_tests: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    common = {
        "relevant_files": list(relevant_files),
        "test_commands": list(test_commands),
    }
    if kind == BRANCH_LOCALIZATION:
        return {
            **common,
            "localization_outputs": ["ranked_files", "symbol_candidates", "failure_signature_refs"],
            "mutation_allowed": False,
        }
    if kind == BRANCH_PATCH:
        return {
            **common,
            "patch_files": list(patch_files),
            "mutation_allowed": True,
            "requires_localization_checkpoint": True,
        }
    if kind == BRANCH_FAILED_TEST:
        return {
            **common,
            "failing_tests": list(failing_tests),
            "mutation_allowed": False,
            "captures_failure_artifacts": True,
        }
    if kind == BRANCH_RECOVERY:
        return {
            **common,
            "patch_files": list(patch_files),
            "mutation_allowed": True,
            "uses_failed_test_evidence": True,
        }
    if kind == BRANCH_SUBMIT:
        return {
            **common,
            "patch_files": list(patch_files),
            "mutation_allowed": False,
            "proof_bundle_required": True,
        }
    return common


def _checkpoint(
    checkpoint_id: str,
    branch_id: str,
    kind: str,
    *,
    parent_checkpoint_id: Optional[str],
    rollback_point_id: str,
    relevant_files: Sequence[str],
    patch_files: Sequence[str],
    test_commands: Sequence[str],
    failing_tests: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    captures = {
        BRANCH_LOCALIZATION: ["localized_files", "failure_evidence", "context_budget"],
        BRANCH_PATCH: ["candidate_diff_ref", "edited_files", "pre_verification_state"],
        BRANCH_FAILED_TEST: ["failed_commands", "stdout_stderr_refs", "failure_signature"],
        BRANCH_RECOVERY: ["recovery_diff_ref", "regression_notes", "rerun_plan"],
        BRANCH_SUBMIT: ["verification_summary", "proof_bundle_ref", "submit_decision"],
    }[kind]
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "checkpoint_id": checkpoint_id,
        "branch_id": branch_id,
        "kind": f"{kind}_checkpoint",
        "parent_checkpoint_id": parent_checkpoint_id,
        "rollback_point_id": rollback_point_id,
        "captures": captures,
        "data_refs": {
            "relevant_files": list(relevant_files),
            "patch_files": list(patch_files),
            "test_commands": list(test_commands),
            "failing_tests": list(failing_tests),
        },
    }


def _rollback_point(
    rollback_id: str,
    branch_id: str,
    kind: str,
    *,
    ref: str,
    checkpoint_id: str,
    base_ref: str,
) -> Dict[str, Any]:
    return {
        "schema_version": ROLLBACK_POINT_SCHEMA,
        "rollback_point_id": rollback_id,
        "branch_id": branch_id,
        "kind": f"{kind}_entry",
        "ref": ref,
        "base_ref": base_ref,
        "checkpoint_id": checkpoint_id,
        "restore_strategy": "caller-managed isolated worktree or explicit user-approved git action",
        "planner_git_mutation": False,
    }


def _submit_gates(test_commands: Sequence[str]) -> List[Dict[str, Any]]:
    gates: List[Dict[str, Any]] = [
        {
            "gate_id": "gate_diff_scope",
            "kind": "diff_scope",
            "required": True,
            "description": "Only task-owned files changed.",
        },
        {
            "gate_id": "gate_no_secrets",
            "kind": "security",
            "required": True,
            "description": "No secrets or forbidden paths introduced.",
        },
    ]
    for index, command in enumerate(test_commands, start=1):
        gates.append(
            {
                "gate_id": f"gate_test_{index}",
                "kind": "test",
                "required": True,
                "command": command,
            }
        )
    gates.append(
        {
            "gate_id": "gate_proof_bundle",
            "kind": "proof_bundle",
            "required": True,
            "description": "Verification evidence is attached before submit.",
        }
    )
    return gates


def _audit_event(
    plan_id: str,
    sequence: int,
    event_type: str,
    subject_id: str,
    *,
    actor: str,
    occurred_at: str,
    branch_id: Optional[str],
    details: Dict[str, Any],
) -> Dict[str, Any]:
    clean_details = _sanitize_obj(details)
    event_seed = {
        "plan_id": plan_id,
        "sequence": sequence,
        "event_type": event_type,
        "subject_id": subject_id,
        "branch_id": branch_id,
        "details": clean_details,
    }
    return {
        "schema_version": AUDIT_EVENT_SCHEMA,
        "event_id": "evt_" + _stable_hash(event_seed, 14),
        "sequence": sequence,
        "event_type": event_type,
        "subject_id": subject_id,
        "branch_id": branch_id,
        "actor": actor or "codex",
        "occurred_at": occurred_at,
        "details": clean_details,
    }


def _find_branch(branches: Sequence[Dict[str, Any]], kind: str) -> Optional[Dict[str, Any]]:
    for branch in branches:
        if branch.get("kind") == kind:
            return branch
    return None


def _resolve_repo_root(repo: Optional[os.PathLike[str] | str]) -> Path:
    base = Path(repo or os.getcwd()).expanduser().resolve()
    proc = _git_read(base, ["rev-parse", "--show-toplevel"])
    if proc:
        return Path(proc).resolve()
    return base


def _observe_git_state(repo_root: Path) -> Dict[str, Any]:
    status = _git_read(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    changed_paths: List[str] = []
    if status:
        for line in status.splitlines():
            path = _status_path(line)
            if path:
                changed_paths.append(path)
    return {
        "branch": _git_read(repo_root, ["branch", "--show-current"]),
        "head_commit": _git_read(repo_root, ["rev-parse", "--short", "HEAD"]),
        "dirty": bool(changed_paths),
        "changed_paths": sorted(set(changed_paths)),
    }


def _git_read(repo_root: Path, args: Sequence[str]) -> str:
    tuple_args = tuple(args)
    if tuple_args not in READ_ONLY_GIT_COMMANDS:
        raise ValueError(f"git observation command is not allowed: {' '.join(args)}")
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _status_path(line: str) -> str:
    if not line:
        return ""
    path = (line[3:] if len(line) > 2 and line[2] == " " else line[2:]).strip()
    if " -> " in path:
        _old, path = path.split(" -> ", 1)
    return path.replace(os.sep, "/")


def _repo_label(repo_root: Path) -> str:
    return repo_root.name if repo_root else "workspace"


def _patch_targets(relevant_files: Sequence[str]) -> List[str]:
    return [path for path in relevant_files if not path.startswith("tests/")]


def _normalize_list(values: Optional[Iterable[str]]) -> List[str]:
    if values is None:
        return []
    out: List[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in out:
            out.append(text)
    return sorted(out)


def _normalize_failures(values: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    if values is None:
        return []
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            command = _clean_text(value.get("command") or value.get("test") or value.get("id") or "")
            item = {
                "command": command,
                "status": _clean_text(value.get("status") or "failed"),
                "evidence_ref": _clean_text(value.get("evidence_ref") or value.get("ref") or ""),
            }
            if value.get("failure_signature"):
                item["failure_signature"] = _clean_text(value.get("failure_signature"))
        else:
            command = _clean_text(value)
            item = {"command": command, "status": "failed", "evidence_ref": ""}
        if not command:
            continue
        key = _stable_hash(item, 12)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return sorted(out, key=lambda item: (item.get("command", ""), item.get("evidence_ref", "")))


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_id(value: Any) -> str:
    text = _clean_text(value)
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", text).strip("-") or "task"


def _normalize_created_at(value: Optional[str]) -> str:
    if value:
        return str(value)
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(value: str, *, limit: int) -> str:
    lowered = str(value or "").lower()
    lowered = _SAFE_SLUG_RE.sub("-", lowered).strip("-._")
    lowered = re.sub(r"-{2,}", "-", lowered)
    return (lowered or "task")[:limit].strip("-._") or "task"


def _safe_namespace(value: str) -> str:
    parts = [_slugify(part, limit=48) for part in str(value or "swe-replay").split("/") if part.strip()]
    return "/".join(parts) or "swe-replay"


def _stable_hash(data: Any, length: int = 16) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _sanitize_obj(value: Any) -> Any:
    if isinstance(value, str):
        home = str(Path.home())
        text = value.replace(home, "$HOME") if home else value
        return _LOCAL_PATH_RE.sub("<local-path>", text)
    if isinstance(value, list):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_obj(item) for key, item in value.items()}
    return value

