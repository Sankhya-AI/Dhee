"""PR 3 — project/workspace asset drawer endpoints + line linkage.

Covers:
  - migration v8_project_assets creates the table + indexes
  - POST /api/projects/{id}/assets uploads, hashes, dedupes
  - POST /api/workspaces/{id}/assets uploads without a project
  - GET returns uploaded assets + recent processing results
  - DELETE removes DB row and storage file
  - emit_agent_activity auto-links asset_id when an agent touches the
    asset's storage path (the pitch deck "processed by codex" flow)
"""

from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient

from dhee.core.shared_tasks import publish_shared_task_result
from dhee.core.workspace_line import emit_agent_activity
from dhee.db.sqlite import SQLiteManager


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee"))
    monkeypatch.setenv("DHEE_USER_ID", "default")
    # Reset the cached DB singleton so each test runs against its own
    # tmp_path rather than whatever the previous test initialised.
    import dhee.mcp_server as mcp_server_module

    mcp_server_module._db = None
    from dhee.ui.server import create_app

    app = create_app(serve_static=False)
    with TestClient(app) as c:
        yield c
    mcp_server_module._db = None


def _seed_workspace(tmp_path):
    dhee_dir = tmp_path / "dhee"
    db = SQLiteManager(str(dhee_dir / "history.db"))
    ws = db.upsert_workspace(
        {"user_id": "default", "name": "Labs", "root_path": str(tmp_path)}
    )
    proj = db.upsert_workspace_project(
        {"user_id": "default", "workspace_id": ws["id"], "name": "backend"}
    )
    return db, ws["id"], proj["id"]


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------


def test_project_assets_table_and_dedup(tmp_path):
    db, ws_id, project_id = _seed_workspace(tmp_path)

    a = db.upsert_project_asset(
        {
            "workspace_id": ws_id,
            "project_id": project_id,
            "user_id": "default",
            "storage_path": str(tmp_path / "spec.pdf"),
            "name": "spec.pdf",
            "checksum": "sha-abc",
            "size_bytes": 1024,
            "mime_type": "application/pdf",
        }
    )
    assert a["id"]

    # Same checksum in the same scope = dedup, returns the same row.
    b = db.upsert_project_asset(
        {
            "workspace_id": ws_id,
            "project_id": project_id,
            "user_id": "default",
            "storage_path": str(tmp_path / "spec-duplicate.pdf"),
            "name": "spec-duplicate.pdf",
            "checksum": "sha-abc",
            "size_bytes": 1024,
        }
    )
    assert b["id"] == a["id"]

    # Different checksum = new row.
    c = db.upsert_project_asset(
        {
            "workspace_id": ws_id,
            "project_id": project_id,
            "user_id": "default",
            "storage_path": str(tmp_path / "design.fig"),
            "name": "design.fig",
            "checksum": "sha-xyz",
        }
    )
    assert c["id"] != a["id"]

    listed = db.list_project_assets(project_id=project_id, user_id="default")
    assert {row["id"] for row in listed} == {a["id"], c["id"]}

    # Workspace-level asset (project_id NULL) shows up in list_workspace_assets
    # but not in list_project_assets.
    w = db.upsert_project_asset(
        {
            "workspace_id": ws_id,
            "project_id": None,
            "user_id": "default",
            "storage_path": str(tmp_path / "readme.md"),
            "name": "readme.md",
            "checksum": "sha-readme",
        }
    )
    workspace_assets = db.list_workspace_assets(workspace_id=ws_id, user_id="default")
    assert w["id"] in {row["id"] for row in workspace_assets}
    assert a["id"] in {row["id"] for row in workspace_assets}  # project assets included
    workspace_only = db.list_workspace_assets(
        workspace_id=ws_id, user_id="default", include_project_assets=False
    )
    assert {row["id"] for row in workspace_only} == {w["id"]}

    # Resolve by path
    hit = db.find_project_asset_by_storage_path(str(tmp_path / "design.fig"))
    assert hit and hit["id"] == c["id"]

    # Delete
    assert db.delete_project_asset(c["id"], user_id="default")
    assert db.get_project_asset(c["id"]) is None


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


def test_upload_list_and_delete_project_asset(tmp_path, client):
    _db, ws_id, project_id = _seed_workspace(tmp_path)

    files = {"file": ("contract.md", io.BytesIO(b"# Contract\nv1"), "text/markdown")}
    r = client.post(
        f"/api/projects/{project_id}/assets",
        files=files,
        data={"label": "contract"},
    )
    assert r.status_code == 200, r.text
    asset = r.json()["asset"]
    assert asset["project_id"] == project_id
    assert asset["workspace_id"] == ws_id
    assert asset["name"] == "contract"
    assert asset["checksum"]
    assert os.path.exists(asset["storage_path"])
    stored_path = asset["storage_path"]
    asset_id = asset["id"]

    # Re-upload same content → dedup returns existing asset
    files2 = {"file": ("contract-v1.md", io.BytesIO(b"# Contract\nv1"), "text/markdown")}
    r2 = client.post(f"/api/projects/{project_id}/assets", files=files2)
    assert r2.status_code == 200
    asset2 = r2.json()["asset"]
    assert asset2["id"] == asset_id

    # Listing includes only one row
    rl = client.get(f"/api/projects/{project_id}/assets")
    assert rl.status_code == 200
    assets = rl.json()["assets"]
    assert len(assets) == 1
    assert assets[0]["id"] == asset_id
    assert isinstance(assets[0]["results"], list)  # results array is present

    # Workspace aggregate includes project assets
    rw = client.get(f"/api/workspaces/{ws_id}/assets")
    assert rw.status_code == 200
    assert asset_id in {a["id"] for a in rw.json()["assets"]}

    # Delete
    rd = client.delete(f"/api/project-assets/{asset_id}")
    assert rd.status_code == 200 and rd.json()["ok"] is True
    assert not os.path.exists(stored_path)
    rl2 = client.get(f"/api/projects/{project_id}/assets")
    assert rl2.json()["assets"] == []


def test_upload_workspace_asset_without_project(tmp_path, client):
    _db, ws_id, _project_id = _seed_workspace(tmp_path)

    files = {"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")}
    r = client.post(f"/api/workspaces/{ws_id}/assets", files=files)
    assert r.status_code == 200
    asset = r.json()["asset"]
    assert asset["project_id"] is None
    assert asset["workspace_id"] == ws_id

    rw = client.get(f"/api/workspaces/{ws_id}/assets?include_project_assets=false")
    assert rw.status_code == 200
    workspace_only = rw.json()["assets"]
    assert [a["id"] for a in workspace_only] == [asset["id"]]


def test_upload_emits_line_message(tmp_path, client):
    _db, ws_id, project_id = _seed_workspace(tmp_path)

    files = {"file": ("spec.txt", io.BytesIO(b"spec data"), "text/plain")}
    r = client.post(f"/api/projects/{project_id}/assets", files=files)
    asset_id = r.json()["asset"]["id"]

    rl = client.get(f"/api/workspaces/{ws_id}/line/messages")
    assert rl.status_code == 200
    messages = rl.json()["messages"]
    kinds = {m["message_kind"] for m in messages}
    assert "tool.asset_upload" in kinds
    upload = next(m for m in messages if m["message_kind"] == "tool.asset_upload")
    assert (upload.get("metadata") or {}).get("asset_id") == asset_id


def test_asset_linkage_into_line_and_results(tmp_path, monkeypatch, client):
    """The drawer's killer feature: when an agent reads a known asset, the
    line message carries the asset_id automatically — AND the drawer's
    per-asset processing feed lists that event.
    """
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee"))

    db, ws_id, project_id = _seed_workspace(tmp_path)

    # Upload the asset
    files = {"file": ("paper.pdf", io.BytesIO(b"paper body"), "application/pdf")}
    r = client.post(f"/api/projects/{project_id}/assets", files=files)
    asset = r.json()["asset"]
    storage_path = asset["storage_path"]

    # Create a shared task so publish_shared_task_result has a target.
    task = db.upsert_shared_task(
        {
            "user_id": "default",
            "repo": str(tmp_path),
            "workspace_id": str(tmp_path),
            "project_id": project_id,
            "title": "read the paper",
            "status": "active",
        }
    )

    # Simulate a codex MCP read of the asset file.
    publish_shared_task_result(
        db,
        packet_kind="routed_read",
        tool_name="Read",
        digest="<dhee_read>...</dhee_read>",
        repo=str(tmp_path),
        cwd=str(tmp_path),
        source_path=storage_path,
        source_event_id="codex-evt-1",
        ptr="R-paper",
        shared_task_id=task["id"],
        harness="codex",
        agent_id="codex",
        session_id="codex-sess",
    )

    # Line now has a tool.routed_read message whose metadata carries asset_id
    rl = client.get(f"/api/workspaces/{ws_id}/line/messages")
    messages = rl.json()["messages"]
    routed = [m for m in messages if m["message_kind"] == "tool.routed_read"]
    assert routed, "expected the tool result on the line"
    assert (routed[0].get("metadata") or {}).get("asset_id") == asset["id"]

    # Drawer endpoint now shows the read in the asset's per-card feed
    rp = client.get(f"/api/projects/{project_id}/assets")
    project_asset = rp.json()["assets"][0]
    results = project_asset["results"]
    assert results, "expected per-asset result feed to be populated"
    assert results[0]["tool_name"] == "Read"
    assert results[0]["ptr"] == "R-paper"

    # Line emit directly also picks up the asset_id linkage
    emit_agent_activity(
        db,
        tool_name="Bash",
        packet_kind="routed_bash",
        digest="grep -c 'body' paper.pdf",
        cwd=str(tmp_path),
        source_path=storage_path,
        source_event_id="codex-evt-2",
        ptr="B-paper",
        harness="codex",
        runtime_id="codex",
        native_session_id="codex-sess",
    )
    rl2 = client.get(f"/api/workspaces/{ws_id}/line/messages")
    bash_msg = next(m for m in rl2.json()["messages"] if m["message_kind"] == "tool.routed_bash")
    assert (bash_msg.get("metadata") or {}).get("asset_id") == asset["id"]


def test_unknown_project_rejected(tmp_path, client):
    _db, _ws_id, _project_id = _seed_workspace(tmp_path)
    files = {"file": ("x.txt", io.BytesIO(b"x"), "text/plain")}
    r = client.post("/api/projects/does-not-exist/assets", files=files)
    assert r.status_code == 404
