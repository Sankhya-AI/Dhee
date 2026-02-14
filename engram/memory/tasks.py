"""TaskManager — tasks as first-class Engram memories.

Tasks are stored as regular memories with memory_type="task".
All task-specific attributes live in the metadata JSON dict.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram.configs.base import TaskConfig

logger = logging.getLogger(__name__)

# Valid task statuses (lifecycle order)
TASK_STATUSES = frozenset({
    "inbox", "assigned", "active", "review", "blocked", "done", "archived",
})
ACTIVE_STATUSES = frozenset({
    "inbox", "assigned", "active", "review", "blocked",
})
TASK_PRIORITIES = frozenset({"low", "normal", "medium", "high", "urgent"})
PRIORITY_ALIASES = {"medium": "medium", "normal": "normal"}  # both accepted


class TaskManager:
    """High-level task CRUD over Engram Memory with dedup and lifecycle."""

    def __init__(self, memory: "Memory"):  # noqa: F821
        self.memory = memory
        self.config: TaskConfig = getattr(memory.config, "task", TaskConfig())

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_task(
        self,
        title: str,
        *,
        description: str = "",
        priority: str | None = None,
        status: str = "inbox",
        assignee: str | None = None,
        due_date: str | None = None,
        tags: List[str] | None = None,
        user_id: str = "default",
        extra_metadata: Dict[str, Any] | None = None,
        # Kanban/project fields
        project_id: str = "default",
        status_id: str | None = None,
        assignee_ids: List[str] | None = None,
        tag_ids: List[str] | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        parent_task_id: str | None = None,
        sort_order: int = 0,
        issue_number: int | None = None,
    ) -> Dict[str, Any]:
        """Create a task. Returns existing task if title matches (dedup)."""
        existing = self._dedup_check(title, user_id)
        if existing:
            return existing

        priority = priority or self.config.default_priority
        if priority not in TASK_PRIORITIES:
            priority = "normal"
        if status not in TASK_STATUSES:
            status = "inbox"

        now = datetime.now(timezone.utc).isoformat()
        prefix = self.config.task_category_prefix
        status_cat = f"{prefix}/{'active' if status in ACTIVE_STATUSES else status}"

        meta = {
            "memory_type": "task",
            "task_status": status,
            "task_priority": priority,
            "task_assigned_agent": assignee,
            "task_due_date": due_date,
            "task_tags": tags or [],
            "task_comments": [],
            "task_conversation": [],
            "task_processes": [],
            "task_files_changed": [],
            "task_created_at": now,
            "task_updated_at": now,
            # Kanban/project fields
            "task_project_id": project_id,
            "task_status_id": status_id,
            "task_assignee_ids": assignee_ids or [],
            "task_tag_ids": tag_ids or [],
            "task_start_date": start_date,
            "task_target_date": target_date,
            "task_parent_id": parent_task_id,
            "task_sort_order": sort_order,
            "task_relationships": [],
            "task_issue_number": issue_number or 0,
            "task_completed_at": None,
        }
        if extra_metadata:
            meta["task_custom"] = extra_metadata

        # Build content string for embedding
        content = title
        if description:
            content = f"{title}\n{description}"

        result = self.memory.add(
            messages=content,
            user_id=user_id,
            categories=[status_cat],
            metadata=meta,
            source_app="task-manager",
            infer=False,
        )

        # Extract the memory id from add result
        mem_id = None
        if isinstance(result, dict):
            results = result.get("results", [])
            if results and isinstance(results, list):
                first = results[0]
                mem_id = first.get("id") or first.get("memory_id")
            if not mem_id:
                mem_id = result.get("id") or result.get("memory_id")

        if not mem_id:
            logger.warning("Could not extract memory ID from add result: %s", result)
            return {"error": "Failed to create task", "add_result": result}

        return self._format_task_from_parts(
            mem_id=mem_id,
            content=content,
            title=title,
            description=description,
            metadata=meta,
            categories=[status_cat],
            strength=1.0,
            created_at=now,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task by its memory ID."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None
        return self._format_task(mem)

    def list_tasks(
        self,
        *,
        user_id: str = "default",
        status: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List tasks with optional filters."""
        memories = self.memory.db.get_all_memories(
            user_id=user_id,
            memory_type="task",
            limit=limit * 3,  # fetch extra for post-filtering
        )
        tasks = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "task":
                continue
            if status and md.get("task_status") != status:
                continue
            if priority and md.get("task_priority") != priority:
                continue
            if assignee and md.get("task_assigned_agent") != assignee:
                continue
            tasks.append(self._format_task(mem))
            if len(tasks) >= limit:
                break
        return tasks

    def get_pending_tasks(
        self,
        *,
        user_id: str = "default",
        assignee: str | None = None,
    ) -> List[Dict[str, Any]]:
        """Get actionable tasks (not done/archived)."""
        memories = self.memory.db.get_all_memories(
            user_id=user_id,
            memory_type="task",
            limit=500,
        )
        tasks = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "task":
                continue
            ts = md.get("task_status", "inbox")
            if ts not in ACTIVE_STATUSES:
                continue
            if assignee and md.get("task_assigned_agent") != assignee:
                continue
            tasks.append(self._format_task(mem))
        return tasks

    def search_tasks(
        self,
        query: str,
        *,
        user_id: str = "default",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Semantic search over tasks."""
        results = self.memory.search(
            query=query,
            user_id=user_id,
            limit=limit * 3,
        )
        tasks = []
        for r in results.get("results", []):
            md = self._parse_metadata(r)
            if md.get("memory_type") != "task":
                continue
            tasks.append(self._format_task(r))
            if len(tasks) >= limit:
                break
        return tasks

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update task fields. Returns updated task or None."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        now = datetime.now(timezone.utc).isoformat()

        # Map update keys to metadata keys
        field_map = {
            "status": "task_status",
            "priority": "task_priority",
            "assigned_agent": "task_assigned_agent",
            "due_date": "task_due_date",
            "tags": "task_tags",
            "project_id": "task_project_id",
            "status_id": "task_status_id",
            "assignee_ids": "task_assignee_ids",
            "tag_ids": "task_tag_ids",
            "start_date": "task_start_date",
            "target_date": "task_target_date",
            "parent_task_id": "task_parent_id",
            "sort_order": "task_sort_order",
            "relationships": "task_relationships",
            "issue_number": "task_issue_number",
            "completed_at": "task_completed_at",
        }

        new_md = dict(md)
        for key, val in updates.items():
            if key in field_map:
                new_md[field_map[key]] = val
            elif key in ("title", "description"):
                pass  # handled below
            else:
                # Arbitrary user-defined metadata
                custom = new_md.get("task_custom", {})
                if not isinstance(custom, dict):
                    custom = {}
                custom[key] = val
                new_md["task_custom"] = custom

        new_md["task_updated_at"] = now

        # Update categories on status change
        db_updates: Dict[str, Any] = {"metadata": new_md}
        new_status = updates.get("status")
        if new_status and new_status in TASK_STATUSES:
            prefix = self.config.task_category_prefix
            cat = f"{prefix}/{'active' if new_status in ACTIVE_STATUSES else new_status}"
            db_updates["categories"] = [cat]

        # Update content if title or description changed
        new_title = updates.get("title")
        new_desc = updates.get("description")
        if new_title or new_desc:
            old_content = mem.get("memory", "")
            parts = old_content.split("\n", 1)
            t = new_title or parts[0]
            d = new_desc or (parts[1] if len(parts) > 1 else "")
            new_content = f"{t}\n{d}" if d else t
            db_updates["memory"] = new_content

        self.memory.db.update_memory(task_id, db_updates)
        return self.get_task(task_id)

    def complete_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Mark a task as done."""
        return self.update_task(task_id, {"status": "done"})

    def add_comment(
        self,
        task_id: str,
        agent: str,
        text: str,
    ) -> Optional[Dict[str, Any]]:
        """Append a comment to a task."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        now = datetime.now(timezone.utc).isoformat()
        comments = md.get("task_comments", [])
        if not isinstance(comments, list):
            comments = []
        comment_id = str(uuid.uuid4())[:8]
        comments.append({"id": comment_id, "agent": agent, "text": text, "timestamp": now, "reactions": []})

        md["task_comments"] = comments
        md["task_updated_at"] = now
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    # ------------------------------------------------------------------
    # Bridge-compatible methods
    # ------------------------------------------------------------------

    def add_conversation_entry(self, task_id: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Append a conversation entry to a task (bridge compat)."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        now = datetime.now(timezone.utc).isoformat()
        convos = md.get("task_conversation", [])
        if not isinstance(convos, list):
            convos = []
        entry.setdefault("timestamp", now)
        convos.append(entry)

        md["task_conversation"] = convos
        md["task_updated_at"] = now
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    def add_process(self, task_id: str, process: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Track a process execution on a task (bridge compat)."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        now = datetime.now(timezone.utc).isoformat()
        procs = md.get("task_processes", [])
        if not isinstance(procs, list):
            procs = []
        process.setdefault("started_at", now)
        procs.append(process)

        md["task_processes"] = procs
        md["task_updated_at"] = now
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    def add_file_change(self, task_id: str, change: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Log a file change on a task (bridge compat)."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        now = datetime.now(timezone.utc).isoformat()
        files = md.get("task_files_changed", [])
        if not isinstance(files, list):
            files = []
        change.setdefault("timestamp", now)
        files.append(change)

        md["task_files_changed"] = files
        md["task_updated_at"] = now
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    # ------------------------------------------------------------------
    # Kanban / project-aware methods
    # ------------------------------------------------------------------

    def list_tasks_by_project(
        self,
        project_id: str,
        *,
        user_id: str = "default",
        status_id: str | None = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """List tasks belonging to a specific project."""
        memories = self.memory.db.get_all_memories(
            user_id=user_id, memory_type="task", limit=limit * 3,
        )
        tasks = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "task":
                continue
            if md.get("task_project_id") != project_id:
                continue
            if status_id and md.get("task_status_id") != status_id:
                continue
            tasks.append(self._format_task(mem))
            if len(tasks) >= limit:
                break
        return tasks

    def add_relationship(
        self, task_id: str, related_id: str, rel_type: str = "related"
    ) -> Optional[Dict[str, Any]]:
        """Add a relationship (blocking, related, duplicate) to a task."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        now = datetime.now(timezone.utc).isoformat()
        rels = md.get("task_relationships", [])
        if not isinstance(rels, list):
            rels = []
        # Avoid duplicates
        for r in rels:
            if r.get("related_task_id") == related_id and r.get("type") == rel_type:
                return self.get_task(task_id)
        rels.append({"related_task_id": related_id, "type": rel_type, "created_at": now})
        md["task_relationships"] = rels
        md["task_updated_at"] = now
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    def remove_relationship(
        self, task_id: str, related_id: str
    ) -> Optional[Dict[str, Any]]:
        """Remove a relationship from a task."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        rels = md.get("task_relationships", [])
        md["task_relationships"] = [r for r in rels if r.get("related_task_id") != related_id]
        md["task_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    def get_relationships(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all relationships for a task."""
        task = self.get_task(task_id)
        if not task:
            return []
        return task.get("relationships", [])

    def get_sub_tasks(self, parent_task_id: str, user_id: str = "default") -> List[Dict[str, Any]]:
        """Get all sub-tasks of a parent task."""
        memories = self.memory.db.get_all_memories(
            user_id=user_id, memory_type="task", limit=500,
        )
        tasks = []
        for mem in memories:
            md = self._parse_metadata(mem)
            if md.get("memory_type") != "task":
                continue
            if md.get("task_parent_id") == parent_task_id:
                tasks.append(self._format_task(mem))
        return tasks

    def add_reaction(
        self, task_id: str, comment_id: str, user_id: str, emoji: str
    ) -> Optional[Dict[str, Any]]:
        """Add a reaction to a comment on a task."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        comments = md.get("task_comments", [])
        for c in comments:
            cid = c.get("id") or c.get("timestamp", "")
            if cid == comment_id:
                reactions = c.get("reactions", [])
                # Avoid duplicate
                if not any(r["user_id"] == user_id and r["emoji"] == emoji for r in reactions):
                    reactions.append({"user_id": user_id, "emoji": emoji})
                c["reactions"] = reactions
                break

        md["task_comments"] = comments
        md["task_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    def remove_reaction(
        self, task_id: str, comment_id: str, user_id: str, emoji: str
    ) -> Optional[Dict[str, Any]]:
        """Remove a reaction from a comment on a task."""
        mem = self.memory.get(task_id)
        if not mem:
            return None
        md = self._parse_metadata(mem)
        if md.get("memory_type") != "task":
            return None

        comments = md.get("task_comments", [])
        for c in comments:
            cid = c.get("id") or c.get("timestamp", "")
            if cid == comment_id:
                reactions = c.get("reactions", [])
                c["reactions"] = [
                    r for r in reactions
                    if not (r["user_id"] == user_id and r["emoji"] == emoji)
                ]
                break

        md["task_comments"] = comments
        md["task_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.memory.db.update_memory(task_id, {"metadata": md})
        return self.get_task(task_id)

    def bulk_update_tasks(self, updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Bulk update multiple tasks (e.g., for drag-drop reorder)."""
        results = []
        for u in updates:
            task_id = u.pop("id", None)
            if task_id:
                r = self.update_task(task_id, u)
                if r:
                    results.append(r)
        return results

    # ------------------------------------------------------------------
    # Dedup
    # ------------------------------------------------------------------

    def _dedup_check(self, title: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Case-insensitive title match on non-done/archived tasks."""
        existing = self.list_tasks(user_id=user_id, limit=500)
        title_lower = title.strip().lower()
        for t in existing:
            if t["title"].strip().lower() == title_lower and t["status"] not in ("done", "archived"):
                return t
        return None

    # ------------------------------------------------------------------
    # Formatting helpers
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

    def _format_task(self, mem: Dict[str, Any]) -> Dict[str, Any]:
        md = self._parse_metadata(mem)
        content = mem.get("memory", "")
        parts = content.split("\n", 1)
        title = parts[0]
        description = parts[1] if len(parts) > 1 else ""

        cats = mem.get("categories", [])
        if isinstance(cats, str):
            try:
                cats = json.loads(cats)
            except (json.JSONDecodeError, TypeError):
                cats = []

        return {
            "id": mem["id"],
            "title": title,
            "description": description,
            "priority": md.get("task_priority", self.config.default_priority),
            "status": md.get("task_status", "inbox"),
            "assigned_agent": md.get("task_assigned_agent"),
            "tags": md.get("task_tags", []),
            "due_date": md.get("task_due_date"),
            "created_at": md.get("task_created_at", mem.get("created_at", "")),
            "updated_at": md.get("task_updated_at", mem.get("updated_at", "")),
            "comments": md.get("task_comments", []),
            "conversation": md.get("task_conversation", []),
            "processes": md.get("task_processes", []),
            "files_changed": md.get("task_files_changed", []),
            "memory_strength": round(float(mem.get("strength", 1.0)), 3),
            "categories": cats,
            "custom": md.get("task_custom", {}),
            # Kanban/project fields
            "project_id": md.get("task_project_id", "default"),
            "status_id": md.get("task_status_id"),
            "assignee_ids": md.get("task_assignee_ids", []),
            "tag_ids": md.get("task_tag_ids", []),
            "start_date": md.get("task_start_date"),
            "target_date": md.get("task_target_date"),
            "parent_task_id": md.get("task_parent_id"),
            "sort_order": md.get("task_sort_order", 0),
            "relationships": md.get("task_relationships", []),
            "issue_number": md.get("task_issue_number", 0),
            "completed_at": md.get("task_completed_at"),
        }

    def _format_task_from_parts(
        self,
        *,
        mem_id: str,
        content: str,
        title: str,
        description: str,
        metadata: Dict[str, Any],
        categories: List[str],
        strength: float,
        created_at: str,
    ) -> Dict[str, Any]:
        return {
            "id": mem_id,
            "title": title,
            "description": description,
            "priority": metadata.get("task_priority", self.config.default_priority),
            "status": metadata.get("task_status", "inbox"),
            "assigned_agent": metadata.get("task_assigned_agent"),
            "tags": metadata.get("task_tags", []),
            "due_date": metadata.get("task_due_date"),
            "created_at": metadata.get("task_created_at", created_at),
            "updated_at": metadata.get("task_updated_at", created_at),
            "comments": metadata.get("task_comments", []),
            "conversation": metadata.get("task_conversation", []),
            "processes": metadata.get("task_processes", []),
            "files_changed": metadata.get("task_files_changed", []),
            "memory_strength": round(strength, 3),
            "categories": categories,
            "custom": metadata.get("task_custom", {}),
            # Kanban/project fields
            "project_id": metadata.get("task_project_id", "default"),
            "status_id": metadata.get("task_status_id"),
            "assignee_ids": metadata.get("task_assignee_ids", []),
            "tag_ids": metadata.get("task_tag_ids", []),
            "start_date": metadata.get("task_start_date"),
            "target_date": metadata.get("task_target_date"),
            "parent_task_id": metadata.get("task_parent_id"),
            "sort_order": metadata.get("task_sort_order", 0),
            "relationships": metadata.get("task_relationships", []),
            "issue_number": metadata.get("task_issue_number", 0),
            "completed_at": metadata.get("task_completed_at"),
        }
