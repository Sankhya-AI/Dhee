from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_handoff_bus_is_bundled_not_external_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert '"engram-bus>=' not in pyproject
    assert 'engram-bus = "engram_bus.server:main"' in pyproject
    assert "bus = []" in pyproject

    assert 'where = [".", "engram-bus"]' in pyproject
    assert 'include = ["dhee*", "engram*", "engram_bus*"]' in pyproject
    assert "prune dhee/ui\n" not in manifest
    assert "prune engram-bus" not in manifest
    assert '"dhee.ui*"' not in pyproject
    assert 'dhee-ui = "dhee.ui.cli:main"' in pyproject
    assert '"web/dist/*"' in pyproject
    assert '"web/dist/assets/*"' in pyproject
    assert '"web/src/components/canvas/*"' in pyproject
    assert (ROOT / "engram-bus" / "engram_bus" / "__init__.py").exists()
    assert (ROOT / "engram-bus" / "engram_bus" / "bus.py").exists()


def test_curl_installer_verifies_handoff_bus():
    installer = (ROOT / "install.sh").read_text(encoding="utf-8")

    assert "Cross-agent handoff bus ready" in installer
    assert "from dhee.core.kernel import _get_bus" in installer
    assert "for bin_name in dhee dhee-mcp engram-bus" in installer
    assert 'DEFAULT_PACKAGE="dhee>=6.2.0"' in installer
    assert "DHEE_INSTALL_PACKAGE" in installer
    assert "FALLBACK_PACKAGE" in installer
    assert "DHEE_INIT_REPO" in installer
    assert "DHEE_INIT_SKIP_INGEST" in installer
    assert "Open the UI:" in installer
    assert "dhee ui" in installer
    assert "dhee uninstall --yes" in installer
