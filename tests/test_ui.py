from __future__ import annotations

import json

from dhee.ui.server import STATIC_DIR, build_dashboard_payload


def test_public_ui_payload_uses_local_dhee_primitives(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    monkeypatch.setenv("ENGRAM_HANDOFF_DB", str(tmp_path / "handoff.db"))
    repo = tmp_path / "repo"
    context_dir = repo / ".dhee" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "entries.jsonl").write_text(
        json.dumps({"id": "ctx-1", "kind": "decision", "title": "Use compact digests", "content": "Keep raw logs behind ptrs."}) + "\n",
        encoding="utf-8",
    )

    out = build_dashboard_payload(repo=str(repo))

    assert out["format"] == "dhee_public_dashboard"
    assert out["workspace"]["root_path"] == str(repo.resolve())
    assert out["context_firewall"]["aggregate"]["saved_pct"] > 50
    assert out["totals"]["repo_context"] == 1
    assert any(item["name"] == "Codex" for item in out["integrations"])
    assert out["portability"]["export"].startswith("dhee export")


def test_public_ui_static_assets_are_packaged():
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "styles.css").exists()
    assert (STATIC_DIR / "app.js").exists()
    assert "Local Dhee" in (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "Context Firewall" in (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "renderContextFirewall" in (STATIC_DIR / "app.js").read_text(encoding="utf-8")
