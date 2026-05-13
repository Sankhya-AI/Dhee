from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dhee.ui.server import create_app


ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = ROOT / "dhee" / "ui" / "web"


def test_ui_source_is_canvas_router_app():
    assert (WEB_DIR / "dist" / "index.html").exists()
    assert (WEB_DIR / "src" / "views" / "CanvasView.tsx").exists()
    assert (WEB_DIR / "src" / "views" / "RouterView.tsx").exists()
    assert (WEB_DIR / "src" / "views" / "ProductViews.tsx").exists()
    assert (WEB_DIR / "src" / "components" / "canvas" / "useInfiniteCanvas.ts").exists()

    nav = (WEB_DIR / "src" / "components" / "NavRail.tsx").read_text(encoding="utf-8")
    canvas = (WEB_DIR / "src" / "components" / "canvas" / "useInfiniteCanvas.ts").read_text(encoding="utf-8")
    app = (WEB_DIR / "src" / "App.tsx").read_text(encoding="utf-8")

    for label in ["HOME", "FIREWALL", "BRAIN", "HANDOFF", "REPLAY", "LEARN", "PACKS"]:
        assert f'label: "{label}"' in nav
    assert "useInfiniteCanvas" in canvas
    assert 'view === "command"' in app
    assert 'view === "canvas"' in app
    assert 'view === "router"' in app
    assert 'view === "handoff"' in app
    assert 'view === "replay"' in app
    assert 'view === "learnings"' in app
    assert 'view === "portability"' in app


def test_ui_serves_built_spa_and_core_api(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("ENGRAM_HANDOFF_DB", str(tmp_path / "handoff.db"))
    monkeypatch.setenv("DHEE_UI_REPO", str(tmp_path))

    client = TestClient(create_app())

    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "/assets/" in index.text

    status = client.get("/api/status")
    assert status.status_code == 200
    assert status.json()["ok"] is True

    graph = client.get("/api/workspace/graph")
    assert graph.status_code == 200
    data = graph.json()
    assert "graph" in data
    assert "nodes" in data["graph"]


def test_ui_product_screen_apis(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("ENGRAM_HANDOFF_DB", str(tmp_path / "handoff.db"))
    monkeypatch.setenv("DHEE_UI_REPO", str(tmp_path))

    client = TestClient(create_app())

    expected = {
        "/api/ui/command-center": ["router", "context", "learnings", "next_action"],
        "/api/ui/handoff": ["continuity", "command", "resume_confidence"],
        "/api/ui/proof-replay": ["items", "totals"],
        "/api/ui/learnings": ["items", "totals"],
        "/api/ui/portability": ["format", "counts", "contract"],
    }
    for path, keys in expected.items():
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        for key in keys:
            assert key in payload
