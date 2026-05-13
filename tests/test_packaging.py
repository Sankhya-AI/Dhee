from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_handoff_bus_is_bundled_not_external_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"engram-bus>=' not in pyproject
    assert 'engram-bus = "engram_bus.server:main"' in pyproject
    assert "bus = []" in pyproject

    assert 'where = [".", "engram-bus"]' in pyproject
    assert 'include = ["dhee*", "engram*", "engram_bus*"]' in pyproject
    assert (ROOT / "engram-bus" / "engram_bus" / "__init__.py").exists()


def test_curl_installer_verifies_handoff_bus():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "Cross-agent handoff bus ready" in installer
    assert "from dhee.core.kernel import _get_bus" in installer
    assert "for bin_name in dhee dhee-mcp engram-bus" in installer
    assert "dhee uninstall --yes" in installer
