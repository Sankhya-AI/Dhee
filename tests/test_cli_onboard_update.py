"""Coverage for the installer-facing CLI additions.

  - dhee onboard:  provider picker, API-key prompt, config + secret
    store writes, UI-build auto-detect
  - dhee update:  editable vs PyPI path, UI rebuild hook
  - dhee ui --no-open: skips the browser-open side effect
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_onboard_provider_default_and_key_paste(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    # Provider picker: empty line → default → openai.  Key: paste a real-looking key.
    tty_in = io.StringIO("\nsk-test-OPENAI-key-0123456789\n")
    tty_out = io.StringIO()
    monkeypatch.setattr("dhee.cli_onboard._open_tty", lambda: (tty_in, tty_out))
    # Secret store + config write are exercised for real — not mocked.
    # Skip the UI build so this stays unit-test fast.
    from dhee.cli_onboard import run_onboard

    rc = run_onboard(skip_ui_build=True)
    assert rc == 0

    from dhee.cli_config import CONFIG_PATH, load_config

    assert Path(CONFIG_PATH).exists()
    assert load_config()["provider"] == "openai"

    # The key must be retrievable through the normal lookup path.
    from dhee.secret_store import get_stored_api_key

    assert get_stored_api_key("openai") == "sk-test-OPENAI-key-0123456789"

    out = tty_out.getvalue()
    assert "Dhee setup" in out
    assert "dhee ui" in out


def test_onboard_gemini_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    tty_in = io.StringIO("2\nAIza-fake-gemini-key-12345\n")
    tty_out = io.StringIO()
    monkeypatch.setattr("dhee.cli_onboard._open_tty", lambda: (tty_in, tty_out))
    from dhee.cli_onboard import run_onboard

    assert run_onboard(skip_ui_build=True) == 0
    from dhee.cli_config import load_config
    from dhee.secret_store import get_stored_api_key

    assert load_config()["provider"] == "gemini"
    assert get_stored_api_key("gemini") == "AIza-fake-gemini-key-12345"


def test_onboard_ollama_skips_key_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    # "4" selects ollama. No second line needed — key prompt is skipped.
    tty_in = io.StringIO("4\n")
    tty_out = io.StringIO()
    monkeypatch.setattr("dhee.cli_onboard._open_tty", lambda: (tty_in, tty_out))
    from dhee.cli_onboard import run_onboard

    assert run_onboard(skip_ui_build=True) == 0
    from dhee.cli_config import load_config

    assert load_config()["provider"] == "ollama"
    output = tty_out.getvalue()
    assert "no api key required" in output.lower() or "no API key required" in output


def test_onboard_empty_key_does_not_explode(tmp_path, monkeypatch):
    """Users who hit enter through the key prompt must get a graceful skip."""
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    tty_in = io.StringIO("1\n\n")  # default provider + blank key
    tty_out = io.StringIO()
    monkeypatch.setattr("dhee.cli_onboard._open_tty", lambda: (tty_in, tty_out))
    from dhee.cli_onboard import run_onboard
    from dhee.secret_store import get_stored_api_key

    assert run_onboard(skip_ui_build=True) == 0
    assert get_stored_api_key("openai") is None
    assert "No key provided" in tty_out.getvalue()


def test_onboard_requires_tty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dhee.cli_onboard._open_tty", lambda: (None, None))
    from dhee.cli_onboard import run_onboard

    assert run_onboard(skip_ui_build=True) == 1
    assert "interactive terminal" in capsys.readouterr().err


def test_onboard_non_interactive_provider_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    # Only the key line is consumed when --provider is passed.
    tty_in = io.StringIO("nvapi-fake-nvidia-token-12345\n")
    tty_out = io.StringIO()
    monkeypatch.setattr("dhee.cli_onboard._open_tty", lambda: (tty_in, tty_out))
    from dhee.cli_onboard import run_onboard
    from dhee.cli_config import load_config
    from dhee.secret_store import get_stored_api_key

    assert run_onboard(provider_default="nvidia", skip_ui_build=True) == 0
    assert load_config()["provider"] == "nvidia"
    assert get_stored_api_key("nvidia") == "nvapi-fake-nvidia-token-12345"


def test_update_uses_pypi_path_on_non_editable_install(monkeypatch):
    """`dhee update` must run pip install --upgrade when not editable."""
    import dhee.cli_update as cli_update

    runs: list[list[str]] = []

    def _fake_run(cmd, *, cwd=None, check=True):
        runs.append([str(c) for c in cmd])
        return 0

    monkeypatch.setattr(cli_update, "_is_editable_install", lambda: False)
    monkeypatch.setattr(cli_update, "_run", _fake_run)
    monkeypatch.setattr(cli_update, "_relink_binaries", lambda: None)
    monkeypatch.setattr(cli_update, "_rebuild_ui", lambda: None)
    monkeypatch.setattr(cli_update, "_print_current_version", lambda: None)
    monkeypatch.setattr(
        cli_update, "_venv_python", lambda: Path("/tmp/fake/venv/bin/python")
    )

    cli_update.cmd_update(SimpleNamespace(from_pypi=False))

    # Must have issued the pip upgrade for dhee[all]
    pip_upgrades = [r for r in runs if "install" in r and "--upgrade" in r and any("dhee" in piece for piece in r)]
    assert pip_upgrades, f"expected pip upgrade, got {runs}"


def test_update_uses_git_pull_when_editable(monkeypatch, tmp_path):
    import dhee.cli_update as cli_update

    runs: list[list[str]] = []
    monkeypatch.setattr(cli_update, "_is_editable_install", lambda: True)
    monkeypatch.setattr(cli_update, "_project_root_for_editable", lambda: tmp_path)
    monkeypatch.setattr(cli_update, "_run", lambda cmd, cwd=None, check=True: runs.append([str(c) for c in cmd]))
    monkeypatch.setattr(cli_update, "_relink_binaries", lambda: None)
    monkeypatch.setattr(cli_update, "_rebuild_ui", lambda: None)
    monkeypatch.setattr(cli_update, "_print_current_version", lambda: None)
    monkeypatch.setattr(
        cli_update, "_venv_python", lambda: Path("/tmp/fake/venv/bin/python")
    )

    cli_update.cmd_update(SimpleNamespace(from_pypi=False))

    assert any(r[:2] == ["git", "pull"] for r in runs), runs
    assert any("install" in r and "-e" in r for r in runs), runs


def test_update_from_pypi_flag_overrides_editable(monkeypatch, tmp_path):
    import dhee.cli_update as cli_update

    runs: list[list[str]] = []
    monkeypatch.setattr(cli_update, "_is_editable_install", lambda: True)
    monkeypatch.setattr(cli_update, "_project_root_for_editable", lambda: tmp_path)
    monkeypatch.setattr(cli_update, "_run", lambda cmd, cwd=None, check=True: runs.append([str(c) for c in cmd]))
    monkeypatch.setattr(cli_update, "_relink_binaries", lambda: None)
    monkeypatch.setattr(cli_update, "_rebuild_ui", lambda: None)
    monkeypatch.setattr(cli_update, "_print_current_version", lambda: None)
    monkeypatch.setattr(
        cli_update, "_venv_python", lambda: Path("/tmp/fake/venv/bin/python")
    )

    cli_update.cmd_update(SimpleNamespace(from_pypi=True))

    # No git pull; pip upgrade dhee[all] instead.
    assert not any(r[:2] == ["git", "pull"] for r in runs), runs
    assert any("install" in r and "--upgrade" in r and "dhee[all]" in r for r in runs)


def test_ui_auto_open_respects_no_open_flag(monkeypatch):
    """`dhee ui --no-open` must not schedule the browser open."""
    scheduled: list = []
    monkeypatch.setattr(
        "dhee.ui.cli._schedule_browser_open",
        lambda url, delay=1.0: scheduled.append(url),
    )

    fake_uvicorn = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn", SimpleNamespace(run=fake_uvicorn)
    )
    # create_app is invoked but we don't want a real FastAPI instance.
    monkeypatch.setattr("dhee.ui.server.create_app", lambda **kw: fake_server)

    dist_dir = Path(__file__).parent / "_fake_dist"
    dist_dir.mkdir(exist_ok=True)
    # Pretend the dist exists so cmd_ui doesn't fall back to dev mode.
    monkeypatch.setattr(
        "dhee.ui.cli.Path",
        lambda *a, **kw: Path(*a, **kw),
    )

    from dhee.ui.cli import cmd_ui

    args = SimpleNamespace(
        host="127.0.0.1",
        port=8080,
        dev=True,  # dev mode bypasses the dist check
        verbose=False,
        no_open=True,
    )
    with patch("dhee.ui.cli.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        cmd_ui(args)

    assert scheduled == []  # no browser open when flag is set
    fake_uvicorn.assert_called_once()


def test_ui_auto_open_triggers_by_default(monkeypatch):
    scheduled: list = []
    monkeypatch.setattr(
        "dhee.ui.cli._schedule_browser_open",
        lambda url, delay=1.0: scheduled.append(url),
    )
    fake_uvicorn = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn", SimpleNamespace(run=fake_uvicorn)
    )
    monkeypatch.setattr("dhee.ui.server.create_app", lambda **kw: fake_server)

    from dhee.ui.cli import cmd_ui

    args = SimpleNamespace(
        host="127.0.0.1",
        port=8080,
        dev=True,
        verbose=False,
        no_open=False,
    )
    monkeypatch.delenv("DHEE_UI_NO_OPEN", raising=False)
    # Ensure DISPLAY is present on linux so the headless-skip doesn't trigger.
    monkeypatch.setenv("DISPLAY", ":0")

    with patch("dhee.ui.cli.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        cmd_ui(args)

    assert scheduled == ["http://127.0.0.1:8080/"]


def test_ui_auto_open_respects_env_var(monkeypatch):
    """DHEE_UI_NO_OPEN=1 suppresses auto-open even without --no-open."""
    scheduled: list = []
    monkeypatch.setattr(
        "dhee.ui.cli._schedule_browser_open",
        lambda url, delay=1.0: scheduled.append(url),
    )
    fake_uvicorn = MagicMock()
    fake_server = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn", SimpleNamespace(run=fake_uvicorn)
    )
    monkeypatch.setattr("dhee.ui.server.create_app", lambda **kw: fake_server)
    monkeypatch.setenv("DHEE_UI_NO_OPEN", "1")

    from dhee.ui.cli import cmd_ui

    args = SimpleNamespace(
        host="127.0.0.1",
        port=8080,
        dev=True,
        verbose=False,
        no_open=False,
    )
    with patch("dhee.ui.cli.subprocess.Popen") as popen:
        popen.return_value = MagicMock()
        cmd_ui(args)

    assert scheduled == []
