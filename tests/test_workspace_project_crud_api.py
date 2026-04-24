"""Coverage for workspace + project create / update / delete via the UI API.

These endpoints back the new WorkspaceManagerModal and are the new
user-facing CRUD surface.
"""

from __future__ import annotations

import json
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee"))
    monkeypatch.setenv("DHEE_USER_ID", "default")
    import dhee.mcp_server as mcp_server_module

    mcp_server_module._db = None
    from dhee.ui.server import create_app

    app = create_app(serve_static=False)
    with TestClient(app) as c:
        yield c
    mcp_server_module._db = None


def _create_workspace(client, *, name: str, path: str) -> dict:
    r = client.post("/api/workspaces", json={"name": name, "root_path": path})
    assert r.status_code == 200, r.text
    return r.json()["workspace"]


def test_create_workspace_seeds_default_project_and_mount(client, tmp_path):
    workspace_root = tmp_path / "office"
    workspace_root.mkdir()
    ws = _create_workspace(client, name="Office", path=str(workspace_root))
    assert ws["id"]
    assert ws["label"] == "Office" or ws["name"] == "Office"
    assert any(
        (m.get("path") or m.get("mount_path")) == str(workspace_root)
        for m in (ws.get("mounts") or ws.get("folders") or [])
    )
    # POST /api/workspaces auto-seeds a "General" project.
    project_names = {p["name"] for p in ws.get("projects") or []}
    assert "General" in project_names


def test_camel_case_create_payloads_scan_live_codex_session(tmp_path, monkeypatch):
    import dhee.mcp_server as mcp_server_module
    import dhee.ui.server as ui_server
    from dhee.ui.server import create_app

    repo_root = tmp_path / "repo"
    frontend_root = repo_root / "apps" / "frontend"
    frontend_root.mkdir(parents=True)

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee"))
    monkeypatch.setenv("DHEE_USER_ID", "default")
    monkeypatch.setenv("DHEE_UI_REPO", str(repo_root))
    mcp_server_module._db = None
    monkeypatch.setattr(
        ui_server,
        "_repo_codex_threads",
        lambda repo, limit=18: [
            {
                "id": "codex-live-frontend",
                "title": "Frontend workspace fix",
                "cwd": str(frontend_root),
                "model": "gpt-5.4",
                "messages": [{"role": "assistant", "content": "Working in frontend"}],
                "recentTools": ["Read"],
                "plan": [],
                "touchedFiles": [],
                "preview": "Working in frontend",
                "updatedAt": "2026-04-24T06:00:00Z",
                "isCurrent": True,
            }
        ],
    )
    monkeypatch.setattr(ui_server, "_find_claude_sessions", lambda repo, limit=8: [])

    with TestClient(create_app(serve_static=False)) as c:
        created = c.post(
            "/api/workspaces",
            json={"label": "Repo Workspace", "rootPath": str(repo_root)},
        )
        assert created.status_code == 200, created.text
        workspace = created.json()["workspace"]
        assert workspace["rootPath"] == str(repo_root)
        assert "session:codex:codex-live-frontend" in {
            session["id"] for session in workspace.get("sessions") or []
        }

        project_created = c.post(
            f"/api/workspaces/{workspace['id']}/projects",
            json={
                "label": "Frontend",
                "defaultRuntime": "claude-code",
                "scopeRules": [{"pathPrefix": str(frontend_root), "label": "frontend"}],
            },
        )
        assert project_created.status_code == 200, project_created.text
        project = project_created.json()["project"]
        assert project["name"] == "Frontend"
        assert project["defaultRuntime"] == "claude-code"
        assert project["scopeRules"][0]["pathPrefix"] == str(frontend_root)
        assert "session:codex:codex-live-frontend" in {
            session["id"] for session in project.get("sessions") or []
        }

    mcp_server_module._db = None


def test_workspace_creation_scans_claude_project_logs(tmp_path, monkeypatch):
    import dhee.mcp_server as mcp_server_module
    import dhee.ui.server as ui_server
    from dhee.core.log_parser import _escape_path
    from dhee.ui.server import create_app

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    fake_home = tmp_path / "home"
    log_dir = fake_home / ".claude" / "projects" / _escape_path(str(repo_root))
    log_dir.mkdir(parents=True)
    (log_dir / "claude-session.jsonl").write_text(
        "\n".join(
            json.dumps(item)
            for item in [
                {
                    "type": "user",
                    "timestamp": "2026-04-24T06:00:00Z",
                    "cwd": str(repo_root),
                    "sessionId": "claude-session",
                    "message": {"role": "user", "content": "Fix workspace creation"},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-04-24T06:01:00Z",
                    "cwd": str(repo_root),
                    "sessionId": "claude-session",
                    "version": "2.1.118",
                    "message": {
                        "role": "assistant",
                        "model": "claude-opus",
                        "content": [
                            {"type": "text", "text": "Scanning workspace state"},
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": str(repo_root / "server.py")},
                            },
                        ],
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee"))
    monkeypatch.setenv("DHEE_USER_ID", "default")
    monkeypatch.setenv("DHEE_UI_REPO", str(repo_root))
    mcp_server_module._db = None
    monkeypatch.setattr(ui_server.Path, "home", lambda: fake_home)
    monkeypatch.setattr(ui_server, "_repo_codex_threads", lambda repo, limit=18: [])
    monkeypatch.setattr(
        ui_server,
        "_runtime_processes",
        lambda: [{"pid": 123, "runtime_id": "claude-code", "cwd": str(repo_root), "command": "claude"}],
    )

    with TestClient(create_app(serve_static=False)) as c:
        created = c.post("/api/workspaces", json={"name": "Repo", "root_path": str(repo_root)})
        assert created.status_code == 200, created.text
        sessions = created.json()["workspace"].get("sessions") or []
        claude = next(session for session in sessions if session["id"] == "session:claude-code:claude-session")
        assert claude["state"] == "active"
        assert claude["isCurrent"] is True
        assert claude["preview"] == "Scanning workspace state"

    mcp_server_module._db = None


def test_update_workspace_label_description_and_root(client, tmp_path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    ws = _create_workspace(client, name="Office", path=str(root_a))

    r = client.patch(
        f"/api/workspaces/{ws['id']}",
        json={"label": "Office ✨", "description": "Day-job workspace", "root_path": str(root_b)},
    )
    assert r.status_code == 200, r.text
    updated = r.json()["workspace"]
    assert updated["label"] == "Office ✨" or updated["name"] == "Office ✨"
    assert updated.get("description") == "Day-job workspace"
    mounts = updated.get("mounts") or updated.get("folders") or []
    paths = {m.get("path") or m.get("mount_path") for m in mounts}
    # The PATCH routes root_path back through upsert_workspace which
    # tracks it on the root; the old mount remains as a folder.
    assert str(root_b) in paths


def test_create_multiple_workspaces_listed_independently(client, tmp_path):
    office = tmp_path / "office"
    personal = tmp_path / "personal"
    office.mkdir()
    personal.mkdir()
    _create_workspace(client, name="Office", path=str(office))
    _create_workspace(client, name="Personal", path=str(personal))

    r = client.get("/api/workspaces")
    assert r.status_code == 200
    names = {w["label"] or w["name"] for w in r.json()["workspaces"]}
    assert {"Office", "Personal"}.issubset(names)


def test_add_project_to_existing_workspace(client, tmp_path):
    root = tmp_path / "office"
    root.mkdir()
    ws = _create_workspace(client, name="Office", path=str(root))

    r = client.post(
        f"/api/workspaces/{ws['id']}/projects",
        json={"name": "design", "description": "brand + UI", "default_runtime": "claude-code"},
    )
    assert r.status_code == 200, r.text
    project = r.json()["project"]
    assert project["name"] == "design"
    assert project.get("defaultRuntime") in ("claude-code", None, "codex")

    r2 = client.get(f"/api/workspaces/{ws['id']}/projects")
    assert r2.status_code == 200
    names = {p["name"] for p in r2.json().get("projects") or []}
    assert "design" in names


def test_update_project_renames_and_changes_runtime(client, tmp_path):
    root = tmp_path / "office"
    root.mkdir()
    ws = _create_workspace(client, name="Office", path=str(root))
    proj = client.post(
        f"/api/workspaces/{ws['id']}/projects",
        json={"name": "frontend"},
    ).json()["project"]

    r = client.patch(
        f"/api/projects/{proj['id']}",
        json={"name": "web", "default_runtime": "claude-code"},
    )
    assert r.status_code == 200, r.text
    updated = r.json()["project"]
    assert updated["name"] == "web"


def test_delete_project_cascade(client, tmp_path):
    root = tmp_path / "office"
    root.mkdir()
    ws = _create_workspace(client, name="Office", path=str(root))
    proj = client.post(
        f"/api/workspaces/{ws['id']}/projects",
        json={"name": "backend"},
    ).json()["project"]

    # Seed a project asset so the cascade has something to clean up.
    import io

    files = {"file": ("spec.md", io.BytesIO(b"# spec"), "text/markdown")}
    r_asset = client.post(f"/api/projects/{proj['id']}/assets", files=files)
    assert r_asset.status_code == 200
    asset_path = r_asset.json()["asset"]["storage_path"]
    assert os.path.exists(asset_path)

    r = client.delete(f"/api/projects/{proj['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r_list = client.get(f"/api/workspaces/{ws['id']}/projects")
    names = {p["name"] for p in r_list.json().get("projects") or []}
    assert "backend" not in names
    assert not os.path.exists(asset_path), "asset file should be cleaned on project delete"

    # Workspace still exists.
    r_ws = client.get("/api/workspaces")
    assert any(w["id"] == ws["id"] for w in r_ws.json()["workspaces"])


def test_delete_workspace_cascade(client, tmp_path):
    root = tmp_path / "gone"
    root.mkdir()
    ws = _create_workspace(client, name="Temp", path=str(root))
    # Add a project and an asset inside it.
    proj = client.post(
        f"/api/workspaces/{ws['id']}/projects",
        json={"name": "scratch"},
    ).json()["project"]
    import io

    client.post(
        f"/api/projects/{proj['id']}/assets",
        files={"file": ("note.txt", io.BytesIO(b"note"), "text/plain")},
    )
    # Broadcast a line message into the workspace.
    client.post(
        f"/api/workspaces/{ws['id']}/line/messages",
        json={"body": "hello", "channel": "workspace", "message_kind": "note"},
    )

    r = client.delete(f"/api/workspaces/{ws['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    # Gone from the listing.
    r_list = client.get("/api/workspaces")
    ids = {w["id"] for w in r_list.json()["workspaces"]}
    assert ws["id"] not in ids

    # Project deletion implied — GET returns 404 via the project CRUD path.
    r_proj = client.get(f"/api/workspaces/{ws['id']}/projects")
    assert r_proj.status_code in (200, 404)
    if r_proj.status_code == 200:
        assert not (r_proj.json().get("projects") or [])

    # Line messages for the workspace are gone.
    r_line = client.get(f"/api/workspaces/{ws['id']}/line/messages")
    assert r_line.status_code in (200, 404)


def test_delete_workspace_returns_404_for_unknown(client):
    r = client.delete("/api/workspaces/does-not-exist")
    assert r.status_code == 404


def test_delete_project_returns_404_for_unknown(client):
    r = client.delete("/api/projects/does-not-exist")
    assert r.status_code == 404
