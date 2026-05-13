from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from dhee.core.learnings import LearningExchange
from dhee.db.sqlite import SQLiteManager
from dhee.router import ptr_store
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
    product_views = (WEB_DIR / "src" / "views" / "ProductViews.tsx").read_text(encoding="utf-8")
    router_view = (WEB_DIR / "src" / "views" / "RouterView.tsx").read_text(encoding="utf-8")

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
    assert 'label="CURRENT WORK"' in product_views
    assert 'label="LATEST SAVED HANDOFF"' in product_views
    assert "Loading active Claude Code and Codex sessions..." in router_view


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


def test_ui_learning_rows_are_digest_first(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("ENGRAM_HANDOFF_DB", str(tmp_path / "handoff.db"))
    monkeypatch.setenv("DHEE_UI_REPO", str(tmp_path))

    exchange = LearningExchange()
    raw_body = "\n".join(
        [
            "Model: internal-model",
            "Session: session-123",
            "<think>private reasoning should not be shown in review lists</think>",
            "Representative turns:",
            "Use routed grep before reading raw files when the repo is large.",
            " ".join(["extra-noise"] * 120),
        ]
    )
    candidate = exchange.submit(
        title="Inspect before reading",
        body=raw_body,
        source_agent_id="codex",
        source_harness="codex",
        evidence=[{"kind": "successful_outcome"}],
        metadata={"model": "codex-local"},
    )

    client = TestClient(create_app())
    response = client.get("/api/ui/learnings")
    assert response.status_code == 200
    items = response.json()["items"]
    row = next(item for item in items if item["id"] == candidate.id)

    assert "<think>" not in row["body"]
    assert "private reasoning" not in row["body"]
    assert "Representative turns" not in row["body"]
    assert len(row["body"]) <= 420
    assert row["raw_body_chars"] > len(row["body"])
    assert row["evidence_count"] == 1
    assert row["needs_distillation"] is True
    assert "evidence" not in row


def test_ui_product_router_metrics_use_real_pointer_rollup(tmp_path, monkeypatch):
    data_dir = tmp_path / "dhee-data"
    history_db = tmp_path / "history.db"
    router_ptrs = tmp_path / "router-ptrs"
    session_id = "session-ui-router"
    now = datetime.now(timezone.utc).isoformat()

    monkeypatch.setenv("DHEE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DHEE_UI_HISTORY_DB", str(history_db))
    monkeypatch.setenv("ENGRAM_HANDOFF_DB", str(tmp_path / "handoff.db"))
    monkeypatch.setenv("DHEE_UI_REPO", str(tmp_path))
    monkeypatch.setenv("DHEE_ROUTER_PTR_DIR", str(router_ptrs))
    monkeypatch.setenv("DHEE_ROUTER_SESSION_ID", session_id)
    monkeypatch.setenv("DHEE_SESSION_ID", session_id)
    monkeypatch.setenv("DHEE_AGENT_ID", "codex")
    monkeypatch.chdir(tmp_path)

    db = SQLiteManager(str(history_db))
    db.upsert_agent_session(
        {
            "id": session_id,
            "user_id": "default",
            "runtime_id": "codex",
            "native_session_id": session_id,
            "title": "Routed UI proof",
            "state": "recent",
            "cwd": str(tmp_path),
            "started_at": now,
            "updated_at": now,
            "metadata": {"preview": "large read stayed behind a pointer"},
        }
    )
    ptr_store.store(
        "x" * 3500,
        tool="Read",
        meta={
            "agent_id": "codex",
            "harness": "codex",
            "session_id": session_id,
            "cwd": str(tmp_path),
            "repo": str(tmp_path),
        },
    )

    client = TestClient(create_app())
    command = client.get("/api/ui/command-center")
    assert command.status_code == 200
    router = command.json()["router"]
    assert router["totalCalls"] == 1
    assert router["sessionTokensSaved"] >= 900

    replay = client.get("/api/ui/proof-replay")
    assert replay.status_code == 200
    replay_data = replay.json()
    assert replay_data["totals"]["digests"] >= 1
    assert any(item["tokens_saved"] >= 900 for item in replay_data["items"])
