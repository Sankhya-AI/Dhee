from __future__ import annotations

import json
import os

from dhee import cli, runtime


def test_runtime_status_reports_paths_without_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))

    status = runtime.status()

    assert status["daemon"]["running"] is False
    assert status["paths"]["runtime_dir"].endswith("runtime")
    assert status["venv"]["python"]


def test_runtime_daemon_start_stop_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        status = runtime.status(timeout=1.0)
        assert status["daemon"]["running"] is True
        assert status["daemon"]["health"]["status"] == "ok"
    finally:
        stopped = runtime.stop_daemon(timeout=5.0)
        assert stopped["stopped"] in {True, False}
        assert runtime.status()["daemon"]["running"] is False


def test_runtime_daemon_executes_dheefs_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        result = runtime.execute_shell(
            "cat dhee://state/current",
            repo=str(tmp_path),
            user_id="test-user",
            agent_id="pytest",
            workspace_id=str(tmp_path),
        )
        assert result is not None
        assert result["ok"] is True
        assert result["runtime"]["daemon"] is True
        assert "# Dhee Compiled State" in result["stdout"]
        assert result["data"]["path"] == "/state/current.md"
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_daemon_executes_context_status(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        result = runtime.execute_context(
            "status",
            repo=str(tmp_path),
            user_id="test-user",
            agent_id="pytest",
            workspace_id=str(tmp_path),
        )
        assert result is not None
        assert result["format"] == "dhee_context_status"
        assert result["runtime"]["daemon"] is True
        assert result["repo"] == str(tmp_path)
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_daemon_executes_safe_router_read_and_grep(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    src = tmp_path / "router_target.py"
    src.write_text("def target():\n    return 'needle'\n", encoding="utf-8")

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        read_result = runtime.execute_router("read", {"file_path": str(src)})
        grep_result = runtime.execute_router("grep", {"pattern": "needle", "path": str(src)})

        assert read_result is not None
        assert read_result["runtime"]["daemon"] is True
        assert read_result["kind"] == "python"
        assert "target" in read_result["digest"]
        assert grep_result is not None
        assert grep_result["runtime"]["daemon"] is True
        assert grep_result["match_count"] == 1
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_daemon_bash_requires_server_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.delenv("DHEE_RUNTIME_ENABLE_BASH", raising=False)
    monkeypatch.delenv("DHEE_RUNTIME_BASH_ALLOWLIST", raising=False)
    monkeypatch.delenv("DHEE_RUNTIME_BASH_CWD_ALLOWLIST", raising=False)

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        result = runtime.execute_router(
            "bash",
            {"command": "echo should-not-run-in-daemon", "cwd": str(tmp_path), "timeout": 1},
        )
        assert result is None
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_daemon_executes_bash_with_allowlisted_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("DHEE_RUNTIME_ENABLE_BASH", "1")
    monkeypatch.setenv("DHEE_RUNTIME_BASH_ALLOWLIST", str(tmp_path))
    monkeypatch.setenv("DHEE_RUNTIME_BASH_MAX_TIMEOUT", "2")

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        result = runtime.execute_router(
            "bash",
            {"command": "printf runtime-bash", "cwd": str(tmp_path), "timeout": 30},
        )
        assert result is not None
        assert result["exit_code"] == 0
        assert result["runtime"]["daemon"] is True
        assert result["runtime"]["bash"]["cwd"] == str(tmp_path)
        assert result["runtime"]["bash"]["allowlist_match"] == str(tmp_path)
        assert result["runtime"]["bash"]["requested_timeout_seconds"] == 30.0
        assert result["runtime"]["bash"]["effective_timeout_seconds"] == 2.0
        assert result["runtime"]["bash"]["trust_boundary"] == "server_env_enable_and_cwd_allowlist"
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_daemon_rejects_bash_outside_allowlist(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("DHEE_RUNTIME_ENABLE_BASH", "1")
    monkeypatch.setenv("DHEE_RUNTIME_BASH_ALLOWLIST", str(allowed))

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        result = runtime.execute_router(
            "bash",
            {"command": "echo outside", "cwd": str(outside), "timeout": 1},
        )
        assert result is None
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_can_be_disabled_for_shell_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("DHEE_RUNTIME_DISABLE", "1")

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        result = runtime.execute_shell("ls /state", repo=str(tmp_path))
        assert result is None
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_can_be_disabled_for_router_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("DHEE_RUNTIME_DISABLE", "1")

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        assert runtime.execute_router("read", {"file_path": str(tmp_path / "x.py")}) is None
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_mcp_bash_uses_runtime_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("DHEE_RUNTIME_ENABLE_BASH", "1")
    monkeypatch.setenv("DHEE_RUNTIME_BASH_ALLOWLIST", str(tmp_path))
    monkeypatch.setenv("DHEE_RUNTIME_BASH_MAX_TIMEOUT", "2")

    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        import dhee.mcp_slim as slim

        result = slim._handle_dhee_bash(
            {"command": "printf mcp-runtime-bash", "cwd": str(tmp_path), "timeout": 30}
        )
        assert result["exit_code"] == 0
        assert result["runtime"]["daemon"] is True
        assert result["runtime"]["bash"]["effective_timeout_seconds"] == 2.0
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_runtime_cli_status_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setattr("sys.argv", ["dhee", "runtime", "status", "--json"])

    cli.main()
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["daemon"]["running"] is False
    assert data["paths"]["data_dir"] == str(tmp_path / "dhee-data")


def test_cli_uninstall_stops_daemon_and_removes_data_dir(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "dhee-data"
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    managed_bin = data_dir / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    managed_bin.mkdir(parents=True)
    data_dir.mkdir(exist_ok=True)
    (data_dir / "config.json").write_text('{"version":"1"}\n', encoding="utf-8")
    (managed_bin / "dhee").write_text("#!/bin/sh\n", encoding="utf-8")
    os.symlink(managed_bin / "dhee", bin_dir / "dhee")
    (home / ".zshrc").write_text(
        f'export PATH="/custom:$PATH"\n\n# dhee\nexport PATH="{bin_dir}:$PATH"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DHEE_DATA_DIR", str(data_dir))

    import dhee.cli_config as cli_config

    monkeypatch.setattr(cli_config, "CONFIG_DIR", str(data_dir))
    monkeypatch.setattr(cli_config, "CONFIG_PATH", str(data_dir / "config.json"))

    started = runtime.start_daemon(timeout=8.0)
    assert started["started"] is True
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.argv", ["dhee", "uninstall", "--yes", "--json"])

    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert data["removed"] is True
    assert data["path"] == str(data_dir)
    assert data["runtime"]["stopped"] is True
    assert data["harnesses"]["codex"]["action"] == "disabled"
    assert len(data["install_artifacts"]["symlinks"]["removed"]) == 1
    assert len(data["install_artifacts"]["shell_profiles"]["changed"]) == 1
    assert not data_dir.exists()
    assert not (bin_dir / "dhee").exists()
    assert (home / ".zshrc").read_text(encoding="utf-8") == 'export PATH="/custom:$PATH"\n'
    assert runtime.status()["daemon"]["running"] is False


def test_cli_shell_uses_runtime_when_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        monkeypatch.setattr(
            "sys.argv",
            ["dhee", "shell", "cat dhee://state/current", "--repo", str(tmp_path), "--json"],
        )
        cli.main()
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["runtime"]["daemon"] is True
        assert data["data"]["path"] == "/state/current.md"
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_cli_context_status_uses_runtime_when_available(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        monkeypatch.setattr(
            "sys.argv",
            ["dhee", "context", "status", "--repo", str(tmp_path), "--json"],
        )
        cli.main()
        data = json.loads(capsys.readouterr().out)
        assert data["format"] == "dhee_context_status"
        assert data["runtime"]["daemon"] is True
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_mcp_shell_uses_runtime_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        import dhee.mcp_slim as slim

        result = slim._handle_dhee_shell(
            {
                "command": "cat dhee://state/current",
                "repo": str(tmp_path),
                "user_id": "test-user",
                "agent_id": "pytest",
            }
        )
        assert result["ok"] is True
        assert result["runtime"]["daemon"] is True
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_mcp_context_status_uses_runtime_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        import dhee.mcp_slim as slim

        result = slim._handle_dhee_context_status(
            {
                "repo": str(tmp_path),
                "user_id": "test-user",
                "agent_id": "pytest",
            }
        )
        assert result["format"] == "dhee_context_status"
        assert result["runtime"]["daemon"] is True
    finally:
        runtime.stop_daemon(timeout=5.0)


def test_mcp_read_uses_runtime_when_available(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    src = tmp_path / "mod.py"
    src.write_text("def from_runtime():\n    return 1\n", encoding="utf-8")
    started = runtime.start_daemon(timeout=8.0)
    try:
        assert started["started"] is True
        import dhee.mcp_slim as slim

        result = slim._handle_dhee_read({"file_path": str(src)})
        assert result["kind"] == "python"
        assert result["runtime"]["daemon"] is True
    finally:
        runtime.stop_daemon(timeout=5.0)
