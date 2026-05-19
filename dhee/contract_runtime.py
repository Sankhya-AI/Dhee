"""Active contract runtime for Dhee router tool enforcement.

Task contracts are useful only if the execution boundary respects them.  This
module binds one active contract to a repo and lets router tools ask a simple
question before doing work: is this read/search/test inside the contract?
"""

from __future__ import annotations

import os
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dhee import repo_link
from dhee.runtime_io import append_jsonl_locked, read_json_checked, read_jsonl_checked, write_json_atomic
from dhee.task_contracts import (
    _load_task_contract,
    _resolve_repo_root,
    _sanitize_obj,
    _stable_hash,
    interpret_task_contract,
)


ACTIVE_CONTRACT_SCHEMA = "dhee.active_contract_runtime.v1"
ENFORCEMENT_POLICY_SCHEMA = "dhee.contract_enforcement_policy.v1"
CONTRACT_TOOL_REFUSAL_SCHEMA = "dhee.contract_tool_refusal.v1"
CONTRACT_TOOL_GUARD_SCHEMA = "dhee.contract_tool_guard.v1"
CONTRACT_RUNTIME_DOCTOR_SCHEMA = "dhee.contract_runtime_doctor.v1"
CONTRACT_SUPERVISOR_UNAVAILABLE = "CONTRACT_SUPERVISOR_UNAVAILABLE"

_CONTRACT_REF_KEYS = ("contract_task_id", "task_contract_id", "contract_id", "task_id", "contract_path")
_ENFORCEMENT_MODES = {"off", "warn", "deny"}
_READ_TOOL_NAMES = {"read", "dhee_read", "Read"}
_GREP_TOOL_NAMES = {"grep", "dhee_grep", "Grep"}
_BASH_TOOL_NAMES = {"bash", "dhee_bash", "Bash"}
_EDIT_TOOL_NAMES = {"edit", "write", "multi_edit", "notebook_edit", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _runtime_dir(repo_root: Path) -> Path:
    return repo_link.repo_context_dir(repo_root) / "task_runs"


def _active_path(repo_root: Path) -> Path:
    return _runtime_dir(repo_root) / "active_contract.json"


def _enforcement_path(repo_root: Path) -> Path:
    return _runtime_dir(repo_root) / "enforcement.json"


def _task_runtime_events_path(repo_root: Path, task_id: str) -> Path:
    return _runtime_dir(repo_root) / str(task_id or "unknown") / "runtime_events.jsonl"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    result = write_json_atomic(path, data, sanitize=_sanitize_obj)
    if not result.get("ok"):
        diagnostic = result.get("diagnostic") or {}
        raise RuntimeError(diagnostic.get("message") or f"failed to write {path}")


def _read_json_checked(path: Path, *, expected_schema: str | None = None, quarantine: bool = False) -> Dict[str, Any]:
    return read_json_checked(path, expected_schema=expected_schema, quarantine=quarantine)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    result = _read_json_checked(path)
    data = result.get("data")
    return data if isinstance(data, dict) and result.get("ok") else None


def _append_runtime_event(repo_root: Path, task_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    path = _task_runtime_events_path(repo_root, task_id)
    return append_jsonl_locked(path, event, sanitize=_sanitize_obj)


def _contract_ref_from_args(arguments: Dict[str, Any]) -> Optional[str]:
    for key in _CONTRACT_REF_KEYS:
        value = arguments.get(key)
        if value:
            return str(value)
    return None


def _looks_like_file(path: Path, raw: str) -> bool:
    if path.exists():
        return path.is_file()
    suffix = Path(raw).suffix
    return bool(suffix)


def _candidate_repo_roots(arguments: Dict[str, Any]) -> Iterable[Path]:
    keys = ("repo", "cwd", "file_path", "path")
    seen: set[str] = set()
    for key in keys:
        raw = str(arguments.get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
        if _looks_like_file(p, raw):
            p = p.parent
        try:
            root = _resolve_repo_root(p)
        except Exception:
            continue
        marker = str(root)
        if marker in seen:
            continue
        seen.add(marker)
        yield root
    try:
        root = _resolve_repo_root(os.getcwd())
        if str(root) not in seen:
            yield root
    except Exception:
        return


def _repo_root_for_policy(repo: str | os.PathLike[str] | Path | None) -> Path:
    return _resolve_repo_root(repo or os.getcwd())


def _mode_from_policy_data(data: Dict[str, Any] | None) -> Optional[str]:
    mode = str((data or {}).get("mode") or "").strip().lower()
    return mode if mode in _ENFORCEMENT_MODES else None


def _env_forces_deny() -> bool:
    return _truthy(os.environ.get("DHEE_REQUIRE_ACTIVE_CONTRACT"))


def contract_enforcement_status(*, repo: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    """Return the effective contract enforcement policy for a repo.

    Public installs default to ``off``.  Premium/strict harnesses can set the
    repo policy to ``deny`` or force it process-wide with
    ``DHEE_REQUIRE_ACTIVE_CONTRACT=1``.
    """

    repo_root = _repo_root_for_policy(repo)
    path = _enforcement_path(repo_root)
    checked = _read_json_checked(path, expected_schema=ENFORCEMENT_POLICY_SCHEMA)
    diagnostics = list(checked.get("diagnostics") or [])
    data = checked.get("data") if isinstance(checked.get("data"), dict) else None
    configured_mode = "off"
    policy_corrupt = False
    if checked.get("exists"):
        mode = _mode_from_policy_data(data)
        if mode:
            configured_mode = mode
        else:
            configured_mode = "deny"
            policy_corrupt = True
            diagnostics.append(
                {
                    "code": "ENFORCEMENT_POLICY_INVALID",
                    "message": "Enforcement policy exists but does not contain a valid off/warn/deny mode.",
                    "path": str(path),
                    "observed_mode": (data or {}).get("mode") if isinstance(data, dict) else None,
                }
            )
    forced_by_env = _env_forces_deny()
    effective_mode = "deny" if forced_by_env else configured_mode
    return {
        "format": ENFORCEMENT_POLICY_SCHEMA,
        "repo": str(repo_root),
        "mode": effective_mode,
        "configured_mode": configured_mode,
        "forced_by_env": forced_by_env,
        "policy_corrupt": policy_corrupt,
        "diagnostics": diagnostics,
        "paths": {"policy": str(path)},
    }


def set_contract_enforcement(
    mode: str,
    *,
    repo: str | os.PathLike[str] | None = None,
    agent_id: str | None = None,
    reason: str | None = None,
) -> Dict[str, Any]:
    """Persist the repo's contract enforcement policy."""

    normalized = str(mode or "").strip().lower()
    if normalized not in _ENFORCEMENT_MODES:
        raise ValueError("mode must be one of: off, warn, deny")
    repo_root = _repo_root_for_policy(repo)
    repo_link._ensure_repo_skeleton(repo_root)
    now = _now_iso()
    policy = {
        "format": ENFORCEMENT_POLICY_SCHEMA,
        "schema_version": ENFORCEMENT_POLICY_SCHEMA,
        "mode": normalized,
        "repo": str(repo_root),
        "updated_at": now,
        "updated_by": agent_id or os.environ.get("DHEE_AGENT_ID") or "unknown",
        "reason": reason or "manual",
    }
    _write_json(_enforcement_path(repo_root), policy)
    _append_runtime_event(
        repo_root,
        "enforcement",
        {
            "event": "enforcement_policy_set",
            "created_at": now,
            "mode": normalized,
            "agent_id": policy["updated_by"],
            "reason": policy["reason"],
        },
    )
    return {
        **policy,
        "effective": contract_enforcement_status(repo=repo_root),
        "paths": {"policy": str(_enforcement_path(repo_root))},
    }


def _record_enforcement_warning(
    repo_root: Path,
    *,
    tool_name: str,
    code: str,
    message: str,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> None:
    _append_runtime_event(
        repo_root,
        "enforcement",
        {
            "event": "enforcement_warning",
            "created_at": _now_iso(),
            "tool_name": tool_name,
            "code": code,
            "message": message,
            "diagnostics": diagnostics or [],
        },
    )


def _last_event(path: Path) -> Optional[Dict[str, Any]]:
    checked = read_jsonl_checked(path)
    records = checked.get("records") or []
    return records[-1] if records else None


def _active_corrupt_codes(diagnostics: Iterable[Dict[str, Any]]) -> bool:
    return any(
        str(diag.get("code") or "") in {"RUNTIME_JSON_CORRUPT", "RUNTIME_JSON_NOT_OBJECT", "RUNTIME_SCHEMA_MISMATCH"}
        for diag in diagnostics or []
    )


def _repo_relative(repo_root: Path, path: str, *, cwd: str | os.PathLike[str] | None = None) -> str:
    raw = Path(str(path or "")).expanduser()
    if not raw.is_absolute():
        base = Path(cwd).expanduser() if cwd else repo_root
        if not base.is_absolute():
            base = Path(os.getcwd()) / base
        raw = base / raw
    try:
        resolved = raw.resolve()
        root = repo_root.resolve()
        if os.path.commonpath([str(root), str(resolved)]) == str(root):
            return os.path.relpath(resolved, root).replace(os.sep, "/")
    except (OSError, ValueError):
        pass
    return str(path or "")


def _scope_relative(repo_root: Path, path: str, *, cwd: str | os.PathLike[str] | None = None) -> str:
    rel = _repo_relative(repo_root, path or ".", cwd=cwd)
    return "." if rel in {"", "."} else rel


def _contract_hash(compiled: Dict[str, Any]) -> str:
    return _stable_hash(
        {
            "contract": compiled.get("contract") or {},
            "compiler": compiled.get("compiler") or {},
            "actions": [
                {
                    "action_id": action.get("action_id"),
                    "type": action.get("type"),
                    "operands": action.get("operands") or {},
                    "requires": action.get("requires") or [],
                }
                for action in compiled.get("actions") or []
                if isinstance(action, dict)
            ],
        },
        24,
    )


def activate_contract_runtime(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    strict: bool = False,
    force: bool = False,
    agent_id: str | None = None,
    harness: str | None = None,
) -> Dict[str, Any]:
    """Select one task contract as the repo's active router runtime."""

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    interpretation = interpret_task_contract(compiled, repo=repo_root, strict=strict)
    readiness = str(interpretation.get("readiness") or "")
    if readiness == "blocked" and not force:
        return {
            "format": ACTIVE_CONTRACT_SCHEMA,
            "active": False,
            "status": "rejected",
            "reason": "contract_not_ready",
            "repo": str(repo_root),
            "task_id": (compiled.get("contract") or {}).get("task_id"),
            "interpretation": interpretation,
        }

    contract = compiled.get("contract") or {}
    task_id = str(contract.get("task_id") or "unknown")
    if isinstance(task_contract, dict):
        contract_ref = task_id
    else:
        source = Path(str(task_contract)).expanduser()
        contract_ref = str(source.resolve()) if source.exists() else str(task_contract)
    runtime = {
        "format": ACTIVE_CONTRACT_SCHEMA,
        "schema_version": ACTIVE_CONTRACT_SCHEMA,
        "active": True,
        "status": "active",
        "task_id": task_id,
        "contract_ref": contract_ref,
        "repo": str(repo_root),
        "strict": bool(strict),
        "force": bool(force),
        "contract_hash": _contract_hash(compiled),
        "activated_at": _now_iso(),
        "activated_by": agent_id or os.environ.get("DHEE_AGENT_ID") or "unknown",
        "harness": harness or os.environ.get("DHEE_HARNESS") or os.environ.get("DHEE_AGENT_ID") or "unknown",
        "policy": {
            "enforce_router_tools": True,
            "auto_record_observations": True,
            "auto_execute": False,
            "allowed_router_tools": ["dhee_read", "dhee_grep", "dhee_bash"],
        },
        "interpretation": {
            "readiness": interpretation.get("readiness"),
            "diagnostic_count": len(interpretation.get("diagnostics") or []),
        },
    }
    repo_link._ensure_repo_skeleton(repo_root)
    _write_json(_active_path(repo_root), runtime)
    _append_runtime_event(
        repo_root,
        task_id,
        {
            "event": "activate",
            "created_at": runtime["activated_at"],
            "task_id": task_id,
            "strict": bool(strict),
            "force": bool(force),
            "contract_hash": runtime["contract_hash"],
            "agent_id": runtime["activated_by"],
            "harness": runtime["harness"],
        },
    )
    return {
        **runtime,
        "paths": {
            "active": str(_active_path(repo_root)),
            "events": str(_task_runtime_events_path(repo_root, task_id)),
        },
        "interpretation": interpretation,
    }


def deactivate_contract_runtime(
    *,
    repo: str | os.PathLike[str] | None = None,
    agent_id: str | None = None,
    reason: str = "manual",
) -> Dict[str, Any]:
    """Deactivate the repo's selected contract without deleting history."""

    repo_root = _resolve_repo_root(repo)
    path = _active_path(repo_root)
    checked = _read_json_checked(path, expected_schema=ACTIVE_CONTRACT_SCHEMA, quarantine=True)
    diagnostics = list(checked.get("diagnostics") or [])
    if checked.get("exists") and not checked.get("ok"):
        return {
            "format": ACTIVE_CONTRACT_SCHEMA,
            "active": False,
            "status": "corrupt",
            "error": "ACTIVE_CONTRACT_CORRUPT",
            "repo": str(repo_root),
            "diagnostics": diagnostics,
            "quarantine": checked.get("quarantine"),
            "paths": {"active": str(path)},
        }
    runtime = checked.get("data") if isinstance(checked.get("data"), dict) else None
    if not runtime or not runtime.get("active"):
        return {
            "format": ACTIVE_CONTRACT_SCHEMA,
            "active": False,
            "status": "inactive",
            "repo": str(repo_root),
            "diagnostics": diagnostics,
            "paths": {"active": str(path)},
        }
    task_id = str(runtime.get("task_id") or "unknown")
    now = _now_iso()
    runtime.update({
        "active": False,
        "status": "inactive",
        "deactivated_at": now,
        "deactivated_by": agent_id or os.environ.get("DHEE_AGENT_ID") or "unknown",
        "deactivation_reason": reason,
    })
    _write_json(path, runtime)
    _append_runtime_event(
        repo_root,
        task_id,
        {
            "event": "deactivate",
            "created_at": now,
            "task_id": task_id,
            "reason": reason,
            "agent_id": runtime["deactivated_by"],
        },
    )
    return {
        "format": ACTIVE_CONTRACT_SCHEMA,
        "active": False,
        "status": "inactive",
        "repo": str(repo_root),
        "task_id": task_id,
        "paths": {
            "active": str(path),
            "events": str(_task_runtime_events_path(repo_root, task_id)),
        },
    }


def contract_runtime_status(*, repo: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    repo_root = _resolve_repo_root(repo)
    path = _active_path(repo_root)
    checked = _read_json_checked(path, expected_schema=ACTIVE_CONTRACT_SCHEMA, quarantine=True)
    diagnostics = list(checked.get("diagnostics") or [])
    enforcement = contract_enforcement_status(repo=repo_root)
    if checked.get("exists") and not checked.get("ok"):
        return {
            "format": ACTIVE_CONTRACT_SCHEMA,
            "active": False,
            "status": "corrupt",
            "error": "ACTIVE_CONTRACT_CORRUPT",
            "repo": str(repo_root),
            "diagnostics": diagnostics,
            "enforcement": enforcement,
            "quarantine": checked.get("quarantine"),
            "paths": {"active": str(path)},
        }
    runtime = checked.get("data") if isinstance(checked.get("data"), dict) else None
    if not runtime or not runtime.get("active"):
        return {
            "format": ACTIVE_CONTRACT_SCHEMA,
            "active": False,
            "status": "inactive",
            "repo": str(repo_root),
            "diagnostics": diagnostics,
            "enforcement": enforcement,
            "paths": {"active": str(path)},
        }
    task_id = str(runtime.get("task_id") or "unknown")
    try:
        interpretation = interpret_task_contract(
            str(runtime.get("contract_ref") or task_id),
            repo=repo_root,
            strict=bool(runtime.get("strict")),
        )
    except Exception as exc:
        interpretation = {
            "readiness": "blocked",
            "diagnostics": [
                {
                    "level": "error",
                    "code": "ACTIVE_CONTRACT_LOAD_FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            ],
        }
    return {
        **runtime,
        "repo": str(repo_root),
        "paths": {
            "active": str(path),
            "events": str(_task_runtime_events_path(repo_root, task_id)),
        },
        "diagnostics": diagnostics,
        "enforcement": enforcement,
        "interpretation": interpretation,
    }


def _active_runtime_for_call(arguments: Dict[str, Any]) -> Tuple[Optional[Path], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    contract_ref = _contract_ref_from_args(arguments)
    collected_diagnostics: List[Dict[str, Any]] = []
    for repo_root in _candidate_repo_roots(arguments):
        enforcement = contract_enforcement_status(repo=repo_root)
        if contract_ref and enforcement.get("mode") != "deny":
            return repo_root, {
                "format": ACTIVE_CONTRACT_SCHEMA,
                "active": True,
                "status": "ephemeral",
                "task_id": contract_ref,
                "repo": str(repo_root),
                "strict": bool(arguments.get("contract_strict") or arguments.get("strict") or False),
                "policy": {
                    "enforce_router_tools": True,
                    "auto_record_observations": True,
                    "auto_execute": False,
                },
            }, collected_diagnostics
        checked = _read_json_checked(_active_path(repo_root), expected_schema=ACTIVE_CONTRACT_SCHEMA, quarantine=True)
        diagnostics = [
            diag
            for diag in checked.get("diagnostics") or []
            if diag.get("code") != "RUNTIME_FILE_MISSING"
        ]
        if diagnostics:
            collected_diagnostics.extend(diagnostics)
        if checked.get("exists") and not checked.get("ok"):
            return repo_root, None, collected_diagnostics
        runtime = checked.get("data") if isinstance(checked.get("data"), dict) else None
        if runtime and runtime.get("active"):
            return repo_root, runtime, collected_diagnostics
    return None, None, collected_diagnostics


def _bash_action_from_command(command: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    timeout = arguments.get("timeout_sec", arguments.get("timeout", 120))
    try:
        timeout_sec = int(float(timeout))
    except (TypeError, ValueError):
        timeout_sec = 120
    return {
        "type": "RUN_TEST",
        "command": str(command or "").strip(),
        "timeout_sec": timeout_sec,
        "reason": "Execute a compiled must_run command under the active contract.",
    }


def _router_action(tool_name: str, arguments: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    normalized = str(tool_name or "").strip()
    if normalized in _READ_TOOL_NAMES:
        file_path = str(arguments.get("file_path") or "")
        return {
            "type": "READ_FILE",
            "path": _repo_relative(repo_root, file_path, cwd=arguments.get("cwd")),
            "reason": "Read through dhee_read under the active contract.",
        }
    if normalized in _GREP_TOOL_NAMES:
        path = str(arguments.get("path") or ".")
        return {
            "type": "SEARCH_CODE",
            "query": str(arguments.get("pattern") or arguments.get("query") or ""),
            "scope": _scope_relative(repo_root, path),
            "reason": "Search through dhee_grep under the active contract.",
        }
    if normalized in _BASH_TOOL_NAMES:
        return _bash_action_from_command(str(arguments.get("command") or ""), arguments)
    if normalized in _EDIT_TOOL_NAMES:
        path = str(
            arguments.get("file_path")
            or arguments.get("path")
            or arguments.get("notebook_path")
            or ""
        )
        patch_payload = {
            "old_string": arguments.get("old_string"),
            "new_string": arguments.get("new_string"),
            "edits": arguments.get("edits"),
            "content_hash": _stable_hash(arguments.get("content") or arguments.get("new_string") or arguments.get("edits") or "", 12),
        }
        proof = arguments.get("proof") if isinstance(arguments.get("proof"), dict) else arguments.get("dhee_proof")
        return {
            "type": "EDIT_FILE",
            "path": _repo_relative(repo_root, path, cwd=arguments.get("cwd")),
            "patch": arguments.get("patch") or f"native_edit:{_stable_hash(patch_payload, 16)}",
            "proof": proof if isinstance(proof, dict) else {},
            "reason": "Native edit tool call under the active contract.",
        }
    return {"type": str(tool_name or ""), "reason": "Unknown router tool."}


def _contract_ref_for_runtime(runtime: Dict[str, Any]) -> str:
    return str(runtime.get("contract_ref") or runtime.get("contract_path") or runtime.get("task_id") or runtime.get("contract_id") or "")


def guard_router_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Return allow/deny for a router call under the active contract, if any."""

    repo_root, runtime, runtime_diagnostics = _active_runtime_for_call(arguments)
    if repo_root is None:
        try:
            repo_root = _repo_root_for_policy(arguments.get("repo") or arguments.get("cwd") or os.getcwd())
        except Exception:
            repo_root = None
    enforcement = contract_enforcement_status(repo=repo_root) if repo_root else {
        "mode": "deny" if _env_forces_deny() else "off",
        "configured_mode": "off",
        "forced_by_env": _env_forces_deny(),
        "diagnostics": [],
    }
    mode = str(enforcement.get("mode") or "off")
    diagnostics = [*runtime_diagnostics, *(enforcement.get("diagnostics") or [])]
    if not repo_root or not runtime:
        required = _truthy(arguments.get("require_active_contract")) or mode == "deny"
        corrupt_active = _active_corrupt_codes(runtime_diagnostics)
        error = None
        if corrupt_active:
            error = "ACTIVE_CONTRACT_CORRUPT"
        elif required:
            error = "ACTIVE_CONTRACT_REQUIRED"
        warning = ""
        if not required and mode == "warn":
            warning = "No active task contract is bound to this repo; warn mode allowed the tool call."
            if repo_root:
                _record_enforcement_warning(
                    repo_root,
                    tool_name=tool_name,
                    code="ACTIVE_CONTRACT_MISSING_WARN",
                    message=warning,
                    diagnostics=diagnostics,
                )
        return {
            "format": CONTRACT_TOOL_GUARD_SCHEMA,
            "active": False,
            "allowed": not required and not (corrupt_active and mode == "deny"),
            "tool_name": tool_name,
            "repo": str(repo_root) if repo_root else None,
            "error": error if required or corrupt_active else None,
            "message": (
                "Active task contract runtime is corrupt and was quarantined."
                if corrupt_active
                else ("No active task contract is bound to this repo." if required else warning)
            ),
            "diagnostics": diagnostics,
            "enforcement": enforcement,
        }
    action = _router_action(tool_name, arguments, repo_root)
    task_ref = _contract_ref_for_runtime(runtime)
    if not task_ref:
        return {
            "format": CONTRACT_TOOL_GUARD_SCHEMA,
            "active": True,
            "allowed": False,
            "repo": str(repo_root),
            "tool_name": tool_name,
            "proposed_action": action,
            "error": "active_runtime_missing_task_ref",
            "runtime": runtime,
            "diagnostics": diagnostics,
            "enforcement": enforcement,
        }
    try:
        from dhee.contract_supervisor import supervise_action

        decision = supervise_action(
            task_ref,
            action,
            repo=repo_root,
            strict=bool(runtime.get("strict")),
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        diagnostics.append({
            "code": CONTRACT_SUPERVISOR_UNAVAILABLE,
            "message": message,
            "tool_name": tool_name,
            "repo": str(repo_root),
        })
        if mode == "deny":
            return {
                "format": CONTRACT_TOOL_GUARD_SCHEMA,
                "active": True,
                "allowed": False,
                "repo": str(repo_root),
                "task_id": str(runtime.get("task_id") or task_ref),
                "tool_name": tool_name,
                "proposed_action": action,
                "error": CONTRACT_SUPERVISOR_UNAVAILABLE,
                "message": "Contract supervisor could not load or execute; deny mode blocks the tool call.",
                "diagnostics": diagnostics,
                "enforcement": enforcement,
                "runtime": {
                    "status": runtime.get("status"),
                    "task_id": runtime.get("task_id"),
                    "strict": bool(runtime.get("strict")),
                    "contract_hash": runtime.get("contract_hash"),
                },
            }
        if mode == "warn":
            warning = "Contract supervisor could not load or execute; warn mode allowed the tool call."
            _record_enforcement_warning(
                repo_root,
                tool_name=tool_name,
                code=CONTRACT_SUPERVISOR_UNAVAILABLE,
                message=warning,
                diagnostics=diagnostics,
            )
            return {
                "format": CONTRACT_TOOL_GUARD_SCHEMA,
                "active": True,
                "allowed": True,
                "repo": str(repo_root),
                "task_id": str(runtime.get("task_id") or task_ref),
                "tool_name": tool_name,
                "proposed_action": action,
                "warning": warning,
                "diagnostics": diagnostics,
                "enforcement": enforcement,
                "runtime": {
                    "status": runtime.get("status"),
                    "task_id": runtime.get("task_id"),
                    "strict": bool(runtime.get("strict")),
                    "contract_hash": runtime.get("contract_hash"),
                },
            }
        return {
            "format": CONTRACT_TOOL_GUARD_SCHEMA,
            "active": True,
            "allowed": True,
            "repo": str(repo_root),
            "task_id": str(runtime.get("task_id") or task_ref),
            "tool_name": tool_name,
            "proposed_action": action,
            "warning": "Contract supervisor unavailable; enforcement mode off preserved compatibility behavior.",
            "diagnostics": diagnostics,
            "enforcement": enforcement,
            "runtime": {
                "status": runtime.get("status"),
                "task_id": runtime.get("task_id"),
                "strict": bool(runtime.get("strict")),
                "contract_hash": runtime.get("contract_hash"),
            },
        }
    allowed = decision.get("decision") == "allow"
    return {
        "format": CONTRACT_TOOL_GUARD_SCHEMA,
        "active": True,
        "allowed": allowed,
        "repo": str(repo_root),
        "task_id": decision.get("task_id") or task_ref,
        "tool_name": tool_name,
        "proposed_action": action,
        "decision": decision,
        "diagnostics": diagnostics,
        "enforcement": enforcement,
        "runtime": {
            "status": runtime.get("status"),
            "task_id": runtime.get("task_id"),
            "strict": bool(runtime.get("strict")),
            "contract_hash": runtime.get("contract_hash"),
        },
    }


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def router_refusal(guard: Dict[str, Any]) -> Dict[str, Any]:
    decision = guard.get("decision") or {}
    violations = decision.get("violations") or []
    codes = [str(item.get("code")) for item in violations if isinstance(item, dict) and item.get("code")]
    if guard.get("error") and not codes:
        codes = [str(guard.get("error"))]
    return {
        "format": CONTRACT_TOOL_REFUSAL_SCHEMA,
        "error": "CONTRACT_TOOL_CALL_DENIED",
        "message": guard.get("message") or "Active task contract refused this router tool call.",
        "will_execute": False,
        "tool_name": guard.get("tool_name"),
        "repo": guard.get("repo"),
        "task_id": guard.get("task_id"),
        "proposed_action": guard.get("proposed_action"),
        "decision": decision,
        "violation_codes": codes,
        "runtime": guard.get("runtime"),
        "diagnostics": guard.get("diagnostics") or [],
        "enforcement": guard.get("enforcement") or {},
    }


def router_result_runtime(guard: Dict[str, Any], observation: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Compact metadata attached to allowed router tool results."""

    if not guard.get("active"):
        return None
    decision = guard.get("decision") or {}
    matched = decision.get("matched_contract_action") or {}
    out = {
        "format": "dhee.contract_router_runtime.v1",
        "task_id": guard.get("task_id"),
        "decision": decision.get("decision"),
        "action_id": (guard.get("proposed_action") or {}).get("action_id") or matched.get("action_id"),
        "action_type": (guard.get("proposed_action") or {}).get("type"),
        "matched_action": {
            "action_id": matched.get("action_id"),
            "type": matched.get("type"),
            "phase": matched.get("phase"),
            "target": matched.get("target"),
        } if matched else None,
        "observation": observation,
    }
    return out


def _observation_for_result(tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
    observation = {
        "tool": tool_name,
        "ptr": result.get("ptr"),
    }
    for key in (
        "line_count",
        "char_count",
        "match_count",
        "file_count",
        "total_bytes",
        "exit_code",
        "duration_ms",
        "class",
        "stdout_bytes",
        "stderr_bytes",
        "timed_out",
        "inlined",
    ):
        if key in result:
            observation[key] = result.get(key)
    return observation


def _outcome_for_result(tool_name: str, result: Dict[str, Any]) -> str:
    if result.get("error"):
        return "failed"
    if str(tool_name) in _BASH_TOOL_NAMES:
        if result.get("timed_out"):
            return "timed_out"
        try:
            exit_code = int(result.get("exit_code"))
        except (TypeError, ValueError):
            exit_code = 1
        return "passed" if exit_code == 0 else "failed"
    return "observed"


def record_router_observation(guard: Dict[str, Any], result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Persist successful router tool observations for the active interpreter."""

    if not guard.get("active") or not guard.get("allowed") or result.get("error"):
        return None
    if not guard.get("task_id") or not guard.get("repo"):
        return None
    try:
        from dhee.contract_supervisor import record_observation_transition

        record = record_observation_transition(
            str(guard["task_id"]),
            dict(guard.get("proposed_action") or {}),
            _observation_for_result(str(guard.get("tool_name") or ""), result),
            repo=str(guard["repo"]),
            outcome=_outcome_for_result(str(guard.get("tool_name") or ""), result),
            strict=bool((guard.get("runtime") or {}).get("strict")),
        )
    except Exception as exc:
        return {
            "format": "dhee.contract_router_observation_error.v1",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "format": "dhee.contract_router_observation.v1",
        "event_id": (record.get("event") or {}).get("event_id"),
        "outcome": (record.get("event") or {}).get("outcome"),
        "events_path": (record.get("paths") or {}).get("events"),
    }


def _path_writable(path: Path) -> Dict[str, Any]:
    existing = path if path.exists() else path.parent
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    writable = os.access(str(existing), os.W_OK) if existing.exists() else False
    return {
        "path": str(path),
        "exists": path.exists(),
        "nearest_existing_parent": str(existing),
        "writable": bool(writable),
    }


def contract_runtime_doctor(*, repo: str | os.PathLike[str] | None = None) -> Dict[str, Any]:
    """Report whether the contract runtime is actually protecting this repo."""

    repo_root = _repo_root_for_policy(repo)
    task_runs = _runtime_dir(repo_root)
    active_path = _active_path(repo_root)
    enforcement_path = _enforcement_path(repo_root)
    enforcement = contract_enforcement_status(repo=repo_root)
    active = contract_runtime_status(repo=repo_root)
    corrupt_files: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    for source in (active, enforcement):
        for diag in source.get("diagnostics") or []:
            diagnostics.append(diag)
            if str(diag.get("code") or "") in {
                "RUNTIME_JSON_CORRUPT",
                "RUNTIME_JSON_NOT_OBJECT",
                "RUNTIME_JSONL_LINE_CORRUPT",
                "RUNTIME_JSONL_LINE_NOT_OBJECT",
                "ENFORCEMENT_POLICY_INVALID",
                "RUNTIME_SCHEMA_MISMATCH",
            }:
                corrupt_files.append({
                    "path": diag.get("path"),
                    "code": diag.get("code"),
                    "message": diag.get("message"),
                })

    router_health: Dict[str, Any]
    try:
        from dhee.router import install as router_install

        state = router_install.status()
        router_health = {
            "available": True,
            "enabled": bool(state.enabled),
            "managed": bool(state.managed),
            "env_flag": bool(state.env_flag),
            "allowed_tools": list(state.allowed_tools or []),
            "settings_path": str(state.settings_path),
        }
    except Exception as exc:
        router_health = {
            "available": False,
            "enabled": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    task_id = str(active.get("task_id") or "")
    last_decision = None
    if task_id:
        supervisor_last = _last_event(task_runs / task_id / "events.jsonl")
        runtime_last = _last_event(_task_runtime_events_path(repo_root, task_id))
        candidates = [item for item in [supervisor_last, runtime_last] if isinstance(item, dict)]
        candidates.sort(key=lambda item: str(item.get("created_at") or ""))
        last_decision = candidates[-1] if candidates else None

    bypass_risks: List[str] = []
    mode = str(enforcement.get("mode") or "off")
    if mode == "off":
        bypass_risks.append("enforcement_off")
    if mode == "warn":
        bypass_risks.append("warn_mode_allows_actions")
    if not active.get("active"):
        bypass_risks.append("no_active_contract")
    if not router_health.get("enabled"):
        bypass_risks.append("native_hook_or_router_not_enabled")
    if corrupt_files:
        bypass_risks.append("corrupt_runtime_state")
    if not all(item.get("writable") for item in [
        _path_writable(task_runs),
        _path_writable(active_path),
        _path_writable(enforcement_path),
    ]):
        bypass_risks.append("runtime_path_not_writable")

    if corrupt_files or mode == "off":
        protection = "unprotected"
    elif mode == "deny" and active.get("active") and router_health.get("enabled"):
        protection = "protected"
    else:
        protection = "partially_protected"

    writable_paths = {
        "task_runs": _path_writable(task_runs),
        "active_contract": _path_writable(active_path),
        "enforcement_policy": _path_writable(enforcement_path),
    }
    return {
        "format": CONTRACT_RUNTIME_DOCTOR_SCHEMA,
        "repo": str(repo_root),
        "status": protection,
        "protected": protection == "protected",
        "active_contract": {
            "active": bool(active.get("active")),
            "status": active.get("status"),
            "task_id": active.get("task_id"),
            "path": (active.get("paths") or {}).get("active"),
            "readiness": (active.get("interpretation") or {}).get("readiness"),
        },
        "enforcement": enforcement,
        "hook_router_health": router_health,
        "writable_runtime_paths": writable_paths,
        "corrupt_files": corrupt_files,
        "last_decision": last_decision,
        "bypass_risks": bypass_risks,
        "diagnostics": diagnostics,
    }


def command_preview(command: str) -> Dict[str, Any]:
    """Small helper for user-facing diagnostics around bash denials."""

    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    return {
        "argv0": argv[0] if argv else "",
        "is_test_like": bool(argv and (argv[0] in {"pytest", "tox", "nox", "npm", "pnpm", "uv", "python", "python3"})),
    }
