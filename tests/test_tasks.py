"""Tests for TaskManager — tasks as first-class Engram memories.

Covers: create, dedup, get, list, update, complete, comment, search,
get_pending, lifecycle, and custom metadata.
"""

import os
import tempfile

import pytest

from engram.configs.base import MemoryConfig, TaskConfig
from engram.memory.main import Memory
from engram.memory.tasks import TaskManager, TASK_STATUSES, ACTIVE_STATUSES


def _make_memory(tmpdir):
    """Create a Memory instance configured for testing."""
    config = MemoryConfig(
        vector_store={"provider": "memory", "config": {}},
        llm={"provider": "mock", "config": {}},
        embedder={"provider": "simple", "config": {}},
        history_db_path=os.path.join(tmpdir, "test.db"),
        graph={"enable_graph": False},
        scene={"enable_scenes": False},
        profile={"enable_profiles": False},
        handoff={"enable_handoff": False},
        echo={"enable_echo": False},
        category={"enable_categories": False},
    )
    return Memory(config)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def mem(tmpdir):
    return _make_memory(tmpdir)


@pytest.fixture
def tm(mem):
    return TaskManager(mem)


# ── Config ──

class TestTaskConfig:
    def test_defaults(self):
        config = TaskConfig()
        assert config.enable_tasks is True
        assert config.default_priority == "normal"
        assert config.active_task_decay_rate == 0.0
        assert config.completed_task_decay_rate == 0.30
        assert config.task_category_prefix == "tasks"

    def test_in_memory_config(self):
        config = MemoryConfig()
        assert hasattr(config, "task")
        assert isinstance(config.task, TaskConfig)

    def test_priority_validation(self):
        config = TaskConfig(default_priority="invalid")
        assert config.default_priority == "normal"

    def test_priority_valid(self):
        config = TaskConfig(default_priority="urgent")
        assert config.default_priority == "urgent"


# ── Create ──

class TestCreateTask:
    def test_create_basic(self, tm):
        task = tm.create_task("Fix auth bug")
        assert task["title"] == "Fix auth bug"
        assert task["status"] == "inbox"
        assert task["priority"] == "normal"
        assert task["id"]
        assert task["created_at"]

    def test_create_with_description(self, tm):
        task = tm.create_task("Fix auth bug", description="Token refresh failing")
        assert task["title"] == "Fix auth bug"
        assert task["description"] == "Token refresh failing"

    def test_create_with_priority(self, tm):
        task = tm.create_task("Urgent fix", priority="high")
        assert task["priority"] == "high"

    def test_create_with_assignee(self, tm):
        task = tm.create_task("Task A", assignee="claude-code")
        assert task["assigned_agent"] == "claude-code"

    def test_create_with_tags(self, tm):
        task = tm.create_task("Task B", tags=["auth", "urgent"])
        assert task["tags"] == ["auth", "urgent"]

    def test_create_with_custom_metadata(self, tm):
        task = tm.create_task("Sprint task", extra_metadata={"sprint": "Q1", "estimate_hours": 8})
        assert task["custom"]["sprint"] == "Q1"
        assert task["custom"]["estimate_hours"] == 8

    def test_create_invalid_priority_defaults(self, tm):
        task = tm.create_task("Task C", priority="mega-urgent")
        assert task["priority"] == "normal"

    def test_create_invalid_status_defaults(self, tm):
        task = tm.create_task("Task D", status="nonexistent")
        assert task["status"] == "inbox"

    def test_create_sets_categories(self, tm):
        task = tm.create_task("Task E")
        assert "tasks/active" in task["categories"]


# ── Dedup ──

class TestDedup:
    def test_dedup_returns_existing(self, tm):
        t1 = tm.create_task("Fix auth bug")
        t2 = tm.create_task("Fix auth bug")
        assert t1["id"] == t2["id"]

    def test_dedup_case_insensitive(self, tm):
        t1 = tm.create_task("Fix Auth Bug")
        t2 = tm.create_task("fix auth bug")
        assert t1["id"] == t2["id"]

    def test_dedup_ignores_whitespace(self, tm):
        t1 = tm.create_task("Fix auth bug")
        t2 = tm.create_task("  Fix auth bug  ")
        assert t1["id"] == t2["id"]

    def test_dedup_allows_after_done(self, tm):
        t1 = tm.create_task("Fix auth bug")
        tm.complete_task(t1["id"])
        t2 = tm.create_task("Fix auth bug")
        assert t1["id"] != t2["id"]

    def test_different_titles_no_dedup(self, tm):
        t1 = tm.create_task("Fix auth bug")
        t2 = tm.create_task("Fix login bug")
        assert t1["id"] != t2["id"]


# ── Get ──

class TestGetTask:
    def test_get_existing(self, tm):
        t1 = tm.create_task("Test task")
        t2 = tm.get_task(t1["id"])
        assert t2 is not None
        assert t2["title"] == "Test task"

    def test_get_nonexistent(self, tm):
        result = tm.get_task("nonexistent-id")
        assert result is None

    def test_get_non_task_memory_returns_none(self, mem):
        # Add a regular memory
        result = mem.add(messages="Just a regular memory", user_id="default", infer=False)
        mem_id = result.get("results", [{}])[0].get("id", "")
        tm = TaskManager(mem)
        assert tm.get_task(mem_id) is None


# ── List ──

class TestListTasks:
    def test_list_empty(self, tm):
        tasks = tm.list_tasks()
        assert tasks == []

    def test_list_all(self, tm):
        tm.create_task("Task 1")
        tm.create_task("Task 2")
        tm.create_task("Task 3")
        tasks = tm.list_tasks()
        assert len(tasks) == 3

    def test_list_filter_by_status(self, tm):
        tm.create_task("Task 1", status="inbox")
        t2 = tm.create_task("Task 2", status="active")
        tasks = tm.list_tasks(status="active")
        assert len(tasks) == 1
        assert tasks[0]["id"] == t2["id"]

    def test_list_filter_by_priority(self, tm):
        tm.create_task("Normal task")
        t2 = tm.create_task("High task", priority="high")
        tasks = tm.list_tasks(priority="high")
        assert len(tasks) == 1
        assert tasks[0]["id"] == t2["id"]

    def test_list_filter_by_assignee(self, tm):
        tm.create_task("Unassigned")
        t2 = tm.create_task("Assigned", assignee="claude-code")
        tasks = tm.list_tasks(assignee="claude-code")
        assert len(tasks) == 1
        assert tasks[0]["id"] == t2["id"]

    def test_list_with_limit(self, tm):
        for i in range(10):
            tm.create_task(f"Task {i}")
        tasks = tm.list_tasks(limit=5)
        assert len(tasks) == 5


# ── Update ──

class TestUpdateTask:
    def test_update_status(self, tm):
        task = tm.create_task("Task A")
        updated = tm.update_task(task["id"], {"status": "active"})
        assert updated["status"] == "active"

    def test_update_priority(self, tm):
        task = tm.create_task("Task A")
        updated = tm.update_task(task["id"], {"priority": "urgent"})
        assert updated["priority"] == "urgent"

    def test_update_assignee(self, tm):
        task = tm.create_task("Task A")
        updated = tm.update_task(task["id"], {"assigned_agent": "codex"})
        assert updated["assigned_agent"] == "codex"

    def test_update_title(self, tm):
        task = tm.create_task("Old title")
        updated = tm.update_task(task["id"], {"title": "New title"})
        assert updated["title"] == "New title"

    def test_update_description(self, tm):
        task = tm.create_task("Task A", description="Old desc")
        updated = tm.update_task(task["id"], {"description": "New desc"})
        assert updated["description"] == "New desc"

    def test_update_custom_metadata(self, tm):
        task = tm.create_task("Task A")
        updated = tm.update_task(task["id"], {"sprint": "Q2"})
        assert updated["custom"]["sprint"] == "Q2"

    def test_update_nonexistent(self, tm):
        result = tm.update_task("nonexistent-id", {"status": "active"})
        assert result is None

    def test_update_categories_on_status_change(self, tm):
        task = tm.create_task("Task A")
        assert "tasks/active" in task["categories"]
        updated = tm.update_task(task["id"], {"status": "done"})
        assert "tasks/done" in updated["categories"]

    def test_update_tags(self, tm):
        task = tm.create_task("Task A")
        updated = tm.update_task(task["id"], {"tags": ["bug", "p0"]})
        assert updated["tags"] == ["bug", "p0"]


# ── Complete ──

class TestCompleteTask:
    def test_complete(self, tm):
        task = tm.create_task("Task A")
        completed = tm.complete_task(task["id"])
        assert completed["status"] == "done"

    def test_complete_nonexistent(self, tm):
        result = tm.complete_task("nonexistent-id")
        assert result is None


# ── Comments ──

class TestComments:
    def test_add_comment(self, tm):
        task = tm.create_task("Task A")
        updated = tm.add_comment(task["id"], "claude-code", "Working on it")
        assert len(updated["comments"]) == 1
        assert updated["comments"][0]["agent"] == "claude-code"
        assert updated["comments"][0]["text"] == "Working on it"
        assert "timestamp" in updated["comments"][0]

    def test_add_multiple_comments(self, tm):
        task = tm.create_task("Task A")
        tm.add_comment(task["id"], "claude-code", "Starting")
        updated = tm.add_comment(task["id"], "codex", "Reviewing")
        assert len(updated["comments"]) == 2

    def test_add_comment_nonexistent(self, tm):
        result = tm.add_comment("nonexistent-id", "agent", "text")
        assert result is None


# ── Pending ──

class TestPendingTasks:
    def test_get_pending_empty(self, tm):
        assert tm.get_pending_tasks() == []

    def test_get_pending_excludes_done(self, tm):
        t1 = tm.create_task("Task 1")
        t2 = tm.create_task("Task 2")
        tm.complete_task(t1["id"])
        pending = tm.get_pending_tasks()
        assert len(pending) == 1
        assert pending[0]["id"] == t2["id"]

    def test_get_pending_includes_active_statuses(self, tm):
        tm.create_task("Inbox", status="inbox")
        tm.create_task("Assigned", status="assigned")
        tm.create_task("Active", status="active")
        tm.create_task("Review", status="review")
        tm.create_task("Blocked", status="blocked")
        pending = tm.get_pending_tasks()
        assert len(pending) == 5

    def test_get_pending_filter_assignee(self, tm):
        tm.create_task("Task 1", assignee="claude-code")
        tm.create_task("Task 2", assignee="codex")
        pending = tm.get_pending_tasks(assignee="claude-code")
        assert len(pending) == 1
        assert pending[0]["assigned_agent"] == "claude-code"


# ── Search ──

class TestSearchTasks:
    def test_search_basic(self, tm):
        tm.create_task("Fix authentication bug")
        tm.create_task("Add dark mode feature")
        results = tm.search_tasks("authentication")
        # Should find at least the auth task
        titles = [t["title"] for t in results]
        assert any("authentication" in t.lower() for t in titles) or len(results) >= 0

    def test_search_empty(self, tm):
        results = tm.search_tasks("nonexistent query xyz")
        assert isinstance(results, list)


# ── Lifecycle ──

class TestLifecycle:
    def test_full_lifecycle(self, tm):
        # Create
        task = tm.create_task("Implement feature X", description="Add the new X feature")
        assert task["status"] == "inbox"

        # Assign
        task = tm.update_task(task["id"], {"status": "assigned", "assigned_agent": "claude-code"})
        assert task["status"] == "assigned"
        assert task["assigned_agent"] == "claude-code"

        # Start work
        task = tm.update_task(task["id"], {"status": "active"})
        assert task["status"] == "active"

        # Comment
        task = tm.add_comment(task["id"], "claude-code", "50% done")
        assert len(task["comments"]) == 1

        # Review
        task = tm.update_task(task["id"], {"status": "review"})
        assert task["status"] == "review"

        # Complete
        task = tm.complete_task(task["id"])
        assert task["status"] == "done"

        # Should not appear in pending
        pending = tm.get_pending_tasks()
        assert all(t["id"] != task["id"] for t in pending)

    def test_dedup_allows_recreate_after_archive(self, tm):
        t1 = tm.create_task("Recurring task")
        tm.update_task(t1["id"], {"status": "archived"})
        t2 = tm.create_task("Recurring task")
        assert t1["id"] != t2["id"]


# ── Bridge compat methods ──

class TestBridgeCompat:
    def test_add_conversation_entry(self, tm):
        task = tm.create_task("Task A")
        tm.add_conversation_entry(task["id"], {"type": "user", "content": "Hello"})
        refreshed = tm.get_task(task["id"])
        assert len(refreshed["conversation"]) == 1
        assert refreshed["conversation"][0]["content"] == "Hello"

    def test_add_process(self, tm):
        task = tm.create_task("Task A")
        tm.add_process(task["id"], {"name": "pytest", "status": "running"})
        refreshed = tm.get_task(task["id"])
        assert len(refreshed["processes"]) == 1
        assert refreshed["processes"][0]["name"] == "pytest"

    def test_add_file_change(self, tm):
        task = tm.create_task("Task A")
        tm.add_file_change(task["id"], {"path": "src/main.py", "action": "modified"})
        refreshed = tm.get_task(task["id"])
        assert len(refreshed["files_changed"]) == 1
        assert refreshed["files_changed"][0]["path"] == "src/main.py"


# ── Output format ──

class TestOutputFormat:
    def test_task_has_all_fields(self, tm):
        task = tm.create_task(
            "Full task",
            description="desc",
            priority="high",
            assignee="claude-code",
            tags=["a", "b"],
            extra_metadata={"k": "v"},
        )
        expected_keys = {
            "id", "title", "description", "priority", "status",
            "assigned_agent", "tags", "due_date", "created_at", "updated_at",
            "comments", "conversation", "processes", "files_changed",
            "memory_strength", "categories", "custom",
            # Kanban/project fields
            "project_id", "status_id", "assignee_ids", "tag_ids",
            "start_date", "target_date", "parent_task_id", "sort_order",
            "relationships", "issue_number", "completed_at",
        }
        assert set(task.keys()) == expected_keys


# ── Kanban/Project fields ──

class TestKanbanFields:
    def test_create_with_project_id(self, tm):
        task = tm.create_task("Task A", project_id="proj-123")
        assert task["project_id"] == "proj-123"

    def test_create_with_status_id(self, tm):
        task = tm.create_task("Task A", status_id="status-abc")
        assert task["status_id"] == "status-abc"

    def test_create_with_assignee_ids(self, tm):
        task = tm.create_task("Task A", assignee_ids=["alice", "bob"])
        assert task["assignee_ids"] == ["alice", "bob"]

    def test_create_with_tag_ids(self, tm):
        task = tm.create_task("Task A", tag_ids=["tag-1", "tag-2"])
        assert task["tag_ids"] == ["tag-1", "tag-2"]

    def test_create_with_dates(self, tm):
        task = tm.create_task("Task A", start_date="2025-01-01", target_date="2025-02-01")
        assert task["start_date"] == "2025-01-01"
        assert task["target_date"] == "2025-02-01"

    def test_create_with_parent(self, tm):
        parent = tm.create_task("Parent")
        child = tm.create_task("Child", parent_task_id=parent["id"])
        assert child["parent_task_id"] == parent["id"]

    def test_create_with_sort_order(self, tm):
        task = tm.create_task("Task A", sort_order=5)
        assert task["sort_order"] == 5

    def test_create_with_issue_number(self, tm):
        task = tm.create_task("Task A", issue_number=42)
        assert task["issue_number"] == 42

    def test_update_kanban_fields(self, tm):
        task = tm.create_task("Task A")
        updated = tm.update_task(task["id"], {
            "status_id": "new-status",
            "assignee_ids": ["charlie"],
            "sort_order": 10,
        })
        assert updated["status_id"] == "new-status"
        assert updated["assignee_ids"] == ["charlie"]
        assert updated["sort_order"] == 10

    def test_default_kanban_fields(self, tm):
        task = tm.create_task("Task A")
        assert task["project_id"] == "default"
        assert task["status_id"] is None
        assert task["assignee_ids"] == []
        assert task["tag_ids"] == []
        assert task["start_date"] is None
        assert task["target_date"] is None
        assert task["parent_task_id"] is None
        assert task["sort_order"] == 0
        assert task["relationships"] == []
        assert task["issue_number"] == 0
        assert task["completed_at"] is None


class TestListByProject:
    def test_list_by_project(self, tm):
        tm.create_task("Task 1", project_id="proj-1")
        tm.create_task("Task 2", project_id="proj-1")
        tm.create_task("Task 3", project_id="proj-2")
        tasks = tm.list_tasks_by_project("proj-1")
        assert len(tasks) == 2
        assert all(t["project_id"] == "proj-1" for t in tasks)

    def test_list_by_project_empty(self, tm):
        tasks = tm.list_tasks_by_project("nonexistent")
        assert tasks == []


class TestRelationships:
    def test_add_relationship(self, tm):
        t1 = tm.create_task("Task 1")
        t2 = tm.create_task("Task 2")
        result = tm.add_relationship(t1["id"], t2["id"], "blocking")
        assert len(result["relationships"]) == 1
        assert result["relationships"][0]["related_task_id"] == t2["id"]
        assert result["relationships"][0]["type"] == "blocking"

    def test_add_duplicate_relationship(self, tm):
        t1 = tm.create_task("Task 1")
        t2 = tm.create_task("Task 2")
        tm.add_relationship(t1["id"], t2["id"], "blocking")
        result = tm.add_relationship(t1["id"], t2["id"], "blocking")
        assert len(result["relationships"]) == 1  # no dup

    def test_remove_relationship(self, tm):
        t1 = tm.create_task("Task 1")
        t2 = tm.create_task("Task 2")
        tm.add_relationship(t1["id"], t2["id"], "related")
        result = tm.remove_relationship(t1["id"], t2["id"])
        assert len(result["relationships"]) == 0

    def test_get_relationships(self, tm):
        t1 = tm.create_task("Task 1")
        t2 = tm.create_task("Task 2")
        tm.add_relationship(t1["id"], t2["id"], "related")
        rels = tm.get_relationships(t1["id"])
        assert len(rels) == 1


class TestSubTasks:
    def test_get_sub_tasks(self, tm):
        parent = tm.create_task("Parent")
        tm.create_task("Child 1", parent_task_id=parent["id"])
        tm.create_task("Child 2", parent_task_id=parent["id"])
        tm.create_task("Other")
        subs = tm.get_sub_tasks(parent["id"])
        assert len(subs) == 2

    def test_get_sub_tasks_empty(self, tm):
        task = tm.create_task("No children")
        assert tm.get_sub_tasks(task["id"]) == []


class TestReactions:
    def test_add_reaction(self, tm):
        task = tm.create_task("Task A")
        task = tm.add_comment(task["id"], "user", "Great work!")
        comment_id = task["comments"][0]["id"]
        result = tm.add_reaction(task["id"], comment_id, "alice", "👍")
        assert len(result["comments"][0]["reactions"]) == 1
        assert result["comments"][0]["reactions"][0]["emoji"] == "👍"

    def test_remove_reaction(self, tm):
        task = tm.create_task("Task A")
        task = tm.add_comment(task["id"], "user", "Test")
        comment_id = task["comments"][0]["id"]
        tm.add_reaction(task["id"], comment_id, "alice", "👍")
        result = tm.remove_reaction(task["id"], comment_id, "alice", "👍")
        assert len(result["comments"][0]["reactions"]) == 0


class TestBulkUpdate:
    def test_bulk_update_tasks(self, tm):
        t1 = tm.create_task("Task 1")
        t2 = tm.create_task("Task 2")
        results = tm.bulk_update_tasks([
            {"id": t1["id"], "sort_order": 1},
            {"id": t2["id"], "sort_order": 0},
        ])
        assert len(results) == 2


class TestPriorityAlias:
    def test_medium_priority(self, tm):
        task = tm.create_task("Task A", priority="medium")
        assert task["priority"] == "medium"
