"""ProjectManager — projects, statuses, and tags as Engram memories.

Projects are top-level containers for tasks/issues.
Statuses and tags belong to a project and are stored as separate memories.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_STATUSES = [
    {"name": "Backlog", "color": "#94a3b8", "sort_order": 0},
    {"name": "Todo", "color": "#3b82f6", "sort_order": 1},
    {"name": "In Progress", "color": "#f59e0b", "sort_order": 2},
    {"name": "In Review", "color": "#8b5cf6", "sort_order": 3},
    {"name": "Done", "color": "#22c55e", "sort_order": 4},
]


class ProjectManager:
    """Manages projects, statuses, and tags as Engram memories."""

    def __init__(self, memory: "Memory") -> None:  # noqa: F821
        self.memory = memory

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_metadata(mem: Dict[str, Any]) -> Dict[str, Any]:
        md = mem.get("metadata", {})
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except (json.JSONDecodeError, TypeError):
                md = {}
        return md if isinstance(md, dict) else {}

    def _get_all_by_type(
        self, memory_type: str, user_id: str = "default", limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Fetch all memories of a given type."""
        memories = self.memory.db.get_all_memories(
            user_id=user_id, memory_type=memory_type, limit=limit,
        )
        return [m for m in memories if self._parse_metadata(m).get("memory_type") == memory_type]

    def _add_entity(
        self, content: str, metadata: Dict[str, Any], user_id: str = "default"
    ) -> str:
        """Store an entity as an Engram memory, return its ID."""
        result = self.memory.add(
            messages=content,
            user_id=user_id,
            metadata=metadata,
            source_app="project-manager",
            infer=False,
        )
        mem_id = None
        if isinstance(result, dict):
            results = result.get("results", [])
            if results and isinstance(results, list):
                first = results[0]
                mem_id = first.get("id") or first.get("memory_id")
            if not mem_id:
                mem_id = result.get("id") or result.get("memory_id")
        return mem_id or ""

    # ------------------------------------------------------------------
    # Projects (memory_type="project")
    # ------------------------------------------------------------------

    def create_project(
        self,
        name: str,
        color: str = "#6366f1",
        description: str = "",
        user_id: str = "default",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "memory_type": "project",
            "project_name": name,
            "project_color": color,
            "project_description": description,
            "project_created_at": now,
            "project_identifier": name[:3].upper(),
            "project_issue_counter": 0,
        }
        content = f"Project: {name}"
        if description:
            content += f"\n{description}"
        mem_id = self._add_entity(content, meta, user_id)
        return {
            "id": mem_id,
            "name": name,
            "color": color,
            "description": description,
            "identifier": meta["project_identifier"],
            "issue_counter": 0,
            "created_at": now,
        }

    def list_projects(self, user_id: str = "default") -> List[Dict[str, Any]]:
        mems = self._get_all_by_type("project", user_id)
        projects = []
        for m in mems:
            md = self._parse_metadata(m)
            projects.append({
                "id": m["id"],
                "name": md.get("project_name", ""),
                "color": md.get("project_color", "#6366f1"),
                "description": md.get("project_description", ""),
                "identifier": md.get("project_identifier", ""),
                "issue_counter": md.get("project_issue_counter", 0),
                "created_at": md.get("project_created_at", m.get("created_at", "")),
            })
        return projects

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        mem = self.memory.get(project_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project":
            return None
        return {
            "id": mem["id"],
            "name": md.get("project_name", ""),
            "color": md.get("project_color", "#6366f1"),
            "description": md.get("project_description", ""),
            "identifier": md.get("project_identifier", ""),
            "issue_counter": md.get("project_issue_counter", 0),
            "created_at": md.get("project_created_at", mem.get("created_at", "")),
        }

    def update_project(self, project_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mem = self.memory.get(project_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project":
            return None

        field_map = {
            "name": "project_name",
            "color": "project_color",
            "description": "project_description",
        }
        for key, val in updates.items():
            if key in field_map:
                md[field_map[key]] = val

        db_updates: Dict[str, Any] = {"metadata": md}
        if "name" in updates:
            content = f"Project: {updates['name']}"
            desc = md.get("project_description", "")
            if desc:
                content += f"\n{desc}"
            db_updates["memory"] = content

        self.memory.db.update_memory(project_id, db_updates)
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> bool:
        mem = self.memory.get(project_id)
        if not mem:
            return False
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project":
            return False
        self.memory.db.delete_memory(project_id)
        return True

    def next_issue_number(self, project_id: str) -> int:
        """Atomically increment and return the next issue number for a project."""
        mem = self.memory.get(project_id)
        if not mem:
            return 1
        md = self._parse_metadata(mem)
        counter = md.get("project_issue_counter", 0) + 1
        md["project_issue_counter"] = counter
        self.memory.db.update_memory(project_id, {"metadata": md})
        return counter

    # ------------------------------------------------------------------
    # Statuses (memory_type="project_status")
    # ------------------------------------------------------------------

    def create_status(
        self,
        project_id: str,
        name: str,
        color: str = "#94a3b8",
        sort_order: int = 0,
        hidden: bool = False,
        user_id: str = "default",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "memory_type": "project_status",
            "status_project_id": project_id,
            "status_name": name,
            "status_color": color,
            "status_sort_order": sort_order,
            "status_hidden": hidden,
            "status_created_at": now,
        }
        mem_id = self._add_entity(f"Status: {name}", meta, user_id)
        return {
            "id": mem_id,
            "project_id": project_id,
            "name": name,
            "color": color,
            "sort_order": sort_order,
            "hidden": hidden,
            "created_at": now,
        }

    def list_statuses(self, project_id: str, user_id: str = "default") -> List[Dict[str, Any]]:
        mems = self._get_all_by_type("project_status", user_id)
        statuses = []
        for m in mems:
            md = self._parse_metadata(m)
            if md.get("status_project_id") != project_id:
                continue
            statuses.append({
                "id": m["id"],
                "project_id": project_id,
                "name": md.get("status_name", ""),
                "color": md.get("status_color", "#94a3b8"),
                "sort_order": md.get("status_sort_order", 0),
                "hidden": md.get("status_hidden", False),
                "created_at": md.get("status_created_at", m.get("created_at", "")),
            })
        statuses.sort(key=lambda s: s["sort_order"])
        return statuses

    def update_status(self, status_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mem = self.memory.get(status_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project_status":
            return None

        field_map = {
            "name": "status_name",
            "color": "status_color",
            "sort_order": "status_sort_order",
            "hidden": "status_hidden",
        }
        for key, val in updates.items():
            if key in field_map:
                md[field_map[key]] = val

        db_updates: Dict[str, Any] = {"metadata": md}
        if "name" in updates:
            db_updates["memory"] = f"Status: {updates['name']}"
        self.memory.db.update_memory(status_id, db_updates)

        return {
            "id": status_id,
            "project_id": md.get("status_project_id", ""),
            "name": md.get("status_name", ""),
            "color": md.get("status_color", "#94a3b8"),
            "sort_order": md.get("status_sort_order", 0),
            "hidden": md.get("status_hidden", False),
            "created_at": md.get("status_created_at", ""),
        }

    def delete_status(self, status_id: str) -> bool:
        mem = self.memory.get(status_id)
        if not mem:
            return False
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project_status":
            return False
        self.memory.db.delete_memory(status_id)
        return True

    def bulk_update_statuses(self, updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for u in updates:
            sid = u.pop("id", None)
            if sid:
                r = self.update_status(sid, u)
                if r:
                    results.append(r)
        return results

    def ensure_default_statuses(self, project_id: str, user_id: str = "default") -> List[Dict[str, Any]]:
        existing = self.list_statuses(project_id, user_id)
        if existing:
            return existing
        statuses = []
        for s in DEFAULT_STATUSES:
            st = self.create_status(
                project_id, s["name"], s["color"], s["sort_order"], user_id=user_id,
            )
            statuses.append(st)
        return statuses

    # ------------------------------------------------------------------
    # Tags (memory_type="project_tag")
    # ------------------------------------------------------------------

    def create_tag(
        self,
        project_id: str,
        name: str,
        color: str = "#6366f1",
        user_id: str = "default",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "memory_type": "project_tag",
            "tag_project_id": project_id,
            "tag_name": name,
            "tag_color": color,
            "tag_created_at": now,
        }
        mem_id = self._add_entity(f"Tag: {name}", meta, user_id)
        return {
            "id": mem_id,
            "project_id": project_id,
            "name": name,
            "color": color,
            "created_at": now,
        }

    def list_tags(self, project_id: str, user_id: str = "default") -> List[Dict[str, Any]]:
        mems = self._get_all_by_type("project_tag", user_id)
        tags = []
        for m in mems:
            md = self._parse_metadata(m)
            if md.get("tag_project_id") != project_id:
                continue
            tags.append({
                "id": m["id"],
                "project_id": project_id,
                "name": md.get("tag_name", ""),
                "color": md.get("tag_color", "#6366f1"),
                "created_at": md.get("tag_created_at", m.get("created_at", "")),
            })
        return tags

    def update_tag(self, tag_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mem = self.memory.get(tag_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project_tag":
            return None

        field_map = {"name": "tag_name", "color": "tag_color"}
        for key, val in updates.items():
            if key in field_map:
                md[field_map[key]] = val

        db_updates: Dict[str, Any] = {"metadata": md}
        if "name" in updates:
            db_updates["memory"] = f"Tag: {updates['name']}"
        self.memory.db.update_memory(tag_id, db_updates)

        return {
            "id": tag_id,
            "project_id": md.get("tag_project_id", ""),
            "name": md.get("tag_name", ""),
            "color": md.get("tag_color", "#6366f1"),
            "created_at": md.get("tag_created_at", ""),
        }

    def delete_tag(self, tag_id: str) -> bool:
        mem = self.memory.get(tag_id)
        if not mem:
            return False
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "project_tag":
            return False
        self.memory.db.delete_memory(tag_id)
        return True
