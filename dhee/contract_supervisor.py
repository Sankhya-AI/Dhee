"""Contract supervisor for deterministic agent action enforcement.

The task compiler says what should be done.  The interpreter says whether a
target checkout can run it.  The supervisor is the runtime gate: it decides
whether a proposed tool action is inside the interpreted contract and records
observation-to-next-action transitions.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from dhee import repo_link
from dhee.runtime_io import append_jsonl_locked, read_jsonl_checked, write_json_atomic
from dhee.task_contracts import (
    ACTION_TYPES,
    _SECRET_PATTERNS,
    _action_operands,
    _is_forbidden_path,
    _path_under_allowed,
    _safe_repo_path,
    _sanitize_obj,
    _stable_hash,
    _tokens,
    interpret_task_contract,
    _load_task_contract,
    _resolve_repo_root,
)


SUPERVISOR_DECISION_SCHEMA = "dhee.contract_supervisor_decision.v1"
OBSERVATION_EVENT_SCHEMA = "dhee.contract_observation_event.v1"
PROOF_BUNDLE_SCHEMA = "dhee.proof_bundle.v1"

_RECOVERY_ACTIONS = {"SEARCH_CODE", "ASK_USER"}
_FORBIDDEN_SUBAGENT_PERMISSIONS = {"write:any", "shell:unsafe", "secrets:read", "network:unbounded"}
_SUCCESS_OUTCOMES = {"pass", "passed", "success", "succeeded", "ok"}
_BLOCKED_OUTCOMES = {"blocked", "denied", "rejected"}
_RUNTIME_ARTIFACT_PREFIXES = (
    ".dhee/context/task_runs/",
    ".dhee/context/task_contracts/",
    ".dhee/context/repo_brain/",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _task_run_dir(repo_root: Path, task_id: str) -> Path:
    return repo_link.repo_context_dir(repo_root) / "task_runs" / str(task_id or "unknown")


def _events_path(repo_root: Path, task_id: str) -> Path:
    return _task_run_dir(repo_root, task_id) / "events.jsonl"


def _load_events(repo_root: Path, task_id: str) -> List[Dict[str, Any]]:
    return list(_load_events_checked(repo_root, task_id).get("records") or [])


def _load_events_checked(repo_root: Path, task_id: str) -> Dict[str, Any]:
    return read_jsonl_checked(_events_path(repo_root, task_id))


def _outcome_is_success(outcome: Any) -> bool:
    return str(outcome or "").strip().lower() in _SUCCESS_OUTCOMES


def _outcome_is_blocked(outcome: Any) -> bool:
    return str(outcome or "").strip().lower() in _BLOCKED_OUTCOMES


def _decision_allows(event: Dict[str, Any]) -> bool:
    decision = event.get("decision") or {}
    return str(decision.get("decision") or "") in {"allow", "needs_input"}


def _action_key(action: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": action.get("type"),
        "operands": _action_operands(action),
    }


def _match_planned_action(planned_actions: Iterable[Dict[str, Any]], proposed_action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action_id = proposed_action.get("action_id")
    if action_id:
        for planned in planned_actions or []:
            if planned.get("action_id") == action_id:
                return planned

    proposed_type = proposed_action.get("type")
    proposed_operands = _action_operands(proposed_action)
    exact: List[Dict[str, Any]] = []
    same_type: List[Dict[str, Any]] = []
    for planned in planned_actions or []:
        if planned.get("type") != proposed_type:
            continue
        same_type.append(planned)
        planned_operands = _action_operands(planned)
        if proposed_operands and all(planned_operands.get(key) == value for key, value in proposed_operands.items() if key != "timeout_sec"):
            exact.append(planned)
    if exact:
        return exact[0]
    if len(same_type) == 1 and proposed_type in {"SUBMIT_PATCH", "WRITE_MEMORY_NOTE", "ASK_USER"}:
        return same_type[0]
    return None


def _event_action(event: Dict[str, Any]) -> Dict[str, Any]:
    action = event.get("action") or {}
    return action if isinstance(action, dict) else {}


def _event_action_id(event: Dict[str, Any], planned_actions: Iterable[Dict[str, Any]]) -> Optional[str]:
    action = _event_action(event)
    if action.get("action_id"):
        return str(action.get("action_id"))
    decision = event.get("decision") or {}
    matched = decision.get("matched_contract_action") or {}
    if matched.get("action_id"):
        return str(matched.get("action_id"))
    planned = _match_planned_action(planned_actions, action)
    if planned and planned.get("action_id"):
        return str(planned.get("action_id"))
    return None


def _observed_action_ids(events: Iterable[Dict[str, Any]], planned_actions: Iterable[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for event in events or []:
        if not _decision_allows(event) or _outcome_is_blocked(event.get("outcome")):
            continue
        action_id = _event_action_id(event, planned_actions)
        if action_id and action_id not in out:
            out.append(action_id)
    return out


def _passed_test_commands(events: Iterable[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for event in events or []:
        action = _event_action(event)
        if action.get("type") != "RUN_TEST":
            continue
        if not _decision_allows(event) or not _outcome_is_success(event.get("outcome")):
            continue
        command = str(action.get("command") or "").strip()
        if command and command not in out:
            out.append(command)
    return out


def _observed_read_paths(events: Iterable[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for event in events or []:
        action = _event_action(event)
        if action.get("type") != "READ_FILE":
            continue
        if not _decision_allows(event) or _outcome_is_blocked(event.get("outcome")):
            continue
        path = str(action.get("path") or "").strip()
        if path and path not in out:
            out.append(path)
    return out


def _accepted_events(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        event
        for event in events or []
        if event.get("accepted") is not False
        and _decision_allows(event)
        and not _outcome_is_blocked(event.get("outcome"))
    ]


def _edit_proof(action: Dict[str, Any]) -> Dict[str, Any]:
    proof = action.get("proof") if isinstance(action.get("proof"), dict) else {}
    return {
        "edit_span": action.get("edit_span") or action.get("span") or proof.get("edit_span") or proof.get("span"),
        "invariant": action.get("invariant") or proof.get("invariant"),
        "related_tests": action.get("related_tests") or action.get("related_test") or proof.get("related_tests") or proof.get("related_test"),
        "rollback_point": action.get("rollback_point") or proof.get("rollback_point"),
    }


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _edit_span_is_valid(span: Any, path: str) -> bool:
    if isinstance(span, dict):
        span_path = str(span.get("path") or path)
        if span_path != path:
            return False
        try:
            start = int(span.get("start_line"))
            end = int(span.get("end_line"))
        except (TypeError, ValueError):
            return False
        return start > 0 and end >= start
    text = str(span or "").strip()
    return bool(text)


def _next_allowed_actions(planned_actions: Iterable[Dict[str, Any]], observed_ids: Iterable[str]) -> List[Dict[str, Any]]:
    observed = set(observed_ids)
    out: List[Dict[str, Any]] = []
    for action in planned_actions or []:
        action_id = str(action.get("action_id") or "")
        if action_id and action_id in observed:
            continue
        missing = [dep for dep in action.get("requires") or [] if dep not in observed]
        if missing:
            continue
        out.append({
            "action_id": action_id,
            "type": action.get("type"),
            "phase": action.get("phase"),
            "target": action.get("path") or action.get("command") or action.get("query") or action.get("summary") or action.get("category"),
            "requires": action.get("requires") or [],
        })
    return out[:8]


def _git_out(repo_root: Path, args: List[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _normalize_status_path(raw: str) -> str:
    value = str(raw or "").strip()
    if " -> " in value:
        value = value.split(" -> ", 1)[1].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except Exception:
            return value.strip('"')
    return value


def _worktree_changed_paths(repo_root: Path) -> List[str]:
    out: List[str] = []
    status = _git_out(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"])
    for line in status.splitlines():
        if not line:
            continue
        path = _normalize_status_path(line[3:] if len(line) > 3 else line)
        if path and path not in out:
            out.append(path)
    return out


def _is_runtime_artifact_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    return any(normalized.startswith(prefix) for prefix in _RUNTIME_ARTIFACT_PREFIXES)


def _code_changed_paths(repo_root: Path) -> List[str]:
    return [path for path in _worktree_changed_paths(repo_root) if not _is_runtime_artifact_path(path)]


def _secret_findings_in_diff(repo_root: Path) -> List[Dict[str, Any]]:
    diff = _git_out(repo_root, ["diff", "--", "."])
    findings: List[Dict[str, Any]] = []
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        if _is_runtime_artifact_path(current_file):
            continue
        for pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append({
                    "path": current_file,
                    "pattern": pattern.pattern[:80],
                    "line_hash": _stable_hash(line, 16),
                })
                break
    return findings[:20]


def _submit_diff_violations(repo_root: Path, contract: Dict[str, Any], proposed_action: Dict[str, Any]) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []
    allowed_paths = contract.get("allowed_write_paths") or []
    forbidden_paths = contract.get("forbidden_paths") or []
    changed_paths = _code_changed_paths(repo_root)
    forbidden_changed = [path for path in changed_paths if _is_forbidden_path(path, forbidden_paths)]
    outside_allowed = [
        path
        for path in changed_paths
        if not _is_forbidden_path(path, forbidden_paths) and not _path_under_allowed(path, allowed_paths)
    ]
    if forbidden_changed:
        violations.append({
            "code": "SUBMIT_CHANGED_FORBIDDEN_PATH",
            "message": "SUBMIT_PATCH cannot proceed while forbidden paths are changed.",
            "paths": forbidden_changed,
        })
    if outside_allowed:
        violations.append({
            "code": "SUBMIT_CHANGED_PATH_OUT_OF_CONTRACT",
            "message": "SUBMIT_PATCH cannot include changed paths outside allowed_write_paths.",
            "paths": outside_allowed,
        })
    secret_findings = _secret_findings_in_diff(repo_root)
    if secret_findings:
        violations.append({
            "code": "SUBMIT_SECRET_PATTERN_IN_DIFF",
            "message": "SUBMIT_PATCH cannot proceed while the diff contains secret-like additions.",
            "findings": secret_findings,
        })
    contamination = contract.get("contamination_status") or {}
    contamination_status = str(contamination.get("status") or "clean")
    if contamination_status not in {"clean", "none"} and not bool(proposed_action.get("contamination_quarantine_ack")):
        violations.append({
            "code": "SUBMIT_CONTAMINATION_NOT_CLEAN",
            "message": "SUBMIT_PATCH requires clean contamination status or explicit quarantine acknowledgement.",
            "status": contamination_status,
            "quarantined_refs": contamination.get("quarantined_refs") or [],
        })
    return violations


def _checkpoint_stage(action: Dict[str, Any], outcome: str) -> Optional[str]:
    action_type = str(action.get("type") or "")
    outcome_text = str(outcome or "").lower()
    if action_type in {"SEARCH_CODE", "READ_FILE", "LSP_SYMBOL"}:
        return "after_localization"
    if action_type == "RUN_TEST" and outcome_text in {"failed", "fail", "timed_out", "error"}:
        return "after_failing_test"
    if action_type == "EDIT_FILE":
        return "before_edit"
    if action_type == "SUBMIT_PATCH":
        return "before_submit"
    return None


def record_replay_checkpoint(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    stage: str,
    *,
    repo: str | os.PathLike[str] | None = None,
    action: Optional[Dict[str, Any]] = None,
    observation: Any = None,
    outcome: str = "observed",
) -> Dict[str, Any]:
    """Create a branchable proof checkpoint for the contract runtime."""

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    contract = compiled.get("contract") or {}
    task_id = str(contract.get("task_id") or "unknown")
    events_checked = _load_events_checked(repo_root, task_id)
    events = list(events_checked.get("records") or [])
    now = _now_iso()
    checkpoint = _sanitize_obj({
        "format": "dhee.replay_checkpoint.v1",
        "checkpoint_id": "chk_" + _stable_hash({
            "task_id": task_id,
            "stage": stage,
            "created_at": now,
            "event_count": len(events),
        }, 18),
        "created_at": now,
        "task_id": task_id,
        "stage": stage,
        "repo": str(repo_root),
        "branch": _git_out(repo_root, ["branch", "--show-current"]),
        "head_commit": _git_out(repo_root, ["rev-parse", "--short", "HEAD"]),
        "status_porcelain": _git_out(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"]),
        "diff_stat": _git_out(repo_root, ["diff", "--stat"]),
        "event_count": len(events),
        "action": action or {},
        "outcome": outcome,
        "observation": observation,
        "rollback_point": _git_out(repo_root, ["rev-parse", "HEAD"]),
        "proof": {
            "contract_hash": _stable_hash(compiled, 20),
            "verification_card": (contract.get("verification_card") or {}).get("schema_version"),
            "contamination_status": (contract.get("contamination_status") or {}).get("status"),
        },
    })
    root = _task_run_dir(repo_root, task_id) / "checkpoints"
    path = root / f"{stage}_{checkpoint['checkpoint_id']}.json"
    write_result = write_json_atomic(path, checkpoint, sanitize=_sanitize_obj)
    if not write_result.get("ok"):
        diagnostic = write_result.get("diagnostic") or {}
        raise RuntimeError(diagnostic.get("message") or f"failed to write replay checkpoint {path}")
    return {
        "format": "dhee_replay_checkpoint_record.v1",
        "checkpoint": checkpoint,
        "paths": {"checkpoint": str(path), "dir": str(root)},
    }


def build_proof_bundle(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    strict: bool = False,
    persist: bool = True,
) -> Dict[str, Any]:
    """Build the auditable proof bundle for a contract run.

    The bundle contains observations and pointers only. It does not expose
    hidden reasoning or raw memory bodies.
    """

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    contract = compiled.get("contract") or {}
    task_id = str(contract.get("task_id") or "unknown")
    events_checked = _load_events_checked(repo_root, task_id)
    events = list(events_checked.get("records") or [])
    accepted_events = _accepted_events(events)
    passed_tests = _passed_test_commands(events)
    required_tests = [str(command) for command in contract.get("must_run") or []]
    missing_tests = [
        command
        for command in required_tests
        if not any(_command_allowed(passed_command, [command]) for passed_command in passed_tests)
    ]
    failed_tests = [
        {
            "command": str((_event_action(event) or {}).get("command") or ""),
            "outcome": event.get("outcome"),
            "event_id": event.get("event_id"),
        }
        for event in events
        if (_event_action(event) or {}).get("type") == "RUN_TEST"
        and not _outcome_is_success(event.get("outcome"))
    ]
    changed_paths = _code_changed_paths(repo_root)
    forbidden_changed = [path for path in changed_paths if _is_forbidden_path(path, contract.get("forbidden_paths") or [])]
    outside_allowed = [
        path
        for path in changed_paths
        if not _is_forbidden_path(path, contract.get("forbidden_paths") or [])
        and not _path_under_allowed(path, contract.get("allowed_write_paths") or [])
    ]
    secret_findings = _secret_findings_in_diff(repo_root)
    contamination = contract.get("contamination_status") or {}
    contamination_clean = str(contamination.get("status") or "clean") in {"clean", "none"}
    verifier_passed = not missing_tests and not forbidden_changed and not outside_allowed and not secret_findings and contamination_clean
    action_trace = []
    for event in events:
        action = _event_action(event)
        decision = event.get("decision") or {}
        matched = decision.get("matched_contract_action") or {}
        action_trace.append({
            "event_id": event.get("event_id"),
            "created_at": event.get("created_at"),
            "action_id": action.get("action_id") or matched.get("action_id"),
            "type": action.get("type"),
            "target": action.get("path") or action.get("command") or action.get("query") or action.get("summary") or action.get("category"),
            "decision": decision.get("decision"),
            "accepted": event.get("accepted"),
            "outcome": event.get("outcome"),
        })
    context_items = [
        {
            "kind": item.get("kind"),
            "title": item.get("title"),
            "evidence_pointer": item.get("evidence_pointer"),
            "why_included": item.get("why_included"),
            "token_cost": item.get("token_cost"),
            "confidence": item.get("confidence"),
            "expected_utility": item.get("expected_utility"),
        }
        for item in (contract.get("compiled_context") or {}).get("items") or []
    ]
    skills_used = sorted({
        str(value)
        for event in events
        for value in [
            (_event_action(event) or {}).get("skill_id"),
            ((event.get("observation") or {}) if isinstance(event.get("observation"), dict) else {}).get("skill_id"),
        ]
        if value
    })
    bundle = _sanitize_obj({
        "schema_version": PROOF_BUNDLE_SCHEMA,
        "generated_at": _now_iso(),
        "contract_id": task_id,
        "contract_hash": _stable_hash(compiled, 20),
        "repo": str(repo_root),
        "branch_state": {
            "branch": _git_out(repo_root, ["branch", "--show-current"]),
            "head_commit": _git_out(repo_root, ["rev-parse", "--short", "HEAD"]),
            "dirty": bool(changed_paths),
        },
        "action_trace": action_trace,
        "files_changed": changed_paths,
        "tests_run": [
            {
                "command": str((_event_action(event) or {}).get("command") or ""),
                "outcome": event.get("outcome"),
                "event_id": event.get("event_id"),
            }
            for event in accepted_events
            if (_event_action(event) or {}).get("type") == "RUN_TEST"
        ],
        "verifier_result": {
            "status": "passed" if verifier_passed else "blocked",
            "required_tests": required_tests,
            "passed_tests": passed_tests,
            "missing_tests": missing_tests,
            "failed_tests": failed_tests,
            "forbidden_changed_paths": forbidden_changed,
            "out_of_contract_changed_paths": outside_allowed,
            "secret_findings": secret_findings,
            "verification_card": contract.get("verification_card") or {},
        },
        "context_used": context_items,
        "memories_used": [
            {
                "kind": pointer.get("kind"),
                "evidence_pointer": pointer.get("evidence_pointer") or pointer.get("ref") or pointer.get("id"),
                "why_included": pointer.get("why_included"),
                "confidence": pointer.get("confidence"),
                "content_hash": pointer.get("content_hash"),
            }
            for pointer in contract.get("memory_pointers") or []
        ],
        "skills_used": skills_used,
        "contamination_status": contamination,
        "policy": {
            "raw_evidence_bodies_excluded": True,
            "hidden_reasoning_excluded": True,
            "strict": bool(strict),
        },
        "runtime_state_diagnostics": events_checked.get("diagnostics") or [],
    })
    paths: Dict[str, str] = {}
    if persist:
        proof_path = _task_run_dir(repo_root, task_id) / "proof_bundle.json"
        write_result = write_json_atomic(proof_path, bundle, sanitize=_sanitize_obj)
        if not write_result.get("ok"):
            diagnostic = write_result.get("diagnostic") or {}
            raise RuntimeError(diagnostic.get("message") or f"failed to write proof bundle {proof_path}")
        paths["proof_bundle"] = str(proof_path)
    return {
        "format": "dhee_contract_proof_bundle.v1",
        "proof_bundle": bundle,
        "paths": paths,
    }


def _goal_token_overlap(goal: str, query: str) -> bool:
    goal_tokens = set(_tokens(goal))
    query_tokens = set(_tokens(query))
    if not goal_tokens or not query_tokens:
        return False
    return bool(goal_tokens & query_tokens)


def _planned_targets(actions: Iterable[Dict[str, Any]], action_type: str, field: str) -> List[str]:
    out: List[str] = []
    for action in actions or []:
        if action.get("type") == action_type and action.get(field):
            value = str(action.get(field))
            if value not in out:
                out.append(value)
    return out


def _command_allowed(command: str, must_run: Iterable[str]) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    for expected in must_run or []:
        expected_text = str(expected or "").strip()
        if text == expected_text or text.startswith(expected_text + " "):
            return True
    return False


def _supervise_by_type(
    *,
    repo_root: Path,
    contract: Dict[str, Any],
    planned_actions: List[Dict[str, Any]],
    proposed_action: Dict[str, Any],
    matched_action: Optional[Dict[str, Any]],
    events: List[Dict[str, Any]],
    interpreted_readiness: str,
) -> List[Dict[str, Any]]:
    violations: List[Dict[str, Any]] = []

    def deny(code: str, message: str, **extra: Any) -> None:
        violations.append({"code": code, "message": message, **extra})

    action_type = str(proposed_action.get("type") or "")
    if action_type not in ACTION_TYPES:
        deny("UNKNOWN_ACTION_TYPE", f"Unknown action type {action_type!r}.")
        return violations

    if interpreted_readiness == "blocked" and action_type not in _RECOVERY_ACTIONS:
        deny("CONTRACT_NOT_READY", "Interpreted contract is blocked; only recovery search or user clarification is allowed.")

    observed_ids = _observed_action_ids(events, planned_actions)
    if matched_action:
        missing = [dep for dep in matched_action.get("requires") or [] if dep not in observed_ids]
        if missing:
            deny(
                "ACTION_DEPENDENCY_UNSATISFIED",
                "Action has hard dependencies that have not been observed yet.",
                action_id=matched_action.get("action_id"),
                missing_action_ids=missing,
            )

    allowed_paths = contract.get("allowed_write_paths") or []
    forbidden_paths = contract.get("forbidden_paths") or []
    relevant_files = set(str(path) for path in contract.get("relevant_files") or [])

    if action_type == "READ_FILE":
        path = str(proposed_action.get("path") or "")
        resolved = _safe_repo_path(repo_root, path)
        if resolved is None:
            deny("UNSAFE_READ_PATH", "READ_FILE path is absolute or escapes the repo.", path=path)
        elif _is_forbidden_path(path, forbidden_paths):
            deny("READ_PATH_FORBIDDEN", "READ_FILE targets a forbidden path.", path=path)
        elif path not in relevant_files and not _path_under_allowed(path, allowed_paths):
            deny("READ_PATH_OUT_OF_CONTRACT", "READ_FILE target is outside relevant_files and allowed_write_paths.", path=path)
        elif not resolved.exists():
            deny("READ_PATH_MISSING", "READ_FILE target does not exist in this checkout.", path=path)

    elif action_type == "SEARCH_CODE":
        query = str(proposed_action.get("query") or "")
        scope = str(proposed_action.get("scope") or ".")
        planned_queries = _planned_targets(planned_actions, "SEARCH_CODE", "query")
        if not query.strip():
            deny("EMPTY_SEARCH_QUERY", "SEARCH_CODE requires a query.")
        elif query not in planned_queries and not _goal_token_overlap(str(contract.get("goal") or ""), query):
            deny("SEARCH_QUERY_OUT_OF_CONTRACT", "SEARCH_CODE query does not overlap the compiled goal.", query=query)
        resolved = repo_root if scope in {"", "."} else _safe_repo_path(repo_root, scope)
        if resolved is None:
            deny("UNSAFE_SEARCH_SCOPE", "SEARCH_CODE scope is absolute or escapes the repo.", scope=scope)
        elif _is_forbidden_path(scope, forbidden_paths):
            deny("SEARCH_SCOPE_FORBIDDEN", "SEARCH_CODE targets a forbidden path.", scope=scope)

    elif action_type == "RUN_TEST":
        command = str(proposed_action.get("command") or "")
        if not _command_allowed(command, contract.get("must_run") or []):
            deny("TEST_COMMAND_OUT_OF_CONTRACT", "RUN_TEST command must match the compiled must_run list.", command=command)

    elif action_type == "EDIT_FILE":
        path = str(proposed_action.get("path") or "")
        resolved = _safe_repo_path(repo_root, path)
        if resolved is None:
            deny("UNSAFE_EDIT_PATH", "EDIT_FILE path is absolute or escapes the repo.", path=path)
        elif _is_forbidden_path(path, forbidden_paths):
            deny("EDIT_PATH_FORBIDDEN", "EDIT_FILE targets a forbidden path.", path=path)
        elif not _path_under_allowed(path, allowed_paths):
            deny("EDIT_PATH_OUTSIDE_ALLOWED", "EDIT_FILE target is outside allowed_write_paths.", path=path)
        if not proposed_action.get("patch"):
            deny("MISSING_EDIT_PATCH", "EDIT_FILE requires a unified diff patch.", path=path)
        if resolved is not None and resolved.exists() and path not in _observed_read_paths(events):
            deny("EDIT_REQUIRES_READ_OBSERVATION", "EDIT_FILE requires a prior observed READ_FILE for the same path.", path=path)
        proof = _edit_proof(proposed_action)
        missing_proof: List[str] = []
        if not str(proof.get("edit_span") or "").strip():
            missing_proof.append("edit_span")
        if not str(proof.get("invariant") or "").strip():
            missing_proof.append("invariant")
        if not _as_list(proof.get("related_tests")):
            missing_proof.append("related_tests")
        if not str(proof.get("rollback_point") or "").strip():
            missing_proof.append("rollback_point")
        if missing_proof:
            deny(
                "EDIT_PROOF_OBLIGATION_MISSING",
                "EDIT_FILE requires edit_span, invariant, related_tests, and rollback_point proof fields.",
                missing=missing_proof,
            )
        if proof.get("edit_span") and not _edit_span_is_valid(proof.get("edit_span"), path):
            deny(
                "EDIT_SPAN_INVALID",
                "EDIT_FILE edit_span must identify the edited file and a valid line range.",
                edit_span=proof.get("edit_span"),
            )
        if proof.get("rollback_point") and not _git_out(repo_root, ["rev-parse", "--verify", str(proof.get("rollback_point"))]):
            deny(
                "EDIT_ROLLBACK_POINT_INVALID",
                "EDIT_FILE rollback_point must resolve to a git object in this checkout.",
                rollback_point=proof.get("rollback_point"),
            )
        related_tests = _as_list(proof.get("related_tests"))
        if related_tests and not all(_command_allowed(test, contract.get("must_run") or []) for test in related_tests):
            deny(
                "EDIT_RELATED_TEST_OUT_OF_CONTRACT",
                "EDIT_FILE related_tests must be selected from the compiled must_run verifier list.",
                related_tests=related_tests,
            )

    elif action_type == "ASK_USER":
        if not str(proposed_action.get("question") or "").strip():
            deny("MISSING_USER_QUESTION", "ASK_USER requires a question.")

    elif action_type == "SPAWN_SUBAGENT":
        permissions = {str(item) for item in proposed_action.get("permissions") or []}
        if not str(proposed_action.get("role") or "").strip() or not str(proposed_action.get("task") or "").strip():
            deny("INVALID_SUBAGENT_REQUEST", "SPAWN_SUBAGENT requires role and task.")
        forbidden = sorted(permissions & _FORBIDDEN_SUBAGENT_PERMISSIONS)
        if forbidden:
            deny("SUBAGENT_PERMISSION_FORBIDDEN", "SPAWN_SUBAGENT requests forbidden permissions.", permissions=forbidden)

    elif action_type == "WRITE_MEMORY_NOTE":
        if not str(proposed_action.get("category") or "").strip() or not str(proposed_action.get("content") or "").strip():
            deny("INVALID_MEMORY_NOTE", "WRITE_MEMORY_NOTE requires category and content.")

    elif action_type == "SUBMIT_PATCH":
        tests = [str(item) for item in proposed_action.get("tests") or []]
        if not str(proposed_action.get("summary") or "").strip():
            deny("MISSING_PATCH_SUMMARY", "SUBMIT_PATCH requires a summary.")
        if tests and not all(_command_allowed(test, contract.get("must_run") or []) for test in tests):
            deny("SUBMIT_TESTS_OUT_OF_CONTRACT", "SUBMIT_PATCH tests must come from the compiled must_run list.", tests=tests)
        passed = _passed_test_commands(events)
        missing_tests = [
            str(command)
            for command in contract.get("must_run") or []
            if not any(_command_allowed(passed_command, [str(command)]) for passed_command in passed)
        ]
        if missing_tests:
            deny("SUBMIT_REQUIRES_PASSING_TESTS", "SUBMIT_PATCH requires every compiled must_run command to be observed as passed.", missing_tests=missing_tests)
        for violation in _submit_diff_violations(repo_root, contract, proposed_action):
            violations.append(violation)

    return violations


def supervise_action(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    proposed_action: Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Decide whether a proposed action is allowed by a compiled contract."""

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    interpretation = interpret_task_contract(compiled, repo=repo_root, strict=strict)
    contract = compiled.get("contract") or {}
    planned_actions = list(compiled.get("actions") or [])
    action = _sanitize_obj(dict(proposed_action or {}))
    task_id = str(contract.get("task_id") or "unknown")
    events_checked = _load_events_checked(repo_root, task_id)
    events = list(events_checked.get("records") or [])
    matched_action = _match_planned_action(planned_actions, action)
    if matched_action and matched_action.get("action_id") and not action.get("action_id"):
        action["action_id"] = matched_action.get("action_id")

    violations = _supervise_by_type(
        repo_root=repo_root,
        contract=contract,
        planned_actions=planned_actions,
        proposed_action=action,
        matched_action=matched_action,
        events=events,
        interpreted_readiness=str(interpretation.get("readiness") or ""),
    )
    action_type = str(action.get("type") or "")
    if violations:
        decision = "deny"
    elif action_type == "ASK_USER" and action.get("blocking"):
        decision = "needs_input"
    else:
        decision = "allow"

    matched = None
    if matched_action:
        matched = {
            "action_id": matched_action.get("action_id"),
            "step": matched_action.get("step"),
            "type": matched_action.get("type"),
            "phase": matched_action.get("phase"),
            "target": matched_action.get("path") or matched_action.get("command") or matched_action.get("query") or matched_action.get("summary") or matched_action.get("category"),
            "reason": matched_action.get("reason"),
            "requires": matched_action.get("requires") or [],
            "soft_requires": matched_action.get("soft_requires") or [],
            "capabilities": matched_action.get("capabilities") or [],
            "effects": matched_action.get("effects") or [],
        }
    observed_ids = _observed_action_ids(events, planned_actions)

    response = {
        "format": SUPERVISOR_DECISION_SCHEMA,
        "decision": decision,
        "task_id": contract.get("task_id"),
        "goal": contract.get("goal"),
        "repo": str(repo_root),
        "interpreted_readiness": interpretation.get("readiness"),
        "proposed_action": action,
        "matched_contract_action": matched,
        "violations": violations,
        "runtime_state": {
            "event_count": len(events),
            "observed_action_ids": observed_ids,
            "passed_tests": _passed_test_commands(events),
            "observed_read_paths": _observed_read_paths(events),
            "next_allowed_actions": _next_allowed_actions(planned_actions, observed_ids),
            "diagnostics": events_checked.get("diagnostics") or [],
        },
        "observation_template": {
            "precondition": action.get("precondition"),
            "execution": action.get("execution"),
            "observation": "Record compact result plus pointer to full output.",
            "postcondition": action.get("postcondition"),
            "memory_update": action.get("memory_update"),
        },
        "policy": {
            "enforced": True,
            "strict": bool(strict),
            "auto_execute": False,
        },
    }
    if action_type == "SUBMIT_PATCH":
        response["proof_bundle_preview"] = build_proof_bundle(
            compiled,
            repo=repo_root,
            strict=strict,
            persist=False,
        )
    return response


def record_observation_transition(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    action: Dict[str, Any],
    observation: str | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    outcome: str = "observed",
    next_action: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Record a compact action observation and optional next-action decision."""

    repo_root = _resolve_repo_root(repo)
    decision = supervise_action(task_contract, action, repo=repo_root, strict=strict)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    contract = compiled.get("contract") or {}
    task_id = str(contract.get("task_id") or decision.get("task_id") or "unknown")
    recorded_action = _sanitize_obj(dict(action or {}))
    matched = decision.get("matched_contract_action") or {}
    if matched.get("action_id") and not recorded_action.get("action_id"):
        recorded_action["action_id"] = matched.get("action_id")
    created_at = _now_iso()

    event = _sanitize_obj({
        "format": OBSERVATION_EVENT_SCHEMA,
        "event_id": "evt_" + _stable_hash({
            "task_id": task_id,
            "action": recorded_action,
            "observation": observation,
            "next_action": next_action,
            "created_at": created_at,
        }, 18),
        "created_at": created_at,
        "task_id": task_id,
        "action": recorded_action,
        "decision": decision,
        "accepted": decision.get("decision") in {"allow", "needs_input"} and not _outcome_is_blocked(outcome),
        "outcome": outcome,
        "observation": observation,
        "next_action": next_action,
    })
    repo_link._ensure_repo_skeleton(repo_root)
    run_dir = _task_run_dir(repo_root, task_id)
    events_path = run_dir / "events.jsonl"
    append_result = append_jsonl_locked(events_path, event, sanitize=_sanitize_obj)
    if not append_result.get("ok"):
        diagnostic = append_result.get("diagnostic") or {}
        raise RuntimeError(diagnostic.get("message") or f"failed to append supervisor event {events_path}")
    checkpoint = None
    stage = _checkpoint_stage(recorded_action, outcome)
    if stage:
        checkpoint = record_replay_checkpoint(
            compiled,
            stage,
            repo=repo_root,
            action=recorded_action,
            observation=observation,
            outcome=outcome,
        )
    proof_bundle = None
    if recorded_action.get("type") == "SUBMIT_PATCH":
        proof_bundle = build_proof_bundle(
            compiled,
            repo=repo_root,
            strict=strict,
            persist=True,
        )
    next_decision = None
    if next_action is not None:
        next_decision = supervise_action(compiled, next_action, repo=repo_root, strict=strict)
        event["next_decision"] = next_decision
        # Keep the JSONL event immutable except for next-decision availability in
        # the returned response; the stored event is the observation record.
    return {
        "format": "dhee_contract_observation_record.v1",
        "event": event,
        "paths": {"events": str(events_path), "dir": str(run_dir)},
        "decision": decision,
        "next_decision": next_decision,
        "checkpoint": checkpoint,
        "proof_bundle": proof_bundle,
    }
