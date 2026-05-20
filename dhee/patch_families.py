"""Deterministic patch-family planning for isolated repair attempts.

The scaffold in this module models candidate patches and the worktrees they
would run in.  It intentionally treats patch bodies as metadata only: raw diffs
are hashed and measured, never stored in plans and never applied by the
executor helpers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


PATCH_CANDIDATE_SCHEMA = "dhee.patch_candidate.v1"
PATCH_FAMILY_PLAN_SCHEMA = "dhee.patch_family_plan.v1"
PATCH_FAMILY_EXECUTION_SCHEMA = "dhee.patch_family_execution.v1"
PATCH_FAMILY_RANKING_SCHEMA = "dhee.patch_family_ranking.v1"

DEFAULT_WORKTREE_NAMESPACE = "patch-family"
RAW_PATCH_KEYS = {"patch", "diff", "unified_diff", "patch_text", "raw_patch"}
RISK_SCORES = {
    "none": 0,
    "low": 1,
    "medium": 3,
    "unknown": 4,
    "high": 6,
    "critical": 9,
}


def _stable_hash(data: Any, length: int = 16) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _repo_root(repo: str | os.PathLike[str] | None) -> Path:
    base = Path(repo or os.getcwd()).expanduser().resolve()
    proc = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"{base} is not inside a git repository")
    return Path(proc.stdout.strip()).resolve()


def _git_out(repo_root: Path, args: Sequence[str], default: str = "") -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return default
    return proc.stdout.strip()


def _slug(value: Any, fallback: str = "candidate") -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9._/-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-/.")
    return text or fallback


def _safe_branch_segment(value: Any, fallback: str = "candidate") -> str:
    text = _slug(value, fallback=fallback).replace("/", "-")
    return text[:64] or fallback


def _dedupe_strings(values: Iterable[Any]) -> Tuple[str, ...]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _raw_patch_from_mapping(data: Mapping[str, Any]) -> str:
    for key in RAW_PATCH_KEYS:
        value = data.get(key)
        if value is not None:
            return str(value)
    return ""


def _metadata_without_raw_patch(data: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        str(key): value
        for key, value in dict(data or {}).items()
        if str(key) not in RAW_PATCH_KEYS
    }


def _paths_from_unified_diff(diff_text: str) -> Tuple[str, ...]:
    paths: List[str] = []
    for line in str(diff_text or "").splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        raw = line[4:].strip().split("\t", 1)[0]
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        if raw and raw not in paths:
            paths.append(raw)
    return tuple(paths)


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _risk_score(risk: Any) -> int:
    return RISK_SCORES.get(str(risk or "unknown").strip().lower(), RISK_SCORES["unknown"])


@dataclass
class PatchCandidate:
    """Metadata-only candidate patch description."""

    family: str
    title: str
    summary: str = ""
    touched_paths: Tuple[str, ...] = ()
    patch_ref: str = ""
    patch_sha256: str = ""
    patch_bytes: int = 0
    risk: str = "unknown"
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    candidate_id: str = ""

    def __post_init__(self) -> None:
        self.family = str(self.family or "unknown").strip()
        self.title = str(self.title or self.family).strip()
        self.summary = str(self.summary or "").strip()
        self.patch_ref = str(self.patch_ref or "").strip()
        self.patch_sha256 = str(self.patch_sha256 or "").strip()
        self.patch_bytes = max(0, int(self.patch_bytes or 0))
        self.risk = str(self.risk or "unknown").strip().lower()
        self.confidence = _clamp_confidence(self.confidence)
        self.touched_paths = _dedupe_strings(self.touched_paths)
        self.metadata = _metadata_without_raw_patch(self.metadata)
        if not self.candidate_id:
            self.candidate_id = "cand_" + _stable_hash(
                {
                    "family": self.family,
                    "title": self.title,
                    "summary": self.summary,
                    "touched_paths": self.touched_paths,
                    "patch_ref": self.patch_ref,
                    "patch_sha256": self.patch_sha256,
                    "risk": self.risk,
                    "metadata": self.metadata,
                },
                14,
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": PATCH_CANDIDATE_SCHEMA,
            "candidate_id": self.candidate_id,
            "family": self.family,
            "title": self.title,
            "summary": self.summary,
            "touched_paths": list(self.touched_paths),
            "patch_ref": self.patch_ref,
            "patch_sha256": self.patch_sha256,
            "patch_bytes": self.patch_bytes,
            "risk": self.risk,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }


@dataclass
class VerifierCommand:
    """Verifier command associated with a candidate worktree."""

    command: str
    kind: str = "fail_to_pass"
    required: bool = True
    timeout_sec: Optional[int] = None
    source: str = "manual"
    command_id: str = ""

    def __post_init__(self) -> None:
        self.command = str(self.command or "").strip()
        self.kind = str(self.kind or "fail_to_pass").strip()
        self.required = bool(self.required)
        self.source = str(self.source or "manual").strip()
        if self.timeout_sec is not None:
            self.timeout_sec = max(1, int(self.timeout_sec))
        if not self.command_id:
            self.command_id = "cmd_" + _stable_hash(
                {
                    "command": self.command,
                    "kind": self.kind,
                    "required": self.required,
                    "source": self.source,
                },
                12,
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command": self.command,
            "kind": self.kind,
            "required": self.required,
            "timeout_sec": self.timeout_sec,
            "source": self.source,
        }


def normalize_patch_candidate(candidate: PatchCandidate | Mapping[str, Any]) -> PatchCandidate:
    """Return a sanitized, metadata-only patch candidate."""

    if isinstance(candidate, PatchCandidate):
        return candidate
    if not isinstance(candidate, Mapping):
        raise TypeError("patch candidates must be mappings or PatchCandidate instances")

    raw_patch = _raw_patch_from_mapping(candidate)
    patch_sha256 = str(candidate.get("patch_sha256") or candidate.get("patch_hash") or "").strip()
    patch_bytes = int(candidate.get("patch_bytes") or 0)
    if raw_patch:
        patch_sha256 = patch_sha256 or _sha256_text(raw_patch)
        patch_bytes = patch_bytes or len(raw_patch.encode("utf-8"))

    explicit_paths = candidate.get("touched_paths") or candidate.get("files") or candidate.get("paths")
    touched_paths = _dedupe_strings(_as_list(explicit_paths) + list(_paths_from_unified_diff(raw_patch)))
    metadata = _metadata_without_raw_patch(candidate.get("metadata") or {})
    for key in ("source", "source_agent", "rationale", "labels", "owner", "notes"):
        if key in candidate and key not in metadata:
            metadata[key] = candidate[key]

    return PatchCandidate(
        family=str(candidate.get("family") or candidate.get("family_name") or candidate.get("name") or "unknown"),
        title=str(candidate.get("title") or candidate.get("intent") or candidate.get("summary") or "candidate patch"),
        summary=str(candidate.get("summary") or candidate.get("intent") or ""),
        touched_paths=touched_paths,
        patch_ref=str(candidate.get("patch_ref") or candidate.get("ref") or ""),
        patch_sha256=patch_sha256,
        patch_bytes=patch_bytes,
        risk=str(candidate.get("risk") or "unknown"),
        confidence=_clamp_confidence(candidate.get("confidence")),
        metadata=metadata,
        candidate_id=str(candidate.get("candidate_id") or ""),
    )


def normalize_verifier_command(command: VerifierCommand | str | Mapping[str, Any]) -> VerifierCommand:
    """Return a normalized verifier command without executing it."""

    if isinstance(command, VerifierCommand):
        return command
    if isinstance(command, str):
        return VerifierCommand(command=command)
    if not isinstance(command, Mapping):
        raise TypeError("verifier commands must be strings, mappings, or VerifierCommand instances")
    return VerifierCommand(
        command=str(command.get("command") or ""),
        kind=str(command.get("kind") or command.get("type") or "fail_to_pass"),
        required=bool(command.get("required", True)),
        timeout_sec=command.get("timeout_sec"),
        source=str(command.get("source") or "manual"),
        command_id=str(command.get("command_id") or ""),
    )


def _normalize_verifiers(commands: Iterable[VerifierCommand | str | Mapping[str, Any]]) -> List[VerifierCommand]:
    out: List[VerifierCommand] = []
    seen: set[str] = set()
    for item in commands or []:
        verifier = normalize_verifier_command(item)
        if not verifier.command or verifier.command in seen:
            continue
        seen.add(verifier.command)
        out.append(verifier)
    return out


def build_worktree_creation_plan(
    repo: str | os.PathLike[str],
    candidate: PatchCandidate | Mapping[str, Any],
    *,
    base_ref: str = "HEAD",
    worktree_root: str | os.PathLike[str] | None = None,
    namespace: str = DEFAULT_WORKTREE_NAMESPACE,
) -> Dict[str, Any]:
    """Build the deterministic git worktree creation plan for one candidate."""

    repo_root = _repo_root(repo)
    normalized = normalize_patch_candidate(candidate)
    base_ref = str(base_ref or "HEAD").strip()
    base_commit = _git_out(repo_root, ["rev-parse", "--verify", base_ref], default=base_ref)
    root = Path(worktree_root).expanduser().resolve() if worktree_root else repo_root / ".dhee" / "worktrees" / namespace
    branch_segment = _safe_branch_segment(normalized.family)
    branch_name = f"codex/{namespace}/{branch_segment}/{normalized.candidate_id}"
    worktree_path = (root / f"{normalized.candidate_id}-{branch_segment}").resolve()
    create_command = [
        "git",
        "-C",
        str(repo_root),
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        base_ref,
    ]
    return {
        "schema_version": "dhee.patch_family_worktree_plan.v1",
        "plan_id": "wt_" + _stable_hash(
            {
                "candidate_id": normalized.candidate_id,
                "repo": str(repo_root),
                "base_ref": base_ref,
                "base_commit": base_commit,
                "worktree_path": str(worktree_path),
                "branch_name": branch_name,
            },
            14,
        ),
        "candidate_id": normalized.candidate_id,
        "repo": str(repo_root),
        "base_ref": base_ref,
        "base_commit": base_commit,
        "worktree_path": str(worktree_path),
        "branch_name": branch_name,
        "preflight_commands": [
            ["git", "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            ["git", "-C", str(repo_root), "rev-parse", "--verify", base_ref],
            ["git", "-C", str(repo_root), "status", "--porcelain"],
        ],
        "create_command": create_command,
        "post_create_checks": [
            ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
            ["git", "-C", str(worktree_path), "status", "--porcelain"],
        ],
        "isolation": {
            "strategy": "git_worktree_per_candidate",
            "base_checkout_unchanged": True,
            "patch_application": "not_performed_by_scaffold",
            "verifier_cwd": str(worktree_path),
        },
    }


def build_cleanup_plan(worktree_plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Build cleanup commands for a planned candidate worktree."""

    repo = str(worktree_plan.get("repo") or "")
    candidate_id = str(worktree_plan.get("candidate_id") or "")
    worktree_path = str(worktree_plan.get("worktree_path") or "")
    branch_name = str(worktree_plan.get("branch_name") or "")
    commands = [
        {
            "name": "remove_worktree",
            "argv": ["git", "-C", repo, "worktree", "remove", "--force", worktree_path],
            "required": True,
        },
        {
            "name": "delete_branch",
            "argv": ["git", "-C", repo, "branch", "-D", branch_name],
            "required": False,
        },
        {
            "name": "prune_worktrees",
            "argv": ["git", "-C", repo, "worktree", "prune"],
            "required": False,
        },
    ]
    return {
        "schema_version": "dhee.patch_family_cleanup_plan.v1",
        "cleanup_id": "cleanup_" + _stable_hash(
            {
                "candidate_id": candidate_id,
                "repo": repo,
                "worktree_path": worktree_path,
                "branch_name": branch_name,
            },
            14,
        ),
        "candidate_id": candidate_id,
        "worktree_path": worktree_path,
        "branch_name": branch_name,
        "commands": commands,
        "notes": [
            "Cleanup is safe to dry-run.",
            "Branch deletion is optional because worktree creation may have been skipped.",
        ],
    }


def associate_verifiers_with_candidate(
    candidate: PatchCandidate | Mapping[str, Any],
    worktree_plan: Mapping[str, Any],
    verifier_commands: Iterable[VerifierCommand | str | Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach normalized verifier commands to the candidate worktree cwd."""

    normalized = normalize_patch_candidate(candidate)
    worktree_path = str(worktree_plan.get("worktree_path") or "")
    associated: List[Dict[str, Any]] = []
    for verifier in _normalize_verifiers(verifier_commands):
        item = verifier.to_dict()
        item.update(
            {
                "candidate_id": normalized.candidate_id,
                "cwd": worktree_path,
                "execution": "associated_not_executed",
            }
        )
        associated.append(item)
    return associated


def build_ranking_inputs(
    candidate: PatchCandidate | Mapping[str, Any],
    verifier_commands: Iterable[VerifierCommand | str | Mapping[str, Any]],
) -> Dict[str, Any]:
    """Build deterministic ranking features before any verifier execution."""

    normalized = normalize_patch_candidate(candidate)
    verifiers = _normalize_verifiers(verifier_commands)
    required = [item for item in verifiers if item.required]
    optional = [item for item in verifiers if not item.required]
    return {
        "schema_version": "dhee.patch_family_ranking_inputs.v1",
        "candidate_id": normalized.candidate_id,
        "family": normalized.family,
        "risk": normalized.risk,
        "risk_score": _risk_score(normalized.risk),
        "confidence": normalized.confidence,
        "touched_path_count": len(normalized.touched_paths),
        "patch_bytes": normalized.patch_bytes,
        "required_verifier_count": len(required),
        "optional_verifier_count": len(optional),
        "verifier_command_ids": [item.command_id for item in verifiers],
        "tie_breaker": normalized.candidate_id,
    }


def build_patch_family_plan(
    repo: str | os.PathLike[str],
    candidates: Iterable[PatchCandidate | Mapping[str, Any]],
    verifier_commands: Iterable[VerifierCommand | str | Mapping[str, Any]],
    *,
    base_ref: str = "HEAD",
    worktree_root: str | os.PathLike[str] | None = None,
    namespace: str = DEFAULT_WORKTREE_NAMESPACE,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Build a complete, deterministic patch-family execution plan."""

    repo_root = _repo_root(repo)
    base_ref = str(base_ref or "HEAD").strip()
    base_commit = _git_out(repo_root, ["rev-parse", "--verify", base_ref], default=base_ref)
    normalized_candidates = [normalize_patch_candidate(item) for item in candidates or []]
    verifiers = _normalize_verifiers(verifier_commands)
    families: List[Dict[str, Any]] = []
    for candidate in normalized_candidates:
        worktree = build_worktree_creation_plan(
            repo_root,
            candidate,
            base_ref=base_ref,
            worktree_root=worktree_root,
            namespace=namespace,
        )
        families.append(
            {
                "candidate": candidate.to_dict(),
                "worktree": worktree,
                "verifiers": associate_verifiers_with_candidate(candidate, worktree, verifiers),
                "ranking_inputs": build_ranking_inputs(candidate, verifiers),
                "cleanup": build_cleanup_plan(worktree),
            }
        )

    return {
        "schema_version": PATCH_FAMILY_PLAN_SCHEMA,
        "plan_id": "pf_" + _stable_hash(
            {
                "repo": str(repo_root),
                "base_ref": base_ref,
                "base_commit": base_commit,
                "candidate_ids": [item.candidate_id for item in normalized_candidates],
                "verifier_ids": [item.command_id for item in verifiers],
                "worktree_root": str(Path(worktree_root).expanduser().resolve()) if worktree_root else "",
                "namespace": namespace,
            },
            16,
        ),
        "repo": str(repo_root),
        "base_ref": base_ref,
        "base_commit": base_commit,
        "families": families,
        "policy": {
            "dry_run_default": bool(dry_run),
            "patch_application": "metadata_only_never_applied",
            "verifier_execution": "associated_not_executed",
            "shell": False,
        },
        "executor_scaffold": [
            "preflight_git_state",
            "create_isolated_worktree",
            "delegate_supervised_patch_application",
            "run_associated_verifiers",
            "rank_results",
            "cleanup_worktree",
        ],
    }


def _run_argv(argv: Sequence[str], *, timeout_sec: int) -> Dict[str, Any]:
    started_command = list(argv)
    try:
        proc = subprocess.run(
            started_command,
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_sec or 60)),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "blocked",
            "argv": started_command,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "status": "passed" if proc.returncode == 0 else "failed",
        "argv": started_command,
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _is_generated_worktree_create(argv: Sequence[str]) -> bool:
    parts = list(argv or [])
    return len(parts) >= 8 and parts[0] == "git" and "worktree" in parts and "add" in parts and "-b" in parts


def execute_worktree_creation(
    worktree_plan: Mapping[str, Any],
    *,
    dry_run: bool = True,
    timeout_sec: int = 60,
) -> Dict[str, Any]:
    """Create a candidate worktree, or return the command in dry-run mode."""

    argv = list(worktree_plan.get("create_command") or [])
    if not _is_generated_worktree_create(argv):
        return {
            "schema_version": PATCH_FAMILY_EXECUTION_SCHEMA,
            "status": "blocked",
            "phase": "create_worktree",
            "reason": "worktree create command was not generated by patch_families",
            "argv": argv,
        }
    if dry_run:
        return {
            "schema_version": PATCH_FAMILY_EXECUTION_SCHEMA,
            "status": "planned",
            "phase": "create_worktree",
            "dry_run": True,
            "argv": argv,
            "patch_application": "not_performed_by_scaffold",
        }
    result = _run_argv(argv, timeout_sec=timeout_sec)
    return {
        "schema_version": PATCH_FAMILY_EXECUTION_SCHEMA,
        "phase": "create_worktree",
        "dry_run": False,
        **result,
        "patch_application": "not_performed_by_scaffold",
    }


def execute_cleanup_plan(
    cleanup_plan: Mapping[str, Any],
    *,
    dry_run: bool = True,
    timeout_sec: int = 60,
) -> Dict[str, Any]:
    """Run or dry-run cleanup commands for a candidate worktree."""

    allowed_names = {"remove_worktree", "delete_branch", "prune_worktrees"}
    commands = [dict(item) for item in cleanup_plan.get("commands") or []]
    if dry_run:
        return {
            "schema_version": PATCH_FAMILY_EXECUTION_SCHEMA,
            "status": "planned",
            "phase": "cleanup",
            "dry_run": True,
            "commands": commands,
        }

    results: List[Dict[str, Any]] = []
    for command in commands:
        name = str(command.get("name") or "")
        argv = list(command.get("argv") or [])
        if name not in allowed_names or not argv or argv[0] != "git":
            results.append(
                {
                    "status": "blocked",
                    "name": name,
                    "argv": argv,
                    "stderr": "cleanup command is not part of the generated allowlist",
                }
            )
            continue
        executed = _run_argv(argv, timeout_sec=timeout_sec)
        executed["name"] = name
        results.append(executed)

    required_failed = [
        item for item in results
        if item.get("status") != "passed"
        and any(command.get("required") for command in commands if command.get("name") == item.get("name"))
    ]
    return {
        "schema_version": PATCH_FAMILY_EXECUTION_SCHEMA,
        "status": "failed" if required_failed else "passed",
        "phase": "cleanup",
        "dry_run": False,
        "results": results,
    }


def _result_counts(verifier_results: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts = {
        "required": 0,
        "passed_required": 0,
        "failed_required": 0,
        "blocked_required": 0,
        "passed_optional": 0,
        "failed_optional": 0,
    }
    for item in verifier_results or []:
        required = bool(item.get("required", True))
        status = str(item.get("status") or "").lower()
        if required:
            counts["required"] += 1
            if status in {"passed", "skipped"}:
                counts["passed_required"] += 1
            elif status == "blocked":
                counts["blocked_required"] += 1
            else:
                counts["failed_required"] += 1
        elif status in {"passed", "skipped"}:
            counts["passed_optional"] += 1
        elif status:
            counts["failed_optional"] += 1
    return counts


def _ranking_input_index(plan: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not plan:
        return out
    for family in plan.get("families") or []:
        inputs = dict(family.get("ranking_inputs") or {})
        candidate_id = str(inputs.get("candidate_id") or "")
        if candidate_id:
            out[candidate_id] = inputs
    return out


def rank_patch_family_results(
    results: Iterable[Mapping[str, Any]],
    *,
    plan: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Rank candidate result records using verifier outcomes and plan inputs."""

    inputs_by_candidate = _ranking_input_index(plan)
    ranked: List[Dict[str, Any]] = []
    for result in results or []:
        candidate_id = str(result.get("candidate_id") or "")
        ranking_inputs = dict(inputs_by_candidate.get(candidate_id) or result.get("ranking_inputs") or {})
        counts = _result_counts(result.get("verifier_results") or [])
        required = counts["required"] or int(ranking_inputs.get("required_verifier_count") or 0)
        pass_rate = counts["passed_required"] / required if required else 0.0
        risk_score = int(ranking_inputs.get("risk_score") or _risk_score(ranking_inputs.get("risk")))
        touched_path_count = int(ranking_inputs.get("touched_path_count") or 0)
        patch_bytes = int(ranking_inputs.get("patch_bytes") or 0)
        conflict_count = int(result.get("conflict_count") or 0)
        duration_ms = int(result.get("duration_ms") or 0)
        score = (
            pass_rate * 1000
            + counts["passed_optional"] * 25
            - counts["failed_required"] * 500
            - counts["blocked_required"] * 350
            - counts["failed_optional"] * 40
            - conflict_count * 150
            - risk_score * 12
            - touched_path_count * 3
            - min(patch_bytes, 100_000) / 10_000
            - min(duration_ms, 600_000) / 10_000
        )
        ranked.append(
            {
                "candidate_id": candidate_id,
                "score": round(score, 4),
                "status_counts": counts,
                "ranking_inputs": ranking_inputs,
                "result": dict(result),
            }
        )

    ranked.sort(
        key=lambda item: (
            -float(item["score"]),
            int((item.get("ranking_inputs") or {}).get("risk_score") or 0),
            int((item.get("ranking_inputs") or {}).get("touched_path_count") or 0),
            str(item.get("candidate_id") or ""),
        )
    )
    return {
        "schema_version": PATCH_FAMILY_RANKING_SCHEMA,
        "ranked": ranked,
        "winner": ranked[0] if ranked else None,
    }


__all__ = [
    "PATCH_CANDIDATE_SCHEMA",
    "PATCH_FAMILY_EXECUTION_SCHEMA",
    "PATCH_FAMILY_PLAN_SCHEMA",
    "PATCH_FAMILY_RANKING_SCHEMA",
    "PatchCandidate",
    "VerifierCommand",
    "associate_verifiers_with_candidate",
    "build_cleanup_plan",
    "build_patch_family_plan",
    "build_ranking_inputs",
    "build_worktree_creation_plan",
    "execute_cleanup_plan",
    "execute_worktree_creation",
    "normalize_patch_candidate",
    "normalize_verifier_command",
    "rank_patch_family_results",
]
