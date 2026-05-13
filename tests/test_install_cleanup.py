from __future__ import annotations

import json
import os

from dhee import cli
from dhee.install_cleanup import cleanup_installer_symlinks, cleanup_shell_profiles


def test_cleanup_shell_profiles_removes_only_dhee_blocks(tmp_path):
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    fish_dir = home / ".config" / "fish"
    fish_dir.mkdir(parents=True)
    home.mkdir(exist_ok=True)

    zshrc = home / ".zshrc"
    zshrc.write_text(
        f'export PATH="/custom:$PATH"\n\n# dhee\nexport PATH="{bin_dir}:$PATH"\n# keep\n',
        encoding="utf-8",
    )
    bashrc = home / ".bashrc"
    bashrc.write_text(
        f'# user-managed PATH, not a Dhee installer block\nexport PATH="{bin_dir}:$PATH"\n',
        encoding="utf-8",
    )
    fish = fish_dir / "config.fish"
    fish.write_text(
        f"set -gx FOO bar\n\n# dhee\nfish_add_path {bin_dir}\n",
        encoding="utf-8",
    )

    result = cleanup_shell_profiles(home=home)

    assert {entry["path"] for entry in result["changed"]} == {str(zshrc), str(fish)}
    assert zshrc.read_text(encoding="utf-8") == 'export PATH="/custom:$PATH"\n# keep\n'
    assert bashrc.read_text(encoding="utf-8").endswith(f'export PATH="{bin_dir}:$PATH"\n')
    assert fish.read_text(encoding="utf-8") == "set -gx FOO bar\n"


def test_cleanup_installer_symlinks_only_managed_targets(tmp_path):
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    data_dir = home / ".dhee"
    managed_bin = data_dir / ".venv" / "bin"
    outside_bin = tmp_path / "outside"
    bin_dir.mkdir(parents=True)
    managed_bin.mkdir(parents=True)
    outside_bin.mkdir()

    managed_dhee = managed_bin / "dhee"
    managed_dhee.write_text("#!/bin/sh\n", encoding="utf-8")
    outside_dhee_mcp = outside_bin / "dhee-mcp"
    outside_dhee_mcp.write_text("#!/bin/sh\n", encoding="utf-8")

    os.symlink(managed_dhee, bin_dir / "dhee")
    os.symlink(managed_bin / "engram-bus", bin_dir / "engram-bus")
    os.symlink(outside_dhee_mcp, bin_dir / "dhee-mcp")
    (bin_dir / "dhee-mcp-full").write_text("# user managed\n", encoding="utf-8")

    result = cleanup_installer_symlinks(data_dir, home=home)

    removed_names = {entry["name"] for entry in result["removed"]}
    skipped = {entry["name"]: entry["reason"] for entry in result["skipped"]}
    assert removed_names == {"dhee", "engram-bus"}
    assert skipped == {"dhee-mcp": "outside_managed_venv", "dhee-mcp-full": "not_symlink"}
    assert not (bin_dir / "dhee").exists()
    assert not (bin_dir / "engram-bus").is_symlink()
    assert (bin_dir / "dhee-mcp").is_symlink()
    assert (bin_dir / "dhee-mcp-full").exists()


def test_cli_uninstall_missing_data_still_removes_leftover_installer_artifacts(
    tmp_path,
    monkeypatch,
    capsys,
):
    home = tmp_path / "home"
    data_dir = home / ".dhee"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    os.symlink(data_dir / ".venv" / "bin" / "dhee", bin_dir / "dhee")
    (home / ".zshrc").write_text(
        f'export PATH="/custom:$PATH"\n\n# dhee\nexport PATH="{bin_dir}:$PATH"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DHEE_DATA_DIR", str(data_dir))

    import dhee.cli_config as cli_config

    monkeypatch.setattr(cli_config, "CONFIG_DIR", str(data_dir))
    monkeypatch.setattr(cli_config, "CONFIG_PATH", str(data_dir / "config.json"))
    monkeypatch.setattr("sys.argv", ["dhee", "uninstall", "--yes", "--json"])

    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert data["removed"] is False
    assert data["reason"] == "missing"
    assert data["harnesses"]["codex"]["action"] == "disabled"
    assert len(data["install_artifacts"]["symlinks"]["removed"]) == 1
    assert len(data["install_artifacts"]["shell_profiles"]["changed"]) == 1
    assert not data_dir.exists()
    assert not (bin_dir / "dhee").is_symlink()
    assert (home / ".zshrc").read_text(encoding="utf-8") == 'export PATH="/custom:$PATH"\n'
