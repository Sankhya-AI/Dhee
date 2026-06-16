"""Regression tests for contract-runtime recovery paths.

2026-06-12 incident: an agent activated an inline (dict) contract — which was
never persisted — with repo enforcement set to deny. Every later supervision
call re-resolved the dangling contract_ref, hit FileNotFoundError, and deny
mode refused all native tool calls including the remediation commands. These
tests pin the three fixes: inline contracts persist on activation, a missing
contract body auto-deactivates the stale runtime, and a supervisor outage in
deny mode still allows read-only tools and dhee CLI remediation.
"""

import shutil
import subprocess
from pathlib import Path

from dhee.contract_runtime import (
    activate_contract_runtime,
    contract_runtime_status,
    guard_router_call,
    set_contract_enforcement,
)
from dhee.task_contracts import compile_task_contract, get_task_contract


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "dhee-test@example.com"], path)
    _run(["git", "config", "user.name", "Dhee Test"], path)
    (path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-m", "initial"], path)
    return path


def _activate_inline(repo: Path, *, strict: bool = True) -> dict:
    compiled = compile_task_contract("Fix the app entrypoint", repo=repo)
    return activate_contract_runtime(compiled, repo=repo, strict=strict, force=True, agent_id="test")


def test_inline_dict_activation_persists_contract_body(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    activated = _activate_inline(repo)
    task_id = activated["task_id"]

    assert Path(activated["contract_ref"]).is_file()
    loaded = get_task_contract(task_id, repo=repo)
    assert loaded["contract"]["task_id"] == task_id


def test_missing_contract_body_auto_deactivates_instead_of_deadlocking(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    activated = _activate_inline(repo)
    set_contract_enforcement("deny", repo=repo, agent_id="test", reason="test")

    # Simulate the incident: the persisted contract body disappears while the
    # runtime still points at it.
    shutil.rmtree(Path(activated["contract_ref"]).parent)

    guard = guard_router_call("Bash", {"cwd": str(repo), "command": "echo hi"})
    assert guard["allowed"] is True
    codes = {diag.get("code") for diag in guard.get("diagnostics") or []}
    assert "CONTRACT_REF_MISSING" in codes

    status = contract_runtime_status(repo=repo)
    assert not status.get("active")


def test_supervisor_outage_in_deny_mode_keeps_recovery_path_open(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    _activate_inline(repo)
    set_contract_enforcement("deny", repo=repo, agent_id="test", reason="test")

    import dhee.contract_supervisor as supervisor

    def _boom(*args, **kwargs):
        raise RuntimeError("supervisor crashed")

    monkeypatch.setattr(supervisor, "supervise_action", _boom)

    read_guard = guard_router_call("Read", {"cwd": str(repo), "file_path": str(repo / "app.py")})
    assert read_guard["allowed"] is True

    remediation = guard_router_call("Bash", {"cwd": str(repo), "command": "dhee context task deactivate"})
    assert remediation["allowed"] is True

    edit_guard = guard_router_call("Edit", {"cwd": str(repo), "file_path": str(repo / "app.py")})
    assert edit_guard["allowed"] is False
    assert "dhee context task" in (edit_guard.get("message") or "")
