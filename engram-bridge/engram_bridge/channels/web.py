"""Web channel adapter — FastAPI + WebSocket chat interface.

Extended with project/issue/status/tag management for kanban frontend.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from engram_bridge.channels.base import BaseChannel, IncomingMessage

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "web-ui" / "dist"

TASK_STATUSES = ("inbox", "assigned", "active", "review", "blocked", "done")
TASK_PRIORITIES = ("low", "normal", "medium", "high", "urgent")


class TaskStore:
    """In-memory task store with event log, conversation, and process tracking."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._feed: list[dict[str, Any]] = []  # live feed events

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        entry = {"id": str(uuid.uuid4()), "event": event, "ts": datetime.now(timezone.utc).isoformat(), **data}
        self._feed.append(entry)
        if len(self._feed) > 200:
            self._feed = self._feed[-200:]

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        task = {
            "id": task_id,
            "title": data.get("title", "Untitled"),
            "description": data.get("description", ""),
            "priority": data.get("priority", "normal"),
            "status": data.get("status", "inbox"),
            "assigned_agent": data.get("assigned_agent"),
            "tags": data.get("tags", []),
            "created_at": now,
            "updated_at": now,
            "comments": [],
            "conversation": [],
            "processes": [],
            "files_changed": [],
        }
        self._tasks[task_id] = task
        self._emit("task_created", {"task_id": task_id, "title": task["title"]})
        return task

    def get_all(self) -> list[dict[str, Any]]:
        # Return tasks without heavy conversation/process data in list view
        result = []
        for t in self._tasks.values():
            summary = {k: v for k, v in t.items() if k not in ("conversation", "processes", "files_changed")}
            summary["conversation"] = []
            summary["processes"] = []
            summary["files_changed"] = []
            result.append(summary)
        return result

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)

    def get_detail(self, task_id: str) -> dict[str, Any] | None:
        """Get full task data including conversation, processes, and files."""
        return self._tasks.get(task_id)

    def update(self, task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        old_status = task.get("status")
        for key in ("title", "description", "priority", "status", "assigned_agent", "tags"):
            if key in data:
                task[key] = data[key]
        task["updated_at"] = datetime.now(timezone.utc).isoformat()
        if data.get("status") and data["status"] != old_status:
            self._emit("task_moved", {
                "task_id": task_id, "title": task["title"],
                "from": old_status, "to": data["status"],
                "agent": task.get("assigned_agent"),
            })
        if data.get("assigned_agent") and data["assigned_agent"] != task.get("assigned_agent"):
            self._emit("task_assigned", {
                "task_id": task_id, "title": task["title"],
                "agent": data["assigned_agent"],
            })
        return task

    def add_conversation_entry(self, task_id: str, entry: dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        entry.setdefault("id", str(uuid.uuid4())[:8])
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        task["conversation"].append(entry)

    def add_process(self, task_id: str, process: dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        process.setdefault("id", str(uuid.uuid4())[:8])
        process.setdefault("started_at", datetime.now(timezone.utc).isoformat())
        task["processes"].append(process)

    def update_process(self, task_id: str, process_id: str, data: dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        for p in task["processes"]:
            if p["id"] == process_id:
                p.update(data)
                break

    def add_file_change(self, task_id: str, change: dict[str, Any]) -> None:
        task = self._tasks.get(task_id)
        if not task:
            return
        change.setdefault("ts", datetime.now(timezone.utc).isoformat())
        task["files_changed"].append(change)

    def add_comment(self, task_id: str, agent: str, text: str) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        if not task:
            return None
        comment = {
            "id": str(uuid.uuid4())[:8],
            "agent": agent,
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        task["comments"].append(comment)
        self._emit("comment", {"task_id": task_id, "title": task["title"], "agent": agent, "text": text[:100]})
        return comment

    def delete(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    def get_feed(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._feed[-limit:]


class EngramTaskStore:
    """Persistent task store backed by Engram Memory via TaskManager.

    Implements the same interface as TaskStore so WebChannel can use either.
    Event feed stays ephemeral (UI activity only).
    """

    def __init__(self, memory: Any, user_id: str = "default") -> None:
        from engram.memory.tasks import TaskManager
        self._tm = TaskManager(memory)
        self._user_id = user_id
        self._feed: list[dict[str, Any]] = []
        self._bus = None  # set by Bridge.start() for coordination events

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        entry = {"id": str(uuid.uuid4()), "event": event, "ts": datetime.now(timezone.utc).isoformat(), **data}
        self._feed.append(entry)
        if len(self._feed) > 200:
            self._feed = self._feed[-200:]

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        task = self._tm.create_task(
            title=data.get("title", "Untitled"),
            description=data.get("description", ""),
            priority=data.get("priority", "normal"),
            status=data.get("status", "inbox"),
            assignee=data.get("assigned_agent"),
            tags=data.get("tags", []),
            user_id=self._user_id,
            extra_metadata=data.get("metadata"),
            project_id=data.get("project_id", "default"),
            status_id=data.get("status_id"),
            assignee_ids=data.get("assignee_ids", []),
            tag_ids=data.get("tag_ids", []),
            start_date=data.get("start_date"),
            target_date=data.get("target_date"),
            parent_task_id=data.get("parent_task_id"),
            sort_order=data.get("sort_order", 0),
            issue_number=data.get("issue_number"),
        )
        self._emit("task_created", {"task_id": task.get("id", ""), "title": task.get("title", "")})
        # Publish bus event for coordination auto-routing
        if self._bus:
            self._bus.publish("bridge.task.created", {
                "task_id": task.get("id", ""),
                "title": task.get("title", ""),
            })
        return task

    def get_all(self) -> list[dict[str, Any]]:
        tasks = self._tm.list_tasks(user_id=self._user_id, limit=200)
        # Strip heavy data for list view (match TaskStore behavior)
        result = []
        for t in tasks:
            summary = dict(t)
            summary["conversation"] = []
            summary["processes"] = []
            summary["files_changed"] = []
            result.append(summary)
        return result

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tm.get_task(task_id)

    def get_detail(self, task_id: str) -> dict[str, Any] | None:
        return self._tm.get_task(task_id)

    def update(self, task_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        result = self._tm.update_task(task_id, data)
        if result:
            old_status = data.get("_old_status")  # caller may provide
            new_status = data.get("status")
            if new_status and new_status != old_status:
                self._emit("task_moved", {
                    "task_id": task_id, "title": result.get("title", ""),
                    "from": old_status, "to": new_status,
                    "agent": result.get("assigned_agent"),
                })
            if data.get("assigned_agent"):
                self._emit("task_assigned", {
                    "task_id": task_id, "title": result.get("title", ""),
                    "agent": data["assigned_agent"],
                })
        return result

    def add_conversation_entry(self, task_id: str, entry: dict[str, Any]) -> None:
        self._tm.add_conversation_entry(task_id, entry)

    def add_process(self, task_id: str, process: dict[str, Any]) -> None:
        self._tm.add_process(task_id, process)

    def update_process(self, task_id: str, process_id: str, data: dict[str, Any]) -> None:
        # Get task, find process, update in-place, save back
        task = self._tm.get_task(task_id)
        if not task:
            return
        for p in task.get("processes", []):
            if p.get("id") == process_id:
                p.update(data)
                break
        # Re-save via low-level update
        mem = self._tm.memory.get(task_id)
        if mem:
            md = self._tm._parse_metadata(mem)
            for p in md.get("task_processes", []):
                if p.get("id") == process_id:
                    p.update(data)
                    break
            self._tm.memory.db.update_memory(task_id, {"metadata": md})

    def add_file_change(self, task_id: str, change: dict[str, Any]) -> None:
        self._tm.add_file_change(task_id, change)

    def add_comment(self, task_id: str, agent: str, text: str) -> dict[str, Any] | None:
        result = self._tm.add_comment(task_id, agent, text)
        if result:
            self._emit("comment", {
                "task_id": task_id, "title": result.get("title", ""),
                "agent": agent, "text": text[:100],
            })
            # Return just the last comment for API compat
            comments = result.get("comments", [])
            return comments[-1] if comments else None
        return None

    def delete(self, task_id: str) -> bool:
        result = self._tm.update_task(task_id, {"status": "archived"})
        return result is not None

    def get_feed(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._feed[-limit:]

    # -- Project-aware extensions --

    def list_by_project(self, project_id: str) -> list[dict[str, Any]]:
        tasks = self._tm.list_tasks_by_project(project_id, user_id=self._user_id)
        result = []
        for t in tasks:
            summary = dict(t)
            summary["conversation"] = []
            summary["processes"] = []
            summary["files_changed"] = []
            result.append(summary)
        return result

    def bulk_update(self, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._tm.bulk_update_tasks(updates)

    def get_sub_tasks(self, parent_task_id: str) -> list[dict[str, Any]]:
        return self._tm.get_sub_tasks(parent_task_id, user_id=self._user_id)


class WebChannel(BaseChannel):
    """WebSocket-based chat channel served via FastAPI."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8200,
        auth_token: str = "",
        allowed_users: list[int] | None = None,
    ):
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._allowed_users = set(allowed_users) if allowed_users else set()

        self._on_message: Callable[[IncomingMessage], Awaitable[None]] | None = None
        self._on_stats_request: Callable[[int], Awaitable[None]] | None = None
        self._get_agents_info: Callable[[], list[dict[str, Any]]] | None = None
        self._connections: dict[int, WebSocket] = {}
        self._message_counter = 0
        self._server_task: asyncio.Task | None = None
        self._server = None
        self.tasks = TaskStore()
        self._project_manager = None  # set when Engram memory is available
        self._memory = None
        self._coordinator = None  # set by Bridge.start() when coordination enabled

        self.app = FastAPI(title="engram-bridge", docs_url=None, redoc_url=None)
        self._setup_routes()

    def set_memory(self, memory: Any) -> None:
        """Configure Engram memory for persistent storage and project management."""
        from engram.memory.projects import ProjectManager
        self._memory = memory
        self._project_manager = ProjectManager(memory)
        self.tasks = EngramTaskStore(memory)

    def _next_message_id(self) -> int:
        self._message_counter += 1
        return self._message_counter

    def _user_id_for(self, ws: WebSocket) -> int:
        return hash(ws) & 0x7FFFFFFF

    def _broadcast_feed(self, event: dict[str, Any]) -> None:
        """Push a feed event to all connected WebSocket clients."""
        msg = json.dumps({"type": "feed_event", **event})
        for ws in self._connections.values():
            asyncio.create_task(self._safe_send(ws, msg))

    def _broadcast_ws(self, msg_type: str, data: dict[str, Any]) -> None:
        """Broadcast a typed message to all WebSocket clients."""
        msg = json.dumps({"type": msg_type, **data})
        for ws in self._connections.values():
            asyncio.create_task(self._safe_send(ws, msg))

    async def _safe_send(self, ws: WebSocket, text: str) -> None:
        try:
            await ws.send_text(text)
        except Exception:
            pass

    def _setup_routes(self) -> None:
        app = self.app
        channel = self  # closure ref

        # ── REST API — Legacy task endpoints ──

        @app.get("/api/agents")
        async def get_agents():
            if channel._get_agents_info:
                return JSONResponse(channel._get_agents_info())
            return JSONResponse([])

        @app.get("/api/tasks")
        async def get_tasks():
            return JSONResponse(channel.tasks.get_all())

        @app.post("/api/tasks")
        async def create_task(request: Request):
            data = await request.json()
            task = channel.tasks.create(data)
            feed = channel.tasks._feed[-1] if channel.tasks._feed else None
            if feed:
                channel._broadcast_feed(feed)
            return JSONResponse(task, status_code=201)

        @app.put("/api/tasks/{task_id}")
        async def update_task(task_id: str, request: Request):
            data = await request.json()
            task = channel.tasks.update(task_id, data)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            feed = channel.tasks._feed[-1] if channel.tasks._feed else None
            if feed:
                channel._broadcast_feed(feed)
            return JSONResponse(task)

        @app.post("/api/tasks/{task_id}/comments")
        async def add_task_comment(task_id: str, request: Request):
            data = await request.json()
            comment = channel.tasks.add_comment(task_id, data.get("agent", "user"), data.get("text", ""))
            if not comment:
                return JSONResponse({"error": "Not found"}, status_code=404)
            feed = channel.tasks._feed[-1] if channel.tasks._feed else None
            if feed:
                channel._broadcast_feed(feed)
            return JSONResponse(comment, status_code=201)

        @app.delete("/api/tasks/{task_id}")
        async def delete_task(task_id: str):
            if channel.tasks.delete(task_id):
                return JSONResponse({"ok": True})
            return JSONResponse({"error": "Not found"}, status_code=404)

        @app.get("/api/tasks/{task_id}/detail")
        async def get_task_detail(task_id: str):
            task = channel.tasks.get_detail(task_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(task)

        @app.get("/api/feed")
        async def get_feed():
            return JSONResponse(channel.tasks.get_feed())

        @app.get("/health")
        async def health():
            return JSONResponse({"status": "ok", "connections": len(channel._connections)})

        @app.get("/api/info")
        async def system_info():
            return JSONResponse({
                "version": "2.0.0",
                "has_memory": channel._memory is not None,
                "has_projects": channel._project_manager is not None,
                "connections": len(channel._connections),
            })

        # ── REST API — Project endpoints ──

        @app.get("/api/projects")
        async def list_projects():
            if not channel._project_manager:
                return JSONResponse([])
            return JSONResponse(channel._project_manager.list_projects())

        @app.post("/api/projects")
        async def create_project(request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            project = channel._project_manager.create_project(
                name=data.get("name", "Untitled"),
                color=data.get("color", "#6366f1"),
                description=data.get("description", ""),
            )
            # Auto-create default statuses
            channel._project_manager.ensure_default_statuses(project["id"])
            channel._broadcast_ws("project_created", {"project": project})
            return JSONResponse(project, status_code=201)

        @app.get("/api/projects/{project_id}")
        async def get_project(project_id: str):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            project = channel._project_manager.get_project(project_id)
            if not project:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(project)

        @app.put("/api/projects/{project_id}")
        async def update_project(project_id: str, request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            project = channel._project_manager.update_project(project_id, data)
            if not project:
                return JSONResponse({"error": "Not found"}, status_code=404)
            channel._broadcast_ws("project_updated", {"project": project})
            return JSONResponse(project)

        @app.delete("/api/projects/{project_id}")
        async def delete_project(project_id: str):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            if channel._project_manager.delete_project(project_id):
                channel._broadcast_ws("project_deleted", {"project_id": project_id})
                return JSONResponse({"ok": True})
            return JSONResponse({"error": "Not found"}, status_code=404)

        # ── REST API — Status endpoints ──

        @app.get("/api/projects/{project_id}/statuses")
        async def list_statuses(project_id: str):
            if not channel._project_manager:
                return JSONResponse([])
            return JSONResponse(channel._project_manager.list_statuses(project_id))

        @app.post("/api/projects/{project_id}/statuses")
        async def create_status(project_id: str, request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            status = channel._project_manager.create_status(
                project_id=project_id,
                name=data.get("name", "New Status"),
                color=data.get("color", "#94a3b8"),
                sort_order=data.get("sort_order", 0),
                hidden=data.get("hidden", False),
            )
            channel._broadcast_ws("status_changed", {"project_id": project_id})
            return JSONResponse(status, status_code=201)

        @app.put("/api/statuses/{status_id}")
        async def update_status(status_id: str, request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            status = channel._project_manager.update_status(status_id, data)
            if not status:
                return JSONResponse({"error": "Not found"}, status_code=404)
            channel._broadcast_ws("status_changed", {"project_id": status.get("project_id", "")})
            return JSONResponse(status)

        @app.delete("/api/statuses/{status_id}")
        async def delete_status(status_id: str):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            if channel._project_manager.delete_status(status_id):
                return JSONResponse({"ok": True})
            return JSONResponse({"error": "Not found"}, status_code=404)

        @app.post("/api/statuses/bulk")
        async def bulk_update_statuses(request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            results = channel._project_manager.bulk_update_statuses(data.get("updates", []))
            return JSONResponse(results)

        # ── REST API — Tag endpoints ──

        @app.get("/api/projects/{project_id}/tags")
        async def list_tags(project_id: str):
            if not channel._project_manager:
                return JSONResponse([])
            return JSONResponse(channel._project_manager.list_tags(project_id))

        @app.post("/api/projects/{project_id}/tags")
        async def create_tag(project_id: str, request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            tag = channel._project_manager.create_tag(
                project_id=project_id,
                name=data.get("name", "tag"),
                color=data.get("color", "#6366f1"),
            )
            return JSONResponse(tag, status_code=201)

        @app.put("/api/tags/{tag_id}")
        async def update_tag(tag_id: str, request: Request):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            data = await request.json()
            tag = channel._project_manager.update_tag(tag_id, data)
            if not tag:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(tag)

        @app.delete("/api/tags/{tag_id}")
        async def delete_tag(tag_id: str):
            if not channel._project_manager:
                return JSONResponse({"error": "No memory configured"}, status_code=503)
            if channel._project_manager.delete_tag(tag_id):
                return JSONResponse({"ok": True})
            return JSONResponse({"error": "Not found"}, status_code=404)

        # ── REST API — Issue/Task endpoints (project-aware) ──

        @app.get("/api/projects/{project_id}/issues")
        async def list_issues(project_id: str):
            if isinstance(channel.tasks, EngramTaskStore):
                return JSONResponse(channel.tasks.list_by_project(project_id))
            return JSONResponse(channel.tasks.get_all())

        @app.post("/api/issues")
        async def create_issue(request: Request):
            data = await request.json()
            # Auto-assign issue number if project manager is available
            project_id = data.get("project_id", "default")
            if channel._project_manager and project_id != "default":
                issue_num = channel._project_manager.next_issue_number(project_id)
                data["issue_number"] = issue_num
            task = channel.tasks.create(data)
            channel._broadcast_ws("issue_created", {"issue": task})
            feed = channel.tasks._feed[-1] if channel.tasks._feed else None
            if feed:
                channel._broadcast_feed(feed)
            return JSONResponse(task, status_code=201)

        @app.get("/api/issues/{issue_id}")
        async def get_issue(issue_id: str):
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(task)

        @app.put("/api/issues/{issue_id}")
        async def update_issue(issue_id: str, request: Request):
            data = await request.json()
            task = channel.tasks.update(issue_id, data)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            channel._broadcast_ws("issue_updated", {"issue": task})
            return JSONResponse(task)

        @app.delete("/api/issues/{issue_id}")
        async def delete_issue(issue_id: str):
            if channel.tasks.delete(issue_id):
                channel._broadcast_ws("issue_deleted", {"issue_id": issue_id})
                return JSONResponse({"ok": True})
            return JSONResponse({"error": "Not found"}, status_code=404)

        @app.post("/api/issues/bulk")
        async def bulk_update_issues(request: Request):
            data = await request.json()
            if isinstance(channel.tasks, EngramTaskStore):
                results = channel.tasks.bulk_update(data.get("updates", []))
                channel._broadcast_ws("issues_bulk_updated", {})
                return JSONResponse(results)
            return JSONResponse([])

        # ── Comments on issues ──

        @app.get("/api/issues/{issue_id}/comments")
        async def list_issue_comments(issue_id: str):
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return JSONResponse(task.get("comments", []))

        @app.post("/api/issues/{issue_id}/comments")
        async def add_issue_comment(issue_id: str, request: Request):
            data = await request.json()
            comment = channel.tasks.add_comment(issue_id, data.get("agent", "user"), data.get("text", ""))
            if not comment:
                return JSONResponse({"error": "Not found"}, status_code=404)
            channel._broadcast_ws("comment_added", {"issue_id": issue_id, "comment": comment})
            return JSONResponse(comment, status_code=201)

        @app.put("/api/comments/{comment_id}")
        async def update_comment(comment_id: str, request: Request):
            data = await request.json()
            issue_id = data.get("issue_id", "")
            if not issue_id:
                return JSONResponse({"error": "issue_id required"}, status_code=400)
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            # Update comment in place via TaskManager
            if isinstance(channel.tasks, EngramTaskStore):
                mem = channel.tasks._tm.memory.get(issue_id)
                if mem:
                    md = channel.tasks._tm._parse_metadata(mem)
                    for c in md.get("task_comments", []):
                        if c.get("id") == comment_id:
                            if "text" in data:
                                c["text"] = data["text"]
                            break
                    channel.tasks._tm.memory.db.update_memory(issue_id, {"metadata": md})
            return JSONResponse({"ok": True})

        @app.delete("/api/comments/{comment_id}")
        async def delete_comment(comment_id: str, request: Request):
            issue_id = request.query_params.get("issue_id", "")
            if not issue_id:
                return JSONResponse({"error": "issue_id required"}, status_code=400)
            if isinstance(channel.tasks, EngramTaskStore):
                mem = channel.tasks._tm.memory.get(issue_id)
                if mem:
                    md = channel.tasks._tm._parse_metadata(mem)
                    md["task_comments"] = [c for c in md.get("task_comments", []) if c.get("id") != comment_id]
                    channel.tasks._tm.memory.db.update_memory(issue_id, {"metadata": md})
            return JSONResponse({"ok": True})

        # ── Reactions ──

        @app.post("/api/comments/{comment_id}/reactions")
        async def add_reaction(comment_id: str, request: Request):
            data = await request.json()
            issue_id = data.get("issue_id", "")
            if not issue_id:
                return JSONResponse({"error": "issue_id required"}, status_code=400)
            if isinstance(channel.tasks, EngramTaskStore):
                channel.tasks._tm.add_reaction(issue_id, comment_id, data.get("user_id", "user"), data.get("emoji", ""))
            return JSONResponse({"ok": True}, status_code=201)

        @app.delete("/api/comments/{comment_id}/reactions/{emoji}")
        async def remove_reaction(comment_id: str, emoji: str, request: Request):
            issue_id = request.query_params.get("issue_id", "")
            user_id = request.query_params.get("user_id", "user")
            if not issue_id:
                return JSONResponse({"error": "issue_id required"}, status_code=400)
            if isinstance(channel.tasks, EngramTaskStore):
                channel.tasks._tm.remove_reaction(issue_id, comment_id, user_id, emoji)
            return JSONResponse({"ok": True})

        # ── Relationships ──

        @app.get("/api/issues/{issue_id}/relationships")
        async def list_relationships(issue_id: str):
            if isinstance(channel.tasks, EngramTaskStore):
                return JSONResponse(channel.tasks._tm.get_relationships(issue_id))
            return JSONResponse([])

        @app.post("/api/issues/{issue_id}/relationships")
        async def add_relationship(issue_id: str, request: Request):
            data = await request.json()
            if isinstance(channel.tasks, EngramTaskStore):
                channel.tasks._tm.add_relationship(
                    issue_id, data.get("related_id", ""), data.get("type", "related"),
                )
            return JSONResponse({"ok": True}, status_code=201)

        @app.delete("/api/relationships/{relationship_id}")
        async def remove_relationship(relationship_id: str, request: Request):
            issue_id = request.query_params.get("issue_id", "")
            if issue_id and isinstance(channel.tasks, EngramTaskStore):
                channel.tasks._tm.remove_relationship(issue_id, relationship_id)
            return JSONResponse({"ok": True})

        # ── Assignees ──

        @app.post("/api/issues/{issue_id}/assignees")
        async def add_assignee(issue_id: str, request: Request):
            data = await request.json()
            uid = data.get("user_id", "")
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            ids = list(task.get("assignee_ids", []))
            if uid and uid not in ids:
                ids.append(uid)
            channel.tasks.update(issue_id, {"assignee_ids": ids})
            return JSONResponse({"ok": True}, status_code=201)

        @app.delete("/api/issues/{issue_id}/assignees/{uid}")
        async def remove_assignee(issue_id: str, uid: str):
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            ids = [a for a in task.get("assignee_ids", []) if a != uid]
            channel.tasks.update(issue_id, {"assignee_ids": ids})
            return JSONResponse({"ok": True})

        # ── Tags on issues ──

        @app.post("/api/issues/{issue_id}/tags")
        async def add_issue_tag(issue_id: str, request: Request):
            data = await request.json()
            tag_id = data.get("tag_id", "")
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            ids = list(task.get("tag_ids", []))
            if tag_id and tag_id not in ids:
                ids.append(tag_id)
            channel.tasks.update(issue_id, {"tag_ids": ids})
            return JSONResponse({"ok": True}, status_code=201)

        @app.delete("/api/issues/{issue_id}/tags/{tag_id}")
        async def remove_issue_tag(issue_id: str, tag_id: str):
            task = channel.tasks.get_detail(issue_id)
            if not task:
                return JSONResponse({"error": "Not found"}, status_code=404)
            ids = [t for t in task.get("tag_ids", []) if t != tag_id]
            channel.tasks.update(issue_id, {"tag_ids": ids})
            return JSONResponse({"ok": True})

        # ── Sub-issues ──

        @app.get("/api/issues/{issue_id}/sub-issues")
        async def list_sub_issues(issue_id: str):
            if isinstance(channel.tasks, EngramTaskStore):
                return JSONResponse(channel.tasks.get_sub_tasks(issue_id))
            return JSONResponse([])

        # ── REST API — Memory endpoints ──

        _MEM_USER = "default"  # scope all memory queries to the default user

        def _clean_memory(m: dict) -> dict:
            """Strip embedding vectors and normalise fields for the frontend."""
            out = {k: v for k, v in m.items() if k != "embedding"}
            md = out.get("metadata") or {}
            out.setdefault("echo_depth", md.get("echo_depth", "none"))
            out.setdefault("echo_encodings", md.get("echo_encodings"))
            return out

        @app.get("/api/memory/stats")
        async def memory_stats():
            if channel._memory is None:
                return JSONResponse({"error": "Memory not configured"}, status_code=503)
            try:
                stats = channel._memory.get_stats(user_id=_MEM_USER)
                return JSONResponse(stats)
            except Exception as exc:
                logger.exception("memory stats error")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.get("/api/memory/search")
        async def memory_search(q: str = Query(default=""), limit: int = Query(default=20)):
            if channel._memory is None:
                return JSONResponse({"error": "Memory not configured"}, status_code=503)
            if not q.strip():
                return JSONResponse([])
            try:
                result = channel._memory.search(q.strip(), user_id=_MEM_USER, limit=limit)
                items = result.get("results", [])
                return JSONResponse([_clean_memory(m) for m in items])
            except Exception as exc:
                logger.exception("memory search error")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.get("/api/memory/all")
        async def memory_all(
            limit: int = Query(default=50),
            layer: str = Query(default=""),
            category: str = Query(default=""),
        ):
            if channel._memory is None:
                return JSONResponse({"error": "Memory not configured"}, status_code=503)
            try:
                result = channel._memory.get_all(user_id=_MEM_USER, limit=limit)
                memories = result.get("results", []) if isinstance(result, dict) else result
                # Apply optional filters
                if layer:
                    memories = [m for m in memories if m.get("layer") == layer]
                if category:
                    memories = [
                        m for m in memories
                        if category in (m.get("categories") or [])
                        or category in (m.get("metadata", {}).get("categories") or [])
                    ]
                # Return newest first, limited
                memories.sort(key=lambda m: m.get("updated_at", m.get("created_at", "")), reverse=True)
                return JSONResponse([_clean_memory(m) for m in memories[:limit]])
            except Exception as exc:
                logger.exception("memory all error")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.get("/api/memory/categories")
        async def memory_categories():
            if channel._memory is None:
                return JSONResponse({"error": "Memory not configured"}, status_code=503)
            try:
                categories = channel._memory.get_categories()
                return JSONResponse(categories)
            except Exception as exc:
                logger.exception("memory categories error")
                return JSONResponse({"error": str(exc)}, status_code=500)

        @app.get("/api/memory/{memory_id}")
        async def memory_get(memory_id: str):
            if channel._memory is None:
                return JSONResponse({"error": "Memory not configured"}, status_code=503)
            try:
                mem = channel._memory.get(memory_id)
                if not mem:
                    return JSONResponse({"error": "Not found"}, status_code=404)
                return JSONResponse(_clean_memory(mem))
            except Exception as exc:
                logger.exception("memory get error")
                return JSONResponse({"error": str(exc)}, status_code=500)

        # ── REST API — Coordination endpoints ──

        @app.get("/api/coordination/agents")
        async def coordination_list_agents():
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            agents = channel._coordinator.registry.list()
            return JSONResponse(agents)

        @app.post("/api/coordination/agents/{name}/register")
        async def coordination_register_agent(name: str, request: Request):
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            data = await request.json()
            result = channel._coordinator.registry.register(
                name,
                capabilities=data.get("capabilities", ["general"]),
                description=data.get("description", f"{name} agent"),
                agent_type=data.get("agent_type", "custom"),
                model=data.get("model", ""),
                max_concurrent=data.get("max_concurrent", 1),
            )
            return JSONResponse(result)

        @app.get("/api/coordination/agents/match")
        async def coordination_match_agents(q: str = Query(default="")):
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            if not q.strip():
                return JSONResponse([])
            agents = channel._coordinator.registry.find_capable(q.strip(), limit=5)
            return JSONResponse(agents)

        @app.post("/api/coordination/route/{task_id}")
        async def coordination_route_task(task_id: str, request: Request):
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            body = {}
            try:
                body = await request.json()
            except Exception:
                pass
            force = body.get("force", False)
            result = channel._coordinator.router.route(task_id, force=force)
            if result:
                channel._broadcast_ws("task_routed", {
                    "task_id": task_id,
                    "agent": result.get("assigned_agent", ""),
                })
                return JSONResponse(result)
            return JSONResponse({"error": "No suitable agent found"}, status_code=404)

        @app.post("/api/coordination/route-pending")
        async def coordination_route_pending():
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            routed = channel._coordinator.router.route_pending()
            for task in routed:
                channel._broadcast_ws("task_routed", {
                    "task_id": task.get("id", ""),
                    "agent": task.get("assigned_agent", ""),
                })
            return JSONResponse({"routed": len(routed), "tasks": routed})

        @app.post("/api/coordination/claim/{task_id}")
        async def coordination_claim_task(task_id: str, request: Request):
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            data = await request.json()
            agent_name = data.get("agent_name", "")
            if not agent_name:
                return JSONResponse({"error": "agent_name required"}, status_code=400)
            result = channel._coordinator.claim(task_id, agent_name)
            if result:
                channel._broadcast_ws("task_claimed", {
                    "task_id": task_id,
                    "agent": agent_name,
                })
                return JSONResponse(result)
            return JSONResponse({"error": "Claim failed (already claimed or invalid status)"}, status_code=409)

        @app.get("/api/coordination/events")
        async def coordination_events(limit: int = Query(default=50)):
            if not channel._coordinator:
                return JSONResponse({"error": "Coordination not enabled"}, status_code=503)
            results = channel._coordinator._memory.get_all(
                user_id="system",
                filters={"memory_type": "coordination_event"},
                limit=limit,
            )
            items = results.get("results", []) if isinstance(results, dict) else results
            events = []
            for item in items:
                md = item.get("metadata", {})
                events.append({
                    "id": item.get("id", ""),
                    "type": md.get("coord_event_type", ""),
                    "timestamp": md.get("coord_timestamp", ""),
                    "details": md.get("coord_details", {}),
                    "content": item.get("memory", item.get("content", "")),
                })
            events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
            return JSONResponse(events[:limit])

        # ── WebSocket ──

        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket, token: str = Query(default="")):
            if self._auth_token:
                if not token or not secrets.compare_digest(token, self._auth_token):
                    await ws.close(code=4001, reason="Unauthorized")
                    return

            await ws.accept()
            user_id = self._user_id_for(ws)
            self._connections[user_id] = ws

            await ws.send_json({"type": "connected", "user_id": user_id})
            logger.info("WebSocket connected: user_id=%d", user_id)

            try:
                while True:
                    raw = await ws.receive_text()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "content": "Invalid JSON"})
                        continue

                    msg_type = data.get("type", "message")

                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                        continue

                    if msg_type == "stats_request":
                        if self._on_stats_request:
                            asyncio.create_task(self._on_stats_request(user_id))
                        continue

                    # Task route: request coordination routing via WebSocket
                    if msg_type == "task_route":
                        task_id = data.get("task_id", "")
                        if task_id and self._coordinator:
                            result = self._coordinator.on_task_created({"task_id": task_id})
                            if result and result.get("assigned_agent"):
                                self._broadcast_ws("task_routed", {
                                    "task_id": task_id,
                                    "agent": result["assigned_agent"],
                                    "title": result.get("title", ""),
                                })
                            else:
                                await ws.send_json({
                                    "type": "task_route_failed",
                                    "task_id": task_id,
                                    "reason": "No suitable agent found",
                                })
                        elif not self._coordinator:
                            await ws.send_json({
                                "type": "error",
                                "content": "Coordination not enabled",
                            })
                        continue

                    # Task execution: dispatch to agent via bridge
                    if msg_type == "task_execute":
                        task_id = data.get("task_id", "")
                        agent_name = data.get("agent", "")
                        prompt = data.get("prompt", "")
                        if task_id and prompt:
                            # Store the conversation entry
                            self.tasks.add_conversation_entry(task_id, {
                                "type": "user", "content": prompt,
                            })
                            text = prompt
                            incoming = IncomingMessage(
                                user_id=user_id,
                                chat_id=user_id,
                                text=text,
                                username=f"web_{user_id}",
                                is_command=False,
                                metadata={"task_id": task_id, "agent": agent_name},
                            )
                            if self._on_message:
                                asyncio.create_task(self._on_message(incoming))
                        continue

                    # Task follow-up: send additional message in task context
                    if msg_type == "task_followup":
                        task_id = data.get("task_id", "")
                        follow_text = data.get("text", "").strip()
                        if task_id and follow_text:
                            self.tasks.add_conversation_entry(task_id, {
                                "type": "user", "content": follow_text,
                            })
                            incoming = IncomingMessage(
                                user_id=user_id,
                                chat_id=user_id,
                                text=follow_text,
                                username=f"web_{user_id}",
                                is_command=False,
                                metadata={"task_id": task_id},
                            )
                            if self._on_message:
                                asyncio.create_task(self._on_message(incoming))
                        continue

                    text = data.get("text", "").strip()
                    if not text:
                        continue

                    if msg_type == "command" or text.startswith("/"):
                        parts = text.split()
                        command = parts[0].lstrip("/")
                        args = parts[1:] if len(parts) > 1 else []
                        incoming = IncomingMessage(
                            user_id=user_id,
                            chat_id=user_id,
                            text=text,
                            username=f"web_{user_id}",
                            is_command=True,
                            command=command,
                            command_args=args,
                        )
                    else:
                        incoming = IncomingMessage(
                            user_id=user_id,
                            chat_id=user_id,
                            text=text,
                            username=f"web_{user_id}",
                            is_command=False,
                        )

                    if self._on_message:
                        asyncio.create_task(self._on_message(incoming))

            except WebSocketDisconnect:
                logger.info("WebSocket disconnected: user_id=%d", user_id)
            except Exception as e:
                logger.error("WebSocket error for user_id=%d: %s", user_id, e)
            finally:
                self._connections.pop(user_id, None)

        # ── SPA static files ──

        if _STATIC_DIR.exists():
            assets_dir = _STATIC_DIR / "assets"
            if assets_dir.exists():
                app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

            @app.get("/{full_path:path}")
            async def serve_spa(full_path: str = ""):
                index = _STATIC_DIR / "index.html"
                if index.exists():
                    return FileResponse(str(index))
                return JSONResponse(
                    {"error": "Frontend not built. Run: cd engram_bridge/channels/web-ui && npm run build"},
                    status_code=404,
                )
        else:
            @app.get("/")
            async def no_frontend():
                return JSONResponse(
                    {"error": "Frontend not built. Run: cd engram_bridge/channels/web-ui && npm run build"},
                    status_code=404,
                )

    # ── BaseChannel interface ──

    async def start(self, on_message: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        self._on_message = on_message

        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self._host,
            port=self._port,
            log_level="info",
            ws_ping_interval=30,
            ws_ping_timeout=10,
        )
        self._server = uvicorn.Server(config)

        self._server_task = asyncio.create_task(self._server.serve())
        logger.info("Web channel started at http://%s:%d", self._host, self._port)

    async def send_text(self, chat_id: int, text: str) -> int:
        ws = self._connections.get(chat_id)
        if not ws:
            logger.warning("No WebSocket connection for chat_id=%d", chat_id)
            return 0

        msg_id = self._next_message_id()
        try:
            await ws.send_json({
                "type": "text",
                "message_id": msg_id,
                "content": text,
            })
        except Exception as e:
            logger.warning("Failed to send text to chat_id=%d: %s", chat_id, e)
        return msg_id

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        ws = self._connections.get(chat_id)
        if not ws:
            return
        try:
            await ws.send_json({
                "type": "edit",
                "message_id": message_id,
                "content": text,
            })
        except Exception as e:
            logger.warning("Failed to edit message %d: %s", message_id, e)

    async def send_stats(self, chat_id: int, data: dict) -> None:
        """Send structured stats data to a client (web-only extension)."""
        ws = self._connections.get(chat_id)
        if not ws:
            return
        try:
            await ws.send_json({"type": "stats", **data})
        except Exception as e:
            logger.warning("Failed to send stats to chat_id=%d: %s", chat_id, e)

    async def send_task_update(self, chat_id: int, task_id: str, update_type: str, data: dict) -> None:
        """Send a task-specific update to a client (conversation entry, process, file change)."""
        ws = self._connections.get(chat_id)
        if not ws:
            return
        try:
            await ws.send_json({
                "type": update_type,
                "task_id": task_id,
                **data,
            })
        except Exception as e:
            logger.warning("Failed to send task update to chat_id=%d: %s", chat_id, e)

        # Persist in task store
        if update_type == "task_text":
            self.tasks.add_conversation_entry(task_id, {
                "type": "assistant",
                "content": data.get("content", ""),
                "agent": data.get("agent"),
                "message_id": data.get("message_id"),
            })
        elif update_type == "task_tool_use":
            self.tasks.add_conversation_entry(task_id, {
                "type": "tool_use",
                "content": data.get("content", ""),
                "tool": data.get("tool"),
                "file_path": data.get("file_path"),
            })
        elif update_type == "task_error":
            self.tasks.add_conversation_entry(task_id, {
                "type": "error",
                "content": data.get("content", ""),
            })
        elif update_type == "task_process":
            pid = data.get("process_id", "")
            if data.get("status") == "running":
                self.tasks.add_process(task_id, {
                    "id": pid,
                    "name": data.get("name", "Process"),
                    "status": "running",
                    "agent": data.get("agent"),
                })
            else:
                self.tasks.update_process(task_id, pid, data)
        elif update_type == "task_file_change":
            self.tasks.add_file_change(task_id, {
                "path": data.get("path", ""),
                "action": data.get("action", "modified"),
                "additions": data.get("additions"),
                "deletions": data.get("deletions"),
                "diff": data.get("diff"),
            })

    async def send_file(self, chat_id: int, content: bytes, filename: str) -> None:
        ws = self._connections.get(chat_id)
        if not ws:
            return
        msg_id = self._next_message_id()
        try:
            await ws.send_json({
                "type": "file",
                "message_id": msg_id,
                "filename": filename,
                "content_b64": base64.b64encode(content).decode(),
            })
        except Exception as e:
            logger.warning("Failed to send file to chat_id=%d: %s", chat_id, e)

    async def stop(self) -> None:
        for user_id, ws in list(self._connections.items()):
            try:
                await ws.close(code=1001, reason="Server shutting down")
            except Exception:
                pass
        self._connections.clear()

        if self._server:
            self._server.should_exit = True

        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass

        logger.info("Web channel stopped.")
