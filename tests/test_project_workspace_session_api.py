from __future__ import annotations

from fastapi.testclient import TestClient

from dhee.ui.server import create_app


class FakeRawMemory:
    def search(self, query, **kwargs):
        return {"results": []}


def test_workspace_project_session_and_line_flow(monkeypatch, tmp_path) -> None:
    import dhee.mcp_server as mcp_server
    import dhee.ui.server as ui_server

    repo_root = tmp_path / "repo"
    frontend_root = repo_root / "apps" / "frontend"
    backend_root = repo_root / "services" / "backend"
    docs_root = repo_root / "docs"
    frontend_root.mkdir(parents=True)
    backend_root.mkdir(parents=True)
    docs_root.mkdir(parents=True)

    frontend_file = frontend_root / "README.md"
    frontend_file.write_text("frontend context\n", encoding="utf-8")

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DHEE_UI_REPO", str(repo_root))
    monkeypatch.setattr(mcp_server, "_db", None)
    monkeypatch.setattr(mcp_server, "_memory", None)
    monkeypatch.setattr(mcp_server, "get_memory_instance", lambda: FakeRawMemory())
    monkeypatch.setattr(
        ui_server,
        "_repo_codex_threads",
        lambda repo, limit=18: [
            {
                "id": "codex-backend-live",
                "title": "Backend model update",
                "cwd": str(backend_root),
                "model": "gpt-5.4",
                "messages": [{"id": "m1", "role": "agent", "content": "Updated backend model contract"}],
                "recentTools": ["CodexExec"],
                "plan": [{"step": "Update contract", "status": "completed"}],
                "touchedFiles": [str(frontend_file)],
                "preview": "Updated backend model contract",
                "updatedAt": "2026-04-23T12:00:00Z",
                "isCurrent": True,
            },
            {
                "id": "codex-unassigned",
                "title": "Research spike",
                "cwd": str(repo_root / "experiments"),
                "model": "gpt-5.4",
                "messages": [{"id": "m2", "role": "agent", "content": "Exploring experiments"}],
                "recentTools": ["Read"],
                "plan": [],
                "touchedFiles": [],
                "preview": "Exploring experiments",
                "updatedAt": "2026-04-23T12:05:00Z",
                "isCurrent": False,
            },
        ],
    )
    monkeypatch.setattr(ui_server, "_find_claude_session", lambda repo: None)

    app = create_app(serve_static=False)
    client = TestClient(app)

    created_workspace = client.post(
        "/api/workspaces",
        json={
            "name": "Sankhya AI Labs",
            "description": "Shared collaboration boundary",
            "root_path": str(repo_root),
        },
    )
    assert created_workspace.status_code == 200
    workspace = created_workspace.json()["workspace"]
    workspace_id = workspace["id"]
    assert workspace["rootPath"] == str(repo_root)
    assert workspace["projects"]
    general_project = workspace["projects"][0]

    created_frontend = client.post(
        f"/api/workspaces/{workspace_id}/projects",
        json={
            "name": "Frontend",
            "default_runtime": "claude-code",
            "scope_rules": [{"path_prefix": str(frontend_root), "label": "frontend-root"}],
        },
    )
    assert created_frontend.status_code == 200
    frontend_project = created_frontend.json()["project"]

    created_backend = client.post(
        f"/api/workspaces/{workspace_id}/projects",
        json={
            "name": "Backend",
            "default_runtime": "codex",
            "scope_rules": [{"path_prefix": str(backend_root), "label": "backend-root"}],
        },
    )
    assert created_backend.status_code == 200
    backend_project = created_backend.json()["project"]

    mounted_docs = client.post(
        f"/api/workspaces/{workspace_id}/mounts",
        json={"path": str(docs_root), "label": "docs"},
    )
    assert mounted_docs.status_code == 200
    assert any(folder["path"] == str(docs_root) for folder in mounted_docs.json()["workspace"]["mounts"])

    renamed = client.patch(
        f"/api/workspaces/{workspace_id}",
        json={"label": "Sankhya Core"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["workspace"]["label"] == "Sankhya Core"

    updated_frontend = client.patch(
        f"/api/projects/{frontend_project['id']}",
        json={
            "name": "Frontend UI",
            "default_runtime": "claude-code",
            "scope_rules": [
                {"path_prefix": str(frontend_root), "label": "frontend-root"},
                {"path_prefix": str(repo_root / 'shared-ui'), "label": "shared-ui"},
            ],
        },
    )
    assert updated_frontend.status_code == 200
    assert updated_frontend.json()["project"]["name"] == "Frontend UI"
    assert len(updated_frontend.json()["project"]["scopeRules"]) == 2

    launched = client.post(
        f"/api/workspaces/{workspace_id}/sessions/launch",
        json={
            "runtime": "claude-code",
            "title": "Audit frontend state",
            "permission_mode": "full-access",
            "project_id": frontend_project["id"],
        },
    )
    assert launched.status_code == 200
    launched_body = launched.json()
    session_id = launched_body["session_id"]
    task_id = launched_body["task_id"]
    assert launched_body["workspace_id"] == workspace_id
    assert launched_body["project_id"] == frontend_project["id"]
    assert "--dangerously-skip-permissions" in launched_body["launch_command"]
    assert str(frontend_root) in launched_body["launch_command"]

    detail = client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["workspace"]["id"] == workspace_id
    assert detail_body["project"]["id"] == frontend_project["id"]
    assert detail_body["session"]["taskId"] == task_id

    upload = client.post(
        f"/api/sessions/{session_id}/assets",
        files={"file": ("frontend-notes.txt", b"frontend ux notes", "text/plain")},
        data={"label": "Frontend Notes"},
    )
    assert upload.status_code == 200
    asset_id = upload.json()["asset"]["id"]

    asset_context = client.get(f"/api/assets/{asset_id}/context")
    assert asset_context.status_code == 200
    assert asset_context.json()["asset"]["name"] == "Frontend Notes"

    db = mcp_server.get_db()
    db.save_shared_task_result(
        {
            "shared_task_id": task_id,
            "workspace_id": workspace_id,
            "project_id": frontend_project["id"],
            "result_key": "frontend-readme",
            "packet_kind": "analysis",
            "tool_name": "CodexExec",
            "result_status": "completed",
            "source_path": str(frontend_file),
            "digest": "Frontend README summary.",
            "metadata": {"content_hash": "sha256:frontend"},
        }
    )

    file_context = client.get(
        f"/api/files/{frontend_file}/context",
        params={"workspace_id": workspace_id},
    )
    assert file_context.status_code == 200
    assert file_context.json()["results"][0]["digest"] == "Frontend README summary."

    backend_sessions = client.get(f"/api/projects/{backend_project['id']}/sessions")
    assert backend_sessions.status_code == 200
    backend_session_ids = {session["id"] for session in backend_sessions.json()["sessions"]}
    assert "session:codex:codex-backend-live" in backend_session_ids

    line_publish = client.post(
        f"/api/workspaces/{workspace_id}/line/messages",
        json={
            "project_id": backend_project["id"],
            "target_project_id": frontend_project["id"],
            "session_id": "session:codex:codex-backend-live",
            "message_kind": "broadcast",
            "title": "Model contract changed",
            "body": "Frontend should update the UI to support the new backend model response.",
        },
    )
    assert line_publish.status_code == 200
    publish_body = line_publish.json()
    assert publish_body["message"]["target_project_id"] == frontend_project["id"]
    assert publish_body["suggestedTask"] is not None

    line_messages = client.get(f"/api/workspaces/{workspace_id}/line/messages")
    assert line_messages.status_code == 200
    assert any(
        message["title"] == "Model contract changed"
        for message in line_messages.json()["messages"]
    )

    canvas = client.get(f"/api/workspaces/{workspace_id}/canvas")
    assert canvas.status_code == 200
    canvas_body = canvas.json()
    node_types = {node["type"] for node in canvas_body["graph"]["nodes"]}
    assert {"workspace", "project", "channel", "session", "task", "file", "asset", "broadcast"} <= node_types
    assert canvas_body["currentWorkspaceId"] == workspace_id
    assert any(link["label"] == "targets" for link in canvas_body["graph"]["links"])

    workspace_graph = client.get("/api/workspace/graph", params={"workspace_id": workspace_id})
    assert workspace_graph.status_code == 200
    workspace_graph_body = workspace_graph.json()
    assert workspace_graph_body["currentWorkspaceId"] == workspace_id
    assert any(project["name"] == "Frontend UI" for project in workspace_graph_body["workspace"]["projects"])

    workspaces = client.get("/api/workspaces")
    assert workspaces.status_code == 200
    workspace_names = {workspace["name"] for workspace in workspaces.json()["workspaces"]}
    assert "Sankhya Core" in workspace_names
    assert general_project["id"] in {
        project["id"]
        for workspace in workspaces.json()["workspaces"]
        if workspace["id"] == workspace_id
        for project in workspace["projects"]
    }
