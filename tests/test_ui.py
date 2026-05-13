from __future__ import annotations

import json

from dhee.ui.server import STATIC_DIR, build_dashboard_payload


def test_public_ui_payload_matches_team_dashboard_shape(tmp_path, monkeypatch):
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

    assert out["workspace"]["root_path"] == str(repo.resolve())
    assert out["org_chart"]["projects"][0]["project_id"] == "developer-brain"
    assert out["team_rows"][0]["team_id"] == "local-dev"
    assert out["context_firewall"]["aggregate"]["saved_pct"] > 50
    assert out["totals"]["repo_mappings"] == 1
    assert out["totals"]["context_items"] == 1
    assert out["code_brain"]["mapping_status"][0]["sync_status"] in {"indexed", "not_indexed"}
    assert out["context_index"][0]["title"] == "Use compact digests"


def test_public_ui_static_assets_are_the_same_dashboard_as_team_ui():
    root = STATIC_DIR.parents[2]
    team_static = root / "enterprise" / "dhee_enterprise" / "ui" / "static"
    assert (STATIC_DIR / "index.html").exists()
    assert (STATIC_DIR / "styles.css").exists()
    assert (STATIC_DIR / "app.js").exists()
    for name in ["index.html", "styles.css", "app.js"]:
        assert (STATIC_DIR / name).read_text(encoding="utf-8") == (team_static / name).read_text(encoding="utf-8")
