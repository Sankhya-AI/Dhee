import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dhee import repo_link
from dhee.contract_runtime import (
    ACTIVE_CONTRACT_SCHEMA,
    CONTRACT_SUPERVISOR_UNAVAILABLE,
    contract_enforcement_status,
    contract_runtime_doctor,
    contract_runtime_status,
    guard_router_call,
    set_contract_enforcement,
)
from dhee.mcp_registry import CONTEXT_COMPILER_TOOL_NAMES, TOOL_SPECS
from dhee.runtime_io import append_jsonl_locked, read_json_checked, read_jsonl_checked, write_json_atomic


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _active_path(repo: Path) -> Path:
    return repo / ".dhee" / "context" / "task_runs" / "active_contract.json"


def _write_minimal_active_runtime(repo: Path, task_id: str = "task_hardened") -> None:
    repo_link._ensure_repo_skeleton(repo)
    result = write_json_atomic(
        _active_path(repo),
        {
            "format": ACTIVE_CONTRACT_SCHEMA,
            "schema_version": ACTIVE_CONTRACT_SCHEMA,
            "active": True,
            "status": "active",
            "task_id": task_id,
            "contract_ref": task_id,
            "repo": str(repo),
            "strict": False,
            "contract_hash": "test",
        },
    )
    assert result["ok"]


def test_runtime_io_atomic_write_and_corrupt_quarantine(tmp_path):
    path = tmp_path / "state.json"
    assert write_json_atomic(path, {"schema_version": "x", "value": 1})["ok"]
    assert write_json_atomic(path, {"schema_version": "x", "value": 2})["ok"]
    checked = read_json_checked(path, expected_schema="x")
    assert checked["ok"]
    assert checked["data"]["value"] == 2

    path.write_text("{broken", encoding="utf-8")
    corrupt = read_json_checked(path, quarantine=True)
    assert not corrupt["ok"]
    assert corrupt["quarantine"]["ok"]
    assert not path.exists()
    assert Path(corrupt["quarantine"]["quarantine_path"]).exists()


def test_runtime_io_locked_jsonl_concurrent_appends(tmp_path):
    path = tmp_path / "events.jsonl"

    def append_one(i: int) -> None:
        result = append_jsonl_locked(path, {"i": i})
        assert result["ok"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append_one, range(64)))

    checked = read_jsonl_checked(path)
    assert checked["ok"]
    assert len(checked["records"]) == 64
    assert sorted(record["i"] for record in checked["records"]) == list(range(64))


def test_corrupt_active_contract_is_quarantined_and_surfaced(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    repo_link._ensure_repo_skeleton(repo)
    path = _active_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")

    status = contract_runtime_status(repo=repo)
    assert status["status"] == "corrupt"
    assert status["error"] == "ACTIVE_CONTRACT_CORRUPT"
    assert any(diag["code"] == "RUNTIME_JSON_CORRUPT" for diag in status["diagnostics"])
    assert status["quarantine"]["ok"]
    assert not path.exists()


def test_enforcement_deny_blocks_without_active_contract(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    set_contract_enforcement("deny", repo=repo, agent_id="test")

    guard = guard_router_call("dhee_read", {"repo": str(repo), "file_path": str(repo / "README.md")})
    assert not guard["allowed"]
    assert guard["error"] == "ACTIVE_CONTRACT_REQUIRED"
    assert guard["enforcement"]["mode"] == "deny"


def test_corrupt_enforcement_policy_fails_closed(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    policy_path = repo / ".dhee" / "context" / "task_runs" / "enforcement.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text("{broken", encoding="utf-8")

    status = contract_enforcement_status(repo=repo)
    assert status["mode"] == "deny"
    assert status["policy_corrupt"] is True
    guard = guard_router_call("dhee_read", {"repo": str(repo), "file_path": str(repo / "README.md")})
    assert not guard["allowed"]
    assert guard["error"] == "ACTIVE_CONTRACT_REQUIRED"


def test_enforcement_warn_allows_and_records_warning(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    set_contract_enforcement("warn", repo=repo, agent_id="test")

    guard = guard_router_call("dhee_read", {"repo": str(repo), "file_path": str(repo / "README.md")})
    assert guard["allowed"]
    assert guard["enforcement"]["mode"] == "warn"

    events = read_jsonl_checked(repo / ".dhee" / "context" / "task_runs" / "enforcement" / "runtime_events.jsonl")
    assert any(record.get("event") == "enforcement_warning" for record in events["records"])


def test_enforcement_off_preserves_compatibility_without_active_contract(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    set_contract_enforcement("off", repo=repo, agent_id="test")

    guard = guard_router_call("dhee_read", {"repo": str(repo), "file_path": str(repo / "README.md")})
    assert guard["allowed"]
    assert guard["error"] is None
    assert guard["enforcement"]["mode"] == "off"


def test_env_forces_deny_even_when_policy_off(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    set_contract_enforcement("off", repo=repo, agent_id="test")
    monkeypatch.setenv("DHEE_REQUIRE_ACTIVE_CONTRACT", "1")

    assert contract_enforcement_status(repo=repo)["mode"] == "deny"
    guard = guard_router_call("dhee_read", {"repo": str(repo), "file_path": str(repo / "README.md")})
    assert not guard["allowed"]
    assert guard["error"] == "ACTIVE_CONTRACT_REQUIRED"


def test_deny_blocks_when_supervisor_unavailable(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    _write_minimal_active_runtime(repo)
    set_contract_enforcement("deny", repo=repo, agent_id="test")

    import dhee.contract_supervisor as contract_supervisor

    def explode(*_args, **_kwargs):
        raise RuntimeError("simulated supervisor failure")

    monkeypatch.setattr(contract_supervisor, "supervise_action", explode)
    guard = guard_router_call("dhee_read", {"repo": str(repo), "file_path": str(repo / "README.md")})
    assert not guard["allowed"]
    assert guard["error"] == CONTRACT_SUPERVISOR_UNAVAILABLE


def test_contract_runtime_doctor_reports_unprotected_and_protected(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    assert contract_runtime_doctor(repo=repo)["status"] == "unprotected"

    _write_minimal_active_runtime(repo)
    set_contract_enforcement("deny", repo=repo, agent_id="test")

    class RouterState:
        enabled = True
        managed = True
        env_flag = True
        allowed_tools = ["Read", "Grep", "Bash"]
        settings_path = repo / "settings.json"

    from dhee.router import install as router_install

    monkeypatch.setattr(router_install, "status", lambda: RouterState())
    protected = contract_runtime_doctor(repo=repo)
    assert protected["status"] == "protected"
    assert protected["protected"] is True

    class MissingRouterState(RouterState):
        enabled = False

    monkeypatch.setattr(router_install, "status", lambda: MissingRouterState())
    partial = contract_runtime_doctor(repo=repo)
    assert partial["status"] == "partially_protected"
    assert "native_hook_or_router_not_enabled" in partial["bypass_risks"]


def test_mcp_registry_slim_parity_for_compiler_runtime_tools():
    import dhee.mcp_slim as slim

    tools = {tool.name: tool for tool in slim.TOOLS}
    for name in CONTEXT_COMPILER_TOOL_NAMES:
        assert name in tools
        assert name in slim.HANDLERS
        assert tools[name].inputSchema == TOOL_SPECS[name]["inputSchema"]


def test_mcp_registry_full_parity_when_mcp_installed():
    mcp_server = pytest.importorskip("dhee.mcp_server", reason="mcp package not installed")

    tools = {tool.name: tool for tool in mcp_server.TOOLS}
    for name in CONTEXT_COMPILER_TOOL_NAMES:
        assert name in tools
        assert name in mcp_server.HANDLERS
        assert tools[name].inputSchema == TOOL_SPECS[name]["inputSchema"]
