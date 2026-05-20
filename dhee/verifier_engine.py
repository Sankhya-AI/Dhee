"""Executable verifier gate built on top of the safe verification runner.

The runner owns bounded command execution.  This module owns the verifier gate:
command expansion, baseline transition evaluation, flaky retries, result
aggregation, and durable evidence suitable for Chotu/Dhee runtime decisions.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee.contract_supervisor import (
    _load_task_contract,
    _now_iso,
    _resolve_repo_root,
    _sanitize_obj,
    _stable_hash,
    _task_run_dir,
    build_proof_bundle,
)
from dhee.runtime_io import write_json_atomic
from dhee.verification_runner import (
    DEFAULT_MAX_COMMANDS,
    DEFAULT_TIMEOUT_SEC,
    VERIFICATION_RUN_SCHEMA,
    build_verification_plan as build_runner_verification_plan,
    _execute_command,
    _record_required_test_observation,
    _run_security_check,
    _safe_command_parts,
)


VERIFIER_ENGINE_PLAN_SCHEMA = "dhee.verifier_engine.plan.v1"
VERIFIER_ENGINE_RUN_SCHEMA = "dhee.verifier_engine.run.v1"
FAIL_TO_PASS_BASELINE_SCHEMA = "dhee.verifier_engine.fail_to_pass_baseline.v1"
PASS_TO_PASS_EXPANSION_SCHEMA = "dhee.verifier_engine.pass_to_pass_expansion.v1"
FLAKY_TEST_POLICY_SCHEMA = "dhee.verifier_engine.flaky_test_policy.v1"
CI_MAPPING_SCHEMA = "dhee.verifier_engine.ci_mapping.v1"

GROUP_ORDER = (
    "fail_to_pass",
    "pass_to_pass",
    "nearest_tests",
    "import_smoke",
    "static",
    "security",
)

GROUP_LABELS = {
    "fail_to_pass": "Fail-to-pass",
    "pass_to_pass": "Pass-to-pass",
    "nearest_tests": "Nearest tests",
    "import_smoke": "Import smoke",
    "static": "Static checks",
    "security": "Security checks",
}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _unique_text(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _command_id(command: str, kind: str, source: str) -> str:
    return "vcmd_" + _stable_hash({"command": command, "kind": kind, "source": source}, 12)


def _normalize_rel_path(path: Any) -> Optional[str]:
    text = str(path or "").strip().replace(os.sep, "/")
    if not text or text.startswith(("/", "~")):
        return None
    parts = [part for part in text.split("/") if part]
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _pytest_command_for_path(path: Any) -> Optional[str]:
    normalized = _normalize_rel_path(path)
    if not normalized or not normalized.endswith(".py"):
        return None
    return "pytest " + shlex.quote(normalized)


def _command_parts(command: str) -> List[str]:
    try:
        return shlex.split(str(command or ""))
    except ValueError:
        return []


def _first_executable(parts: Sequence[str]) -> str:
    if len(parts) >= 3 and Path(parts[0]).name in {"uv", "poetry", "pipenv"} and parts[1] == "run":
        parts = parts[2:]
    if not parts:
        return ""
    if Path(parts[0]).name in {"python", "python3", Path(sys.executable).name} and "-m" in parts:
        idx = list(parts).index("-m")
        if idx + 1 < len(parts):
            return str(parts[idx + 1])
    return Path(parts[0]).name


def _classify_command_safety(command: str) -> Tuple[bool, Optional[str], str]:
    if str(command or "").startswith("builtin:"):
        return True, None, "builtin"
    _parts, reason = _safe_command_parts(command)
    return reason is None, reason, "runner"


def _ci_hint(command: str, kind: str, groups: Sequence[str], required: bool) -> Dict[str, Any]:
    parts = _command_parts(command)
    executable = _first_executable(parts)
    stage = "verify"
    job_hint = "custom-verifier"
    confidence = "medium"

    if str(command or "").startswith("builtin:"):
        stage = "security"
        job_hint = "security-policy"
        confidence = "high"
    elif executable == "pytest":
        stage = "test"
        test_targets = [part for part in parts[1:] if not part.startswith("-")]
        job_hint = "pytest"
        if test_targets:
            job_hint = "pytest:" + ",".join(test_targets[:3])
        confidence = "high"
    elif executable == "py_compile":
        stage = "static"
        job_hint = "python-import-smoke" if "import_smoke" in groups else "python-compile"
        confidence = "high"
    elif executable == "ruff":
        stage = "lint"
        job_hint = "ruff"
        confidence = "high"
    elif executable == "mypy":
        stage = "typecheck"
        job_hint = "mypy"
        confidence = "high"
    elif executable in {"npm", "pnpm", "yarn"}:
        stage = "test" if any("test" in part for part in parts[1:]) else "build"
        job_hint = executable + (":" + parts[1] if len(parts) > 1 else "")

    return {
        "stage": stage,
        "job_hint": job_hint,
        "required_for_merge": bool(required),
        "confidence": confidence,
        "source": "deterministic_command_mapping",
        "kind": kind,
    }


def _known_flaky_commands(contract: Dict[str, Any], card: Dict[str, Any]) -> List[str]:
    candidates: List[Any] = []
    for source in (contract, card, contract.get("metadata") or {}, card.get("metadata") or {}):
        candidates.extend(_as_list(source.get("flaky_tests")))
        candidates.extend(_as_list(source.get("known_flaky_tests")))
        policy = source.get("flaky_test_policy") or {}
        if isinstance(policy, dict):
            candidates.extend(_as_list(policy.get("known_flaky_commands")))
            candidates.extend(_as_list(policy.get("flaky_tests")))
    return _unique_text(candidates)


def _flaky_entry(command: str, known_flaky: Sequence[str], attempts: int) -> Dict[str, Any]:
    is_known = command in set(known_flaky)
    return {
        "known_flaky": is_known,
        "attempts": max(2, int(attempts or 2)) if is_known else 1,
        "retry_policy": "run_all_attempts_require_stable_pass" if is_known else "single_attempt",
        "requires_stable_pass": is_known,
        "failure_masking_allowed": False,
    }


def _runner_group_for_kind(kind: str) -> str:
    if kind == "fail_to_pass":
        return "fail_to_pass"
    if kind == "pass_to_pass":
        return "pass_to_pass"
    if kind == "import_smoke":
        return "import_smoke"
    if kind == "static_check":
        return "static"
    if kind == "security_check":
        return "security"
    return "static"


def _add_or_merge_command(
    commands: List[Dict[str, Any]],
    index: Dict[str, Dict[str, Any]],
    *,
    command: str,
    kind: str,
    group: str,
    required: bool,
    source: str,
    runner_command_id: Optional[str] = None,
    known_flaky: Sequence[str] = (),
    flaky_attempts: int = 2,
) -> Dict[str, Any]:
    command = str(command or "").strip()
    if command in index:
        item = index[command]
        if group not in item["groups"]:
            item["groups"].append(group)
        item["required"] = bool(item.get("required") or required)
        if source not in item["sources"]:
            item["sources"].append(source)
        if runner_command_id and runner_command_id not in item["runner_command_ids"]:
            item["runner_command_ids"].append(runner_command_id)
        item["ci"] = _ci_hint(command, item["kind"], item["groups"], item["required"])
        return item

    safe, blocked_reason, execution_mode = _classify_command_safety(command)
    item = {
        "command_id": _command_id(command, kind, source),
        "runner_command_ids": [runner_command_id] if runner_command_id else [],
        "kind": kind,
        "group": group,
        "groups": [group],
        "command": command,
        "required": bool(required),
        "sources": [source],
        "safe_to_execute": safe,
        "blocked_reason": blocked_reason,
        "execution_mode": execution_mode,
        "flaky": _flaky_entry(command, known_flaky, flaky_attempts),
    }
    item["ci"] = _ci_hint(command, kind, item["groups"], item["required"])
    commands.append(item)
    index[command] = item
    return item


def _ordered_commands(commands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {name: idx for idx, name in enumerate(GROUP_ORDER)}

    def sort_key(item: Dict[str, Any]) -> Tuple[int, str]:
        groups = item.get("groups") or []
        primary = min((order.get(group, len(order)) for group in groups), default=len(order))
        return primary, str(item.get("command") or "")

    return sorted(commands, key=sort_key)


def _build_commands(
    *,
    runner_plan: Dict[str, Any],
    card: Dict[str, Any],
    contract: Dict[str, Any],
    include_pass_to_pass: bool,
    include_static: bool,
    include_security: bool,
    include_nearest_tests: bool,
    max_commands: int,
    flaky_attempts: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    known_flaky = _known_flaky_commands(contract, card)
    commands: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}

    for item in runner_plan.get("commands") or []:
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        kind = str(item.get("kind") or "static_check")
        group = _runner_group_for_kind(kind)
        _add_or_merge_command(
            commands,
            index,
            command=command,
            kind=kind,
            group=group,
            required=bool(item.get("required")),
            source=str(item.get("source") or "runner_plan"),
            runner_command_id=item.get("command_id"),
            known_flaky=known_flaky,
            flaky_attempts=flaky_attempts,
        )

    fail_to_pass = set(_unique_text(card.get("fail_to_pass_tests") or contract.get("must_run") or []))
    pass_to_pass_base = _unique_text(card.get("pass_to_pass_tests") or [])
    nearest_paths = _unique_text(card.get("nearest_tests") or [])
    nearest_commands = _unique_text(
        command for command in (_pytest_command_for_path(path) for path in nearest_paths) if command
    )
    excluded_from_expansion = [command for command in nearest_commands + pass_to_pass_base if command in fail_to_pass]

    if include_pass_to_pass:
        for command in pass_to_pass_base:
            _add_or_merge_command(
                commands,
                index,
                command=command,
                kind="pass_to_pass",
                group="pass_to_pass",
                required=True,
                source="verification_card.pass_to_pass_tests",
                known_flaky=known_flaky,
                flaky_attempts=flaky_attempts,
            )
    if include_nearest_tests:
        for command in nearest_commands:
            if command in fail_to_pass:
                continue
            _add_or_merge_command(
                commands,
                index,
                command=command,
                kind="nearest_test",
                group="nearest_tests",
                required=False,
                source="verification_card.nearest_tests",
                known_flaky=known_flaky,
                flaky_attempts=flaky_attempts,
            )

    if include_static:
        for command in _unique_text(card.get("import_smoke_tests") or []):
            _add_or_merge_command(
                commands,
                index,
                command=command,
                kind="import_smoke",
                group="import_smoke",
                required=True,
                source="verification_card.import_smoke_tests",
                known_flaky=known_flaky,
                flaky_attempts=flaky_attempts,
            )
        for command in _unique_text(card.get("static_checks") or []):
            _add_or_merge_command(
                commands,
                index,
                command=command,
                kind="static_check",
                group="static",
                required=True,
                source="verification_card.static_checks",
                known_flaky=known_flaky,
                flaky_attempts=flaky_attempts,
            )

    if include_security:
        for check in _unique_text(card.get("security_checks") or []):
            _add_or_merge_command(
                commands,
                index,
                command=f"builtin:{check}",
                kind="security_check",
                group="security",
                required=True,
                source="verification_card.security_checks",
                known_flaky=known_flaky,
                flaky_attempts=flaky_attempts,
            )

    ordered = _ordered_commands(commands)
    limit = max(1, int(max_commands or DEFAULT_MAX_COMMANDS))
    retained = ordered[:limit]
    retained_ids = {item["command_id"] for item in retained}
    expansion = {
        "schema_version": PASS_TO_PASS_EXPANSION_SCHEMA,
        "base_commands": pass_to_pass_base,
        "nearest_test_paths": nearest_paths,
        "expanded_commands": [
            {
                "command": item["command"],
                "command_id": item["command_id"],
                "groups": item["groups"],
            }
            for item in retained
            if "pass_to_pass" in item.get("groups", []) or "nearest_tests" in item.get("groups", [])
        ],
        "excluded_duplicates": sorted(set(excluded_from_expansion)),
        "truncated_command_ids": [item["command_id"] for item in ordered if item["command_id"] not in retained_ids],
    }
    return retained, expansion


def _groups(commands: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for group in GROUP_ORDER:
        members = [item for item in commands if group in (item.get("groups") or [])]
        out.append(
            {
                "group": group,
                "label": GROUP_LABELS[group],
                "command_ids": [item["command_id"] for item in members],
                "required_count": sum(1 for item in members if item.get("required")),
                "optional_count": sum(1 for item in members if not item.get("required")),
            }
        )
    return out


def _baseline_capture_model(commands: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    scenarios: List[Dict[str, Any]] = []
    for item in commands:
        if "fail_to_pass" not in (item.get("groups") or []):
            continue
        baseline_id = "baseline_" + _stable_hash(item.get("command"), 12)
        scenarios.append(
            {
                "baseline_id": baseline_id,
                "command_id": item.get("command_id"),
                "command": item.get("command"),
                "capture_phase": "pre_change",
                "confirm_phase": "post_change",
                "expected_pre_change_status": "failed",
                "expected_post_change_status": "passed",
                "baseline_required": True,
                "evidence_keys": {
                    "pre_change": f"{baseline_id}.pre_change_result",
                    "post_change": f"{item.get('command_id')}.final",
                },
            }
        )
    return {
        "schema_version": FAIL_TO_PASS_BASELINE_SCHEMA,
        "mode": "fail_to_pass_transition_capture",
        "scenarios": scenarios,
        "acceptance": {
            "requires_post_change_pass": True,
            "baseline_failure_is_expected": True,
            "baseline_missing_does_not_verify_transition": True,
            "pre_change_pass_is_suspicious_by_default": True,
            "flaky_fail_to_pass_requires_stable_post_change_pass": True,
        },
    }


def _flaky_policy(commands: Sequence[Dict[str, Any]], known_flaky: Sequence[str]) -> Dict[str, Any]:
    return {
        "schema_version": FLAKY_TEST_POLICY_SCHEMA,
        "default_attempts": 1,
        "known_flaky_commands": list(known_flaky),
        "deterministic": True,
        "failure_masking_allowed": False,
        "commands": [
            {
                "command_id": item.get("command_id"),
                "command": item.get("command"),
                **(item.get("flaky") or {}),
            }
            for item in commands
        ],
    }


def _ci_mapping(commands: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": CI_MAPPING_SCHEMA,
        "provider": "generic",
        "workflow_candidates": [
            ".github/workflows",
            ".gitlab-ci.yml",
            "circleci/config.yml",
            "azure-pipelines.yml",
        ],
        "commands": [
            {
                "command_id": item.get("command_id"),
                "command": item.get("command"),
                **(item.get("ci") or {}),
            }
            for item in commands
        ],
    }


def build_advanced_verification_plan(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    include_pass_to_pass: bool = True,
    include_nearest_tests: bool = True,
    include_static: bool = True,
    include_security: bool = True,
    max_commands: int = DEFAULT_MAX_COMMANDS,
    flaky_attempts: int = 2,
) -> Dict[str, Any]:
    """Return the executable verifier-engine plan without running commands."""

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    contract = compiled.get("contract") or {}
    runner_plan = build_runner_verification_plan(
        compiled,
        repo=repo_root,
        include_pass_to_pass=include_pass_to_pass,
        include_static=include_static,
        include_security=include_security,
        max_commands=max_commands,
    )
    card = dict(runner_plan.get("verification_card") or contract.get("verification_card") or {})
    known_flaky = _known_flaky_commands(contract, card)
    commands, pass_to_pass_expansion = _build_commands(
        runner_plan=runner_plan,
        card=card,
        contract=contract,
        include_pass_to_pass=include_pass_to_pass,
        include_static=include_static,
        include_security=include_security,
        include_nearest_tests=include_nearest_tests,
        max_commands=max_commands,
        flaky_attempts=flaky_attempts,
    )
    return _sanitize_obj(
        {
            "schema_version": VERIFIER_ENGINE_PLAN_SCHEMA,
            "task_id": contract.get("task_id"),
            "repo": str(repo_root),
            "commands": commands,
            "groups": _groups(commands),
            "fail_to_pass_baseline": _baseline_capture_model(commands),
            "pass_to_pass_expansion": pass_to_pass_expansion,
            "flaky_test_policy": _flaky_policy(commands, known_flaky),
            "ci_mapping": _ci_mapping(commands),
            "runner_plan": runner_plan,
            "contract": contract,
            "compiled_contract": compiled,
            "truncated": bool(runner_plan.get("truncated")) or bool(pass_to_pass_expansion.get("truncated_command_ids")),
            "policy": {
                "execution": "safe_verifier_engine",
                "safe_execution": "dhee.verification_runner._execute_command",
                "safe_security_execution": "dhee.verification_runner._run_security_check",
                "shell": False,
                "no_shell": True,
                "no_llm": True,
                "deterministic": True,
                "safe_command_classifier": "dhee.verification_runner._safe_command_parts",
                "max_commands": max(1, int(max_commands or DEFAULT_MAX_COMMANDS)),
                "known_flaky_attempts": max(2, int(flaky_attempts or 2)),
            },
        }
    )


def build_verifier_plan(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    include_pass_to_pass: bool = True,
    include_nearest_tests: bool = True,
    include_static: bool = True,
    include_security: bool = True,
    max_commands: int = DEFAULT_MAX_COMMANDS,
    flaky_attempts: int = 2,
) -> Dict[str, Any]:
    """Compatibility alias for callers that prefer the shorter engine name."""

    return build_advanced_verification_plan(
        task_contract,
        repo=repo,
        include_pass_to_pass=include_pass_to_pass,
        include_nearest_tests=include_nearest_tests,
        include_static=include_static,
        include_security=include_security,
        max_commands=max_commands,
        flaky_attempts=flaky_attempts,
    )


def _baseline_index(baseline_plan: Any) -> Dict[str, Dict[str, Any]]:
    if not baseline_plan:
        return {}
    root = baseline_plan
    if isinstance(root, dict) and isinstance(root.get("verifier_run"), dict):
        root = root["verifier_run"]
    if isinstance(root, dict) and isinstance(root.get("verification_run"), dict):
        root = root["verification_run"]

    out: Dict[str, Dict[str, Any]] = {}

    def add(command: Any, status: Any, item: Dict[str, Any]) -> None:
        text = str(command or "").strip()
        if not text:
            return
        entry = dict(item)
        entry["status"] = str(status or entry.get("status") or "unknown")
        entry.setdefault("command", text)
        entry.setdefault("evidence_key", entry.get("evidence_pointer") or entry.get("command_id") or "baseline:" + _stable_hash(text, 12))
        out[text] = entry

    if isinstance(root, dict):
        for command, value in (root.get("baseline_results") or {}).items():
            if isinstance(value, dict):
                add(command, value.get("status"), value)
            else:
                add(command, value, {"source": "baseline_results"})
        for item in root.get("commands") or root.get("results") or []:
            if isinstance(item, dict):
                status = item.get("status")
                attempts = item.get("attempts") or []
                if attempts and isinstance(attempts[-1], dict):
                    status = attempts[-1].get("status") or status
                add(item.get("command"), status, item)
        for item in root.get("fail_to_pass_transitions") or []:
            if isinstance(item, dict):
                add(
                    item.get("command"),
                    item.get("pre_change_status") or item.get("baseline_status"),
                    {
                        "source": "fail_to_pass_transitions",
                        "evidence_key": (item.get("evidence_keys") or {}).get("pre_change"),
                    },
                )
    return out


def _attempt_from_executed(command_id: str, attempt_no: int, executed: Dict[str, Any], *, executed_flag: bool = True) -> Dict[str, Any]:
    evidence_key = f"{command_id}.attempt_{attempt_no}"
    return {
        "attempt": attempt_no,
        "evidence_key": evidence_key,
        "executed": bool(executed_flag),
        "status": executed.get("status") or "failed",
        "exit_code": executed.get("exit_code"),
        "stdout_tail": executed.get("stdout_tail") or "",
        "stderr_tail": executed.get("stderr_tail") or "",
        "duration_ms": int(executed.get("duration_ms") or 0),
        "observation": executed.get("observation") or {},
        "blocked_reason": (executed.get("observation") or {}).get("blocked_reason"),
    }


def _blocked_attempt(command_id: str, reason: str) -> Dict[str, Any]:
    return _attempt_from_executed(
        command_id,
        1,
        {
            "status": "blocked",
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": reason,
            "duration_ms": 0,
            "observation": {"blocked_reason": reason},
        },
        executed_flag=False,
    )


def _aggregate_attempts(attempts: Sequence[Dict[str, Any]], flaky: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    if not attempts:
        return "skipped", {"stable_pass": False, "reason": "no_attempts"}
    statuses = [str(item.get("status") or "failed") for item in attempts]
    if any(status == "blocked" for status in statuses):
        return "blocked", {"stable_pass": False, "reason": "blocked"}
    if flaky.get("known_flaky"):
        stable_pass = len(attempts) >= int(flaky.get("attempts") or 2) and all(status == "passed" for status in statuses)
        if stable_pass:
            return "passed", {"stable_pass": True, "reason": "all_flaky_attempts_passed"}
        if any(status == "timeout" for status in statuses):
            return "timeout", {"stable_pass": False, "reason": "flaky_attempt_timeout"}
        if any(status == "failed" for status in statuses):
            return "failed", {"stable_pass": False, "reason": "flaky_attempt_failed"}
        return statuses[-1], {"stable_pass": False, "reason": "flaky_attempts_not_stable"}
    return statuses[-1], {"stable_pass": statuses[-1] == "passed", "reason": "single_attempt"}


def _execute_one_command(
    repo_root: Path,
    contract: Dict[str, Any],
    item: Dict[str, Any],
    *,
    timeout_sec: int,
) -> Dict[str, Any]:
    command_id = str(item.get("command_id") or _command_id(item.get("command"), item.get("kind"), "runtime"))
    command = str(item.get("command") or "")
    flaky = dict(item.get("flaky") or {})
    attempts: List[Dict[str, Any]] = []

    safe, blocked_reason, execution_mode = _classify_command_safety(command)
    if not safe:
        attempts.append(_blocked_attempt(command_id, blocked_reason or "unsafe verifier command"))
    else:
        attempt_count = max(1, int(flaky.get("attempts") or 1))
        for attempt_no in range(1, attempt_count + 1):
            if command.startswith("builtin:"):
                executed = _run_security_check(repo_root, contract, command.split(":", 1)[1])
            else:
                executed = _execute_command(repo_root, command, timeout_sec)
            attempts.append(_attempt_from_executed(command_id, attempt_no, executed))

    status, flaky_result = _aggregate_attempts(attempts, flaky)
    final = attempts[-1] if attempts else {}
    return _sanitize_obj(
        {
            "command_id": command_id,
            "runner_command_ids": item.get("runner_command_ids") or [],
            "command": command,
            "kind": item.get("kind"),
            "group": item.get("group"),
            "groups": item.get("groups") or [],
            "required": bool(item.get("required")),
            "safe_to_execute": safe,
            "execution_mode": execution_mode,
            "blocked_reason": blocked_reason if not safe else final.get("blocked_reason"),
            "attempts": attempts,
            "status": status,
            "exit_code": final.get("exit_code"),
            "stdout_tail": final.get("stdout_tail") or "",
            "stderr_tail": final.get("stderr_tail") or "",
            "duration_ms": sum(int(attempt.get("duration_ms") or 0) for attempt in attempts),
            "evidence_key": f"{command_id}.final",
            "flaky": {**flaky, **flaky_result},
            "ci": item.get("ci") or {},
        }
    )


def _transition_status(pre_status: str, post_status: str, *, allow_prechange_passed: bool) -> str:
    if post_status == "blocked":
        return "blocked"
    if post_status in {"failed", "timeout"}:
        return "failed"
    if post_status == "skipped":
        return "skipped"
    if pre_status == "missing":
        return "baseline_missing"
    if pre_status == "failed" and post_status == "passed":
        return "passed"
    if pre_status == "passed" and post_status == "passed":
        return "passed_preexisting" if allow_prechange_passed else "suspicious_failed"
    if pre_status in {"failed", "timeout"} and post_status != "passed":
        return "failed"
    return "partial"


def _fail_to_pass_transitions(
    command_results: Sequence[Dict[str, Any]],
    baseline_plan: Any,
    *,
    allow_prechange_passed: bool,
) -> List[Dict[str, Any]]:
    baseline = _baseline_index(baseline_plan)
    transitions: List[Dict[str, Any]] = []
    for item in command_results:
        if "fail_to_pass" not in (item.get("groups") or []):
            continue
        command = str(item.get("command") or "")
        entry = baseline.get(command) or {}
        pre_status = str(entry.get("status") or "missing")
        post_status = str(item.get("status") or "failed")
        status = _transition_status(pre_status, post_status, allow_prechange_passed=allow_prechange_passed)
        baseline_id = "baseline_" + _stable_hash(command, 12)
        transitions.append(
            {
                "baseline_id": baseline_id,
                "command_id": item.get("command_id"),
                "command": command,
                "pre_change_status": pre_status,
                "post_change_status": post_status,
                "transition_status": status,
                "verified": status == "passed",
                "baseline_missing": pre_status == "missing",
                "baseline_suspicious": status == "suspicious_failed",
                "evidence_keys": {
                    "pre_change": entry.get("evidence_key") or f"{baseline_id}.missing",
                    "post_change": item.get("evidence_key") or f"{item.get('command_id')}.final",
                },
            }
        )
    return transitions


def _group_results(command_results: Sequence[Dict[str, Any]], group: str) -> List[Dict[str, Any]]:
    return [item for item in command_results if group in (item.get("groups") or [])]


def _static_results(command_results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        item
        for item in command_results
        if "static" in (item.get("groups") or []) or "import_smoke" in (item.get("groups") or [])
    ]


def _flaky_evidence(command_results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in command_results:
        flaky = item.get("flaky") or {}
        if not flaky.get("known_flaky"):
            continue
        out.append(
            {
                "command_id": item.get("command_id"),
                "command": item.get("command"),
                "required": item.get("required"),
                "attempt_count": len(item.get("attempts") or []),
                "attempt_statuses": [attempt.get("status") for attempt in item.get("attempts") or []],
                "stable_pass": flaky.get("stable_pass") is True,
                "failure_masking_allowed": False,
                "status": item.get("status"),
            }
        )
    return out


def _gate_summary(
    command_results: Sequence[Dict[str, Any]],
    transitions: Sequence[Dict[str, Any]],
    *,
    strict: bool,
    truncated: bool,
    require_baseline: bool,
) -> Dict[str, Any]:
    required = [item for item in command_results if item.get("required")]
    optional_nearest = [
        item
        for item in command_results
        if not item.get("required") and "nearest_tests" in (item.get("groups") or [])
    ]
    blocked_required = [item for item in required if item.get("status") == "blocked"]
    failed_required = [item for item in required if item.get("status") in {"failed", "timeout"}]
    skipped_required = [item for item in required if item.get("status") == "skipped"]
    transition_blocked = [item for item in transitions if item.get("transition_status") == "blocked"]
    transition_failed = [
        item
        for item in transitions
        if item.get("transition_status") in {"failed", "suspicious_failed"}
    ]
    baseline_missing = [item for item in transitions if item.get("transition_status") == "baseline_missing"]
    optional_nearest_failed = [
        item
        for item in optional_nearest
        if item.get("status") in {"failed", "timeout", "blocked"}
    ]

    reasons: List[str] = []
    if blocked_required or transition_blocked:
        status = "blocked"
        reasons.append("required command or transition blocked")
    elif failed_required or transition_failed:
        status = "failed"
        reasons.append("required verifier gate failed")
    elif strict and optional_nearest_failed:
        status = "failed"
        reasons.append("strict nearest-test gate failed")
    elif not command_results:
        status = "skipped"
        reasons.append("no verifier commands selected")
    elif skipped_required or (require_baseline and baseline_missing) or optional_nearest_failed or truncated:
        status = "partial"
        if skipped_required:
            reasons.append("required command skipped")
        if baseline_missing:
            reasons.append("fail-to-pass baseline missing")
        if optional_nearest_failed:
            reasons.append("optional nearest test failed")
        if truncated:
            reasons.append("plan truncated")
    elif required and all(item.get("status") == "passed" for item in required):
        status = "passed"
        reasons.append("all required verifier gates passed")
    elif not required and all(item.get("status") == "passed" for item in command_results):
        status = "passed"
        reasons.append("all optional verifier commands passed")
    else:
        status = "partial"
        reasons.append("verifier gate incomplete")

    return {
        "status": status,
        "reasons": reasons,
        "required_count": len(required),
        "passed_required_count": sum(1 for item in required if item.get("status") == "passed"),
        "failed_required_commands": [item.get("command") for item in failed_required],
        "blocked_required_commands": [
            {"command": item.get("command"), "reason": item.get("blocked_reason") or item.get("stderr_tail")}
            for item in blocked_required
        ],
        "skipped_required_commands": [item.get("command") for item in skipped_required],
        "optional_nearest_failed_commands": [item.get("command") for item in optional_nearest_failed],
        "transition_failures": [
            {"command": item.get("command"), "transition_status": item.get("transition_status")}
            for item in transition_failed
        ],
        "baseline_missing_commands": [item.get("command") for item in baseline_missing],
        "strict_nearest_tests": bool(strict),
        "truncated": bool(truncated),
    }


def _proof_verifier_result(
    command_results: Sequence[Dict[str, Any]],
    gate_summary: Mapping[str, Any],
    transitions: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    required = [item for item in command_results if item.get("required")]
    failed_required = [
        item
        for item in required
        if item.get("status") in {"failed", "timeout", "blocked", "skipped"}
    ]
    return {
        "schema_version": "dhee.verifier_engine.proof_result.v1",
        "source": "dhee.verifier_engine",
        "status": gate_summary.get("status"),
        "required_commands": [item.get("command") for item in required],
        "passed_required_commands": [
            item.get("command")
            for item in required
            if item.get("status") == "passed"
        ],
        "failed_required_commands": [item.get("command") for item in failed_required],
        "blocked_required_commands": gate_summary.get("blocked_required_commands") or [],
        "baseline_missing_commands": gate_summary.get("baseline_missing_commands") or [],
        "optional_nearest_failed_commands": gate_summary.get("optional_nearest_failed_commands") or [],
        "fail_to_pass_transitions": [
            {
                "command": item.get("command"),
                "transition_status": item.get("transition_status"),
                "verified": item.get("verified"),
                "evidence_keys": item.get("evidence_keys") or {},
            }
            for item in transitions
        ],
        "command_evidence": [
            {
                "command_id": item.get("command_id"),
                "command": item.get("command"),
                "status": item.get("status"),
                "required": item.get("required"),
                "groups": item.get("groups") or [],
                "evidence_key": item.get("evidence_key"),
                "attempt_evidence_keys": [
                    attempt.get("evidence_key")
                    for attempt in item.get("attempts") or []
                ],
            }
            for item in command_results
        ],
    }


def _record_engine_observations(
    compiled: Optional[Mapping[str, Any]],
    repo_root: Path,
    contract: Mapping[str, Any],
    command_results: Sequence[Dict[str, Any]],
    run_id: str,
    strict: bool,
) -> None:
    if not compiled:
        return
    for item in command_results:
        if "fail_to_pass" not in (item.get("groups") or []):
            continue
        if item.get("status") not in {"passed", "failed", "timeout"}:
            item["recorded_observation"] = None
            continue
        item["recorded_observation"] = _record_required_test_observation(
            dict(compiled),
            repo_root,
            dict(contract),
            str(item.get("command") or ""),
            item,
            run_id,
            strict,
        )


def execute_verifier_plan(
    plan: Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    baseline_plan: Optional[Dict[str, Any]] = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    strict: bool = False,
    persist: bool = True,
    allow_prechange_passed: bool = False,
    require_fail_to_pass_baseline: bool = True,
) -> Dict[str, Any]:
    """Execute an advanced verifier plan and return a durable gate result."""

    repo_root = _resolve_repo_root(repo or plan.get("repo"))
    task_id = str(plan.get("task_id") or "unknown")
    contract = dict(plan.get("contract") or {})
    compiled = plan.get("compiled_contract")
    created_at = _now_iso()
    run_id = "ve_" + _stable_hash({"task_id": task_id, "created_at": created_at}, 18)

    command_results = [
        _execute_one_command(repo_root, contract, item, timeout_sec=timeout_sec)
        for item in plan.get("commands") or []
    ]
    _record_engine_observations(compiled, repo_root, contract, command_results, run_id, strict)
    transitions = _fail_to_pass_transitions(
        command_results,
        baseline_plan,
        allow_prechange_passed=allow_prechange_passed,
    )
    gate_summary = _gate_summary(
        command_results,
        transitions,
        strict=strict,
        truncated=bool(plan.get("truncated")),
        require_baseline=bool(require_fail_to_pass_baseline),
    )

    proof: Dict[str, Any] = {"proof_bundle": {}, "paths": {}}
    if compiled:
        proof = build_proof_bundle(compiled, repo=repo_root, strict=strict, persist=persist)
    proof_bundle = dict(proof.get("proof_bundle") or {})
    legacy_verifier_result = proof_bundle.get("verifier_result")
    if legacy_verifier_result:
        proof_bundle["contract_supervisor_verifier_result"] = legacy_verifier_result
    proof_bundle["verifier_result"] = _proof_verifier_result(command_results, gate_summary, transitions)

    result = _sanitize_obj(
        {
            "format": "dhee_verifier_engine_run.v1",
            "schema_version": VERIFIER_ENGINE_RUN_SCHEMA,
            "run_id": run_id,
            "created_at": created_at,
            "task_id": task_id,
            "repo": str(repo_root),
            "status": gate_summary["status"],
            "commands": command_results,
            "fail_to_pass_transitions": transitions,
            "pass_to_pass_results": _group_results(command_results, "pass_to_pass"),
            "nearest_test_results": _group_results(command_results, "nearest_tests"),
            "static_results": _static_results(command_results),
            "security_results": _group_results(command_results, "security"),
            "flaky_evidence": _flaky_evidence(command_results),
            "gate_summary": gate_summary,
            "proof_bundle": proof_bundle,
            "policy": {
                "execution": "safe_verifier_engine",
                "safe_execution": "dhee.verification_runner._execute_command",
                "safe_security_execution": "dhee.verification_runner._run_security_check",
                "shell": False,
                "no_shell": True,
                "no_llm": True,
                "safe_command_classifier": "dhee.verification_runner._safe_command_parts",
                "timeout_sec": int(timeout_sec or DEFAULT_TIMEOUT_SEC),
                "strict": bool(strict),
                "allow_prechange_passed": bool(allow_prechange_passed),
                "require_fail_to_pass_baseline": bool(require_fail_to_pass_baseline),
                "flaky_failure_masking_allowed": False,
            },
            "plan": {
                "schema_version": plan.get("schema_version"),
                "command_ids": [item.get("command_id") for item in plan.get("commands") or []],
                "truncated": bool(plan.get("truncated")),
            },
            "paths": dict(proof.get("paths") or {}),
        }
    )

    if persist:
        run_path = _task_run_dir(repo_root, task_id) / "verifier_engine_runs" / f"{run_id}.json"
        write_result = write_json_atomic(run_path, result, sanitize=_sanitize_obj)
        if not write_result.get("ok"):
            diagnostic = write_result.get("diagnostic") or {}
            raise RuntimeError(diagnostic.get("message") or f"failed to write verifier engine run {run_path}")
        result["paths"]["verifier_engine_run"] = str(run_path)
    return result


def run_verifier_engine(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    baseline_plan: Optional[Dict[str, Any]] = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    max_commands: int = DEFAULT_MAX_COMMANDS,
    include_pass_to_pass: bool = True,
    include_nearest_tests: bool = True,
    include_static: bool = True,
    include_security: bool = True,
    flaky_attempts: int = 2,
    strict: bool = False,
    persist: bool = True,
    allow_prechange_passed: bool = False,
    require_fail_to_pass_baseline: bool = True,
) -> Dict[str, Any]:
    """Build and execute the verifier engine gate for a task contract."""

    plan = build_advanced_verification_plan(
        task_contract,
        repo=repo,
        include_pass_to_pass=include_pass_to_pass,
        include_nearest_tests=include_nearest_tests,
        include_static=include_static,
        include_security=include_security,
        max_commands=max_commands,
        flaky_attempts=flaky_attempts,
    )
    return execute_verifier_plan(
        plan,
        repo=repo,
        baseline_plan=baseline_plan,
        timeout_sec=timeout_sec,
        strict=strict,
        persist=persist,
        allow_prechange_passed=allow_prechange_passed,
        require_fail_to_pass_baseline=require_fail_to_pass_baseline,
    )


def _legacy_summary(command_results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    required = [item for item in command_results if item.get("required")]
    return {
        "required_count": len(required),
        "passed_required_count": sum(1 for item in required if item.get("status") == "passed"),
        "failed_required_commands": [
            item.get("command")
            for item in required
            if item.get("status") in {"failed", "timeout"}
        ],
        "blocked_required_commands": [
            {"command": item.get("command"), "reason": item.get("blocked_reason") or item.get("stderr_tail")}
            for item in required
            if item.get("status") == "blocked"
        ],
        "fail_to_pass_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in command_results
            if item.get("kind") == "fail_to_pass"
        ],
        "pass_to_pass_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in command_results
            if item.get("kind") == "pass_to_pass"
        ],
        "static_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in command_results
            if item.get("kind") in {"import_smoke", "static_check"}
        ],
        "security_status": [
            {"command": item.get("command"), "status": item.get("status")}
            for item in command_results
            if item.get("kind") == "security_check"
        ],
    }


def run_verification_compat(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    max_commands: int = DEFAULT_MAX_COMMANDS,
    include_pass_to_pass: bool = True,
    include_static: bool = True,
    include_security: bool = True,
    strict: bool = False,
    persist: bool = True,
) -> Dict[str, Any]:
    """Compatibility envelope for the legacy verification-run API.

    The verifier engine is authoritative, but callers that expect
    ``verification_run.summary`` and ``verification_run.results`` keep working.
    """

    engine_run = run_verifier_engine(
        task_contract,
        repo=repo,
        timeout_sec=timeout_sec,
        max_commands=max_commands,
        include_pass_to_pass=include_pass_to_pass,
        include_nearest_tests=False,
        include_static=include_static,
        include_security=include_security,
        strict=strict,
        persist=persist,
        require_fail_to_pass_baseline=False,
    )
    paths = dict(engine_run.get("paths") or {})
    if paths.get("verifier_engine_run"):
        paths.setdefault("verification_run", paths["verifier_engine_run"])
    verification_run = _sanitize_obj(
        {
            "schema_version": VERIFICATION_RUN_SCHEMA,
            "run_id": engine_run.get("run_id"),
            "created_at": engine_run.get("created_at"),
            "task_id": engine_run.get("task_id"),
            "repo": engine_run.get("repo"),
            "status": engine_run.get("status"),
            "plan": engine_run.get("plan") or {},
            "results": engine_run.get("commands") or [],
            "summary": _legacy_summary(engine_run.get("commands") or []),
            "proof_bundle": engine_run.get("proof_bundle") or {},
            "policy": {
                **dict(engine_run.get("policy") or {}),
                "engine_replaced_legacy_runner": True,
            },
        }
    )
    return {
        "format": "dhee_verification_run.v2",
        "verification_run": verification_run,
        "verifier_engine_run": engine_run,
        "paths": paths,
    }


__all__ = [
    "CI_MAPPING_SCHEMA",
    "FAIL_TO_PASS_BASELINE_SCHEMA",
    "FLAKY_TEST_POLICY_SCHEMA",
    "PASS_TO_PASS_EXPANSION_SCHEMA",
    "VERIFIER_ENGINE_PLAN_SCHEMA",
    "VERIFIER_ENGINE_RUN_SCHEMA",
    "build_advanced_verification_plan",
    "build_verifier_plan",
    "execute_verifier_plan",
    "run_verification_compat",
    "run_verifier_engine",
]
