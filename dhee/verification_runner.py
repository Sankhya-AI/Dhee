"""Executable verification runner for Dhee task contracts.

The task compiler produces a verification card; this module turns that card
into bounded local execution with an auditable result. It deliberately avoids a
shell, records supervised observations for contract-required tests, and writes a
compact run artifact under the task run directory.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee.contract_supervisor import (
    _command_allowed,
    _is_forbidden_path,
    _load_task_contract,
    _now_iso,
    _resolve_repo_root,
    _sanitize_obj,
    _secret_findings_in_diff,
    _stable_hash,
    _task_run_dir,
    build_proof_bundle,
    record_observation_transition,
)
from dhee.runtime_io import write_json_atomic


VERIFICATION_RUN_SCHEMA = "dhee.verification_run.v1"
MAX_CAPTURE_CHARS = 8_000
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_MAX_COMMANDS = 24
_SHELL_META_PATTERN = re.compile(r"(?<!\\)(?:[;&|<>`]|[$][(])")
_ALLOWED_DIRECT_COMMANDS = {
    "pytest",
    "npm",
    "pnpm",
    "yarn",
    "ruff",
    "mypy",
}
_ALLOWED_PYTHON_MODULES = {
    "compileall",
    "mypy",
    "py_compile",
    "pytest",
    "ruff",
}
_RUN_WRAPPERS = {
    "uv",
    "poetry",
    "pipenv",
}


def _tail(value: str, limit: int = MAX_CAPTURE_CHARS) -> str:
    value = str(value or "")
    if len(value) <= limit:
        return value
    return value[-limit:]


def _strip_run_wrapper(parts: Sequence[str]) -> Sequence[str]:
    if len(parts) >= 3 and Path(parts[0]).name in _RUN_WRAPPERS and parts[1] == "run":
        return parts[2:]
    return parts


def _safe_command_parts(command: str) -> Tuple[Optional[List[str]], Optional[str]]:
    command = str(command or "").strip()
    if not command:
        return None, "empty command"
    if _SHELL_META_PATTERN.search(command):
        return None, "shell metacharacters are not allowed in verifier commands"
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return None, f"could not parse command: {exc}"
    if not parts:
        return None, "empty command"

    executable_parts = list(_strip_run_wrapper(parts))
    if not executable_parts:
        return None, "empty command after run wrapper"
    executable = Path(executable_parts[0]).name
    if executable in {"pytest", "ruff", "mypy"} and shutil.which(executable) is None:
        return [sys.executable, "-m", executable, *executable_parts[1:]], None
    if executable in _ALLOWED_DIRECT_COMMANDS:
        return parts, None
    if executable in {"python", "python3", Path(sys.executable).name}:
        if "-m" in executable_parts:
            idx = executable_parts.index("-m")
            module = executable_parts[idx + 1] if idx + 1 < len(executable_parts) else ""
            if module in _ALLOWED_PYTHON_MODULES:
                if list(executable_parts) == list(parts):
                    return [sys.executable, *list(executable_parts[1:])], None
                return parts, None
        return None, "python verifier commands must use an allowed -m module"
    return None, f"command executable {executable!r} is not allowed by verifier runner"


def _verification_card(contract: Dict[str, Any]) -> Dict[str, Any]:
    return dict(contract.get("verification_card") or {})


def _add_command(
    plan: List[Dict[str, Any]],
    seen: set[str],
    *,
    command: str,
    kind: str,
    required: bool,
    source: str,
) -> None:
    command = str(command or "").strip()
    if not command or command in seen:
        return
    seen.add(command)
    plan.append(
        {
            "command_id": "cmd_" + _stable_hash({"command": command, "kind": kind, "source": source}, 12),
            "kind": kind,
            "command": command,
            "required": bool(required),
            "source": source,
        }
    )


def build_verification_plan(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    include_pass_to_pass: bool = True,
    include_static: bool = True,
    include_security: bool = True,
    max_commands: int = DEFAULT_MAX_COMMANDS,
) -> Dict[str, Any]:
    """Return the executable verifier plan without running commands."""

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    contract = compiled.get("contract") or {}
    card = _verification_card(contract)
    seen: set[str] = set()
    plan: List[Dict[str, Any]] = []
    for command in card.get("fail_to_pass_tests") or contract.get("must_run") or []:
        _add_command(plan, seen, command=command, kind="fail_to_pass", required=True, source="verification_card.fail_to_pass_tests")
    if include_pass_to_pass:
        for command in card.get("pass_to_pass_tests") or []:
            _add_command(plan, seen, command=command, kind="pass_to_pass", required=True, source="verification_card.pass_to_pass_tests")
    if include_static:
        for command in card.get("import_smoke_tests") or []:
            _add_command(plan, seen, command=command, kind="import_smoke", required=True, source="verification_card.import_smoke_tests")
        for command in card.get("static_checks") or []:
            _add_command(plan, seen, command=command, kind="static_check", required=True, source="verification_card.static_checks")
    if include_security:
        for check in card.get("security_checks") or []:
            _add_command(
                plan,
                seen,
                command=f"builtin:{check}",
                kind="security_check",
                required=True,
                source="verification_card.security_checks",
            )
    return {
        "schema_version": "dhee.verification_plan.v1",
        "task_id": contract.get("task_id"),
        "repo": str(repo_root),
        "commands": plan[: max(1, int(max_commands or DEFAULT_MAX_COMMANDS))],
        "truncated": len(plan) > max(1, int(max_commands or DEFAULT_MAX_COMMANDS)),
        "verification_card": card,
    }


def _git_changed_paths(repo_root: Path) -> List[str]:
    changed: List[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        normalized = path.strip().replace(os.sep, "/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            changed.append(normalized)

    for args in (["git", "diff", "--name-only"], ["git", "diff", "--cached", "--name-only"]):
        proc = subprocess.run(
            args,
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=20,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                add(line)

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=20,
    )
    if status.returncode == 0:
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            add(line[3:] if line.startswith("?? ") else line[3:])
    return changed


def _run_security_check(repo_root: Path, contract: Dict[str, Any], check: str) -> Dict[str, Any]:
    lowered = str(check or "").lower()
    started = time.monotonic()
    if "forbidden path" in lowered:
        changed = _git_changed_paths(repo_root)
        forbidden = [
            path
            for path in changed
            if _is_forbidden_path(path, contract.get("forbidden_paths") or [])
        ]
        status = "passed" if not forbidden else "failed"
        observation = {"changed_paths": changed[:80], "forbidden_changed_paths": forbidden}
    elif "secret" in lowered:
        findings = _secret_findings_in_diff(repo_root)
        status = "passed" if not findings else "failed"
        observation = {"secret_findings": findings}
    elif "contamination" in lowered:
        contamination = contract.get("contamination_status") or {}
        clean = str(contamination.get("status") or "clean") in {"clean", "none"}
        status = "passed" if clean else "failed"
        observation = {"contamination_status": contamination}
    elif "risky files" in lowered:
        status = "passed"
        observation = {"note": "manual risky-file review remains a submit-time proof obligation"}
    else:
        status = "skipped"
        observation = {"reason": "no builtin verifier exists for this textual check"}
    return {
        "status": status,
        "exit_code": 0 if status in {"passed", "skipped"} else 1,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "stdout_tail": "",
        "stderr_tail": "",
        "observation": observation,
    }


def _execute_command(repo_root: Path, command: str, timeout_sec: int) -> Dict[str, Any]:
    started = time.monotonic()
    parts, reason = _safe_command_parts(command)
    if reason:
        return {
            "status": "blocked",
            "exit_code": None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout_tail": "",
            "stderr_tail": reason,
            "observation": {"blocked_reason": reason},
        }
    try:
        proc = subprocess.run(
            list(parts or []),
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=max(1, int(timeout_sec or DEFAULT_TIMEOUT_SEC)),
        )
        status = "passed" if proc.returncode == 0 else "failed"
        return {
            "status": status,
            "exit_code": proc.returncode,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout_tail": _tail(proc.stdout),
            "stderr_tail": _tail(proc.stderr),
            "observation": {
                "exit_code": proc.returncode,
                "stdout_tail": _tail(proc.stdout, 2_000),
                "stderr_tail": _tail(proc.stderr, 2_000),
            },
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "exit_code": None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout_tail": _tail(exc.stdout or ""),
            "stderr_tail": _tail(exc.stderr or ""),
            "observation": {"timeout_sec": timeout_sec},
        }
    except OSError as exc:
        return {
            "status": "blocked",
            "exit_code": None,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "observation": {"blocked_reason": str(exc)},
        }


def _record_required_test_observation(
    compiled: Dict[str, Any],
    repo_root: Path,
    contract: Dict[str, Any],
    command: str,
    result: Dict[str, Any],
    run_id: str,
    strict: bool,
) -> Optional[Dict[str, Any]]:
    if not _command_allowed(command, contract.get("must_run") or []):
        return None
    outcome = "passed" if result.get("status") == "passed" else str(result.get("status") or "failed")
    observation = {
        "schema_version": "dhee.verifier_observation.v1",
        "verification_run_id": run_id,
        "command": command,
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "stdout_tail": result.get("stdout_tail"),
        "stderr_tail": result.get("stderr_tail"),
    }
    recorded = record_observation_transition(
        compiled,
        {"type": "RUN_TEST", "command": command, "timeout_sec": DEFAULT_TIMEOUT_SEC},
        observation,
        repo=repo_root,
        outcome=outcome,
        strict=strict,
    )
    return {
        "event_id": (recorded.get("event") or {}).get("event_id"),
        "accepted": (recorded.get("event") or {}).get("accepted"),
        "decision": (recorded.get("decision") or {}).get("decision"),
        "events_path": (recorded.get("paths") or {}).get("events"),
    }


def _overall_status(results: Sequence[Dict[str, Any]]) -> str:
    required = [item for item in results if item.get("required")]
    if any(item.get("status") == "blocked" for item in required):
        return "blocked"
    if any(item.get("status") in {"failed", "timeout"} for item in required):
        return "failed"
    if required and all(item.get("status") in {"passed", "skipped"} for item in required):
        return "passed"
    return "partial"


def run_verification(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    max_commands: int = DEFAULT_MAX_COMMANDS,
    include_pass_to_pass: bool = True,
    include_static: bool = True,
    include_security: bool = True,
    persist: bool = True,
    strict: bool = False,
) -> Dict[str, Any]:
    """Execute a task contract verification card and persist an auditable run."""

    from dhee.verifier_engine import run_verification_compat

    return run_verification_compat(
        task_contract,
        repo=repo,
        timeout_sec=timeout_sec,
        max_commands=max_commands,
        include_pass_to_pass=include_pass_to_pass,
        include_static=include_static,
        include_security=include_security,
        persist=persist,
        strict=strict,
    )

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    contract = compiled.get("contract") or {}
    task_id = str(contract.get("task_id") or "unknown")
    created_at = _now_iso()
    run_id = "ver_" + _stable_hash({"task_id": task_id, "created_at": created_at}, 18)
    plan = build_verification_plan(
        compiled,
        repo=repo_root,
        include_pass_to_pass=include_pass_to_pass,
        include_static=include_static,
        include_security=include_security,
        max_commands=max_commands,
    )
    results: List[Dict[str, Any]] = []
    for item in plan.get("commands") or []:
        command = str(item.get("command") or "")
        if command.startswith("builtin:"):
            executed = _run_security_check(repo_root, contract, command.split(":", 1)[1])
        else:
            executed = _execute_command(repo_root, command, timeout_sec)
        result = {
            **item,
            **executed,
            "recorded_observation": None,
        }
        if item.get("kind") == "fail_to_pass" and executed.get("status") in {"passed", "failed", "timeout"}:
            result["recorded_observation"] = _record_required_test_observation(
                compiled,
                repo_root,
                contract,
                command,
                executed,
                run_id,
                strict,
            )
        results.append(_sanitize_obj(result))

    status = _overall_status(results)
    proof = build_proof_bundle(compiled, repo=repo_root, strict=strict, persist=persist)
    summary = {
        "required_count": sum(1 for item in results if item.get("required")),
        "passed_required_count": sum(1 for item in results if item.get("required") and item.get("status") in {"passed", "skipped"}),
        "failed_required_commands": [
            item.get("command")
            for item in results
            if item.get("required") and item.get("status") in {"failed", "timeout"}
        ],
        "blocked_required_commands": [
            {"command": item.get("command"), "reason": item.get("stderr_tail")}
            for item in results
            if item.get("required") and item.get("status") == "blocked"
        ],
        "fail_to_pass_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in results
            if item.get("kind") == "fail_to_pass"
        ],
        "pass_to_pass_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in results
            if item.get("kind") == "pass_to_pass"
        ],
        "static_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in results
            if item.get("kind") in {"import_smoke", "static_check"}
        ],
        "security_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in results
            if item.get("kind") == "security_check"
        ],
    }
    run = _sanitize_obj(
        {
            "schema_version": VERIFICATION_RUN_SCHEMA,
            "run_id": run_id,
            "created_at": created_at,
            "task_id": task_id,
            "repo": str(repo_root),
            "status": status,
            "plan": plan,
            "results": results,
            "summary": summary,
            "proof_bundle": proof.get("proof_bundle") or {},
            "policy": {
                "shell": False,
                "safe_command_allowlist": sorted(_ALLOWED_DIRECT_COMMANDS),
                "safe_python_modules": sorted(_ALLOWED_PYTHON_MODULES),
                "timeout_sec": int(timeout_sec or DEFAULT_TIMEOUT_SEC),
                "max_commands": int(max_commands or DEFAULT_MAX_COMMANDS),
                "strict": bool(strict),
            },
        }
    )
    paths: Dict[str, str] = dict(proof.get("paths") or {})
    if persist:
        run_dir = _task_run_dir(repo_root, task_id) / "verification_runs"
        run_path = run_dir / f"{run_id}.json"
        write_result = write_json_atomic(run_path, run, sanitize=_sanitize_obj)
        if not write_result.get("ok"):
            diagnostic = write_result.get("diagnostic") or {}
            raise RuntimeError(diagnostic.get("message") or f"failed to write verification run {run_path}")
        paths["verification_run"] = str(run_path)
    return {
        "format": "dhee_verification_run.v1",
        "verification_run": run,
        "paths": paths,
    }
