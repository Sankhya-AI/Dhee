"""Tests for ProjectManager — projects, statuses, and tags as Engram memories."""

import os
import tempfile

import pytest

from engram.configs.base import MemoryConfig
from engram.memory.main import Memory
from engram.memory.projects import ProjectManager, DEFAULT_STATUSES


def _make_memory(tmpdir):
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
def pm(mem):
    return ProjectManager(mem)


# ── Projects ──

class TestCreateProject:
    def test_create_basic(self, pm):
        p = pm.create_project("Test Project")
        assert p["name"] == "Test Project"
        assert p["color"] == "#6366f1"
        assert p["id"]
        assert p["created_at"]
        assert p["identifier"] == "TES"

    def test_create_with_color(self, pm):
        p = pm.create_project("Red Project", color="#ef4444")
        assert p["color"] == "#ef4444"

    def test_create_with_description(self, pm):
        p = pm.create_project("My Project", description="A test project")
        assert p["description"] == "A test project"


class TestListProjects:
    def test_list_empty(self, pm):
        assert pm.list_projects() == []

    def test_list_multiple(self, pm):
        pm.create_project("Project A")
        pm.create_project("Project B")
        projects = pm.list_projects()
        assert len(projects) == 2
        names = {p["name"] for p in projects}
        assert names == {"Project A", "Project B"}


class TestGetProject:
    def test_get_existing(self, pm):
        p = pm.create_project("Test")
        result = pm.get_project(p["id"])
        assert result is not None
        assert result["name"] == "Test"

    def test_get_nonexistent(self, pm):
        assert pm.get_project("nonexistent") is None


class TestUpdateProject:
    def test_update_name(self, pm):
        p = pm.create_project("Old Name")
        updated = pm.update_project(p["id"], {"name": "New Name"})
        assert updated["name"] == "New Name"

    def test_update_color(self, pm):
        p = pm.create_project("Test")
        updated = pm.update_project(p["id"], {"color": "#22c55e"})
        assert updated["color"] == "#22c55e"

    def test_update_nonexistent(self, pm):
        assert pm.update_project("nonexistent", {"name": "X"}) is None


class TestDeleteProject:
    def test_delete_existing(self, pm):
        p = pm.create_project("To Delete")
        assert pm.delete_project(p["id"]) is True
        assert pm.get_project(p["id"]) is None

    def test_delete_nonexistent(self, pm):
        assert pm.delete_project("nonexistent") is False


class TestIssueCounter:
    def test_next_issue_number(self, pm):
        p = pm.create_project("Counter Test")
        assert pm.next_issue_number(p["id"]) == 1
        assert pm.next_issue_number(p["id"]) == 2
        assert pm.next_issue_number(p["id"]) == 3


# ── Statuses ──

class TestCreateStatus:
    def test_create_basic(self, pm):
        p = pm.create_project("Test")
        s = pm.create_status(p["id"], "In Progress", "#f59e0b", 2)
        assert s["name"] == "In Progress"
        assert s["color"] == "#f59e0b"
        assert s["sort_order"] == 2
        assert s["project_id"] == p["id"]


class TestListStatuses:
    def test_list_sorted(self, pm):
        p = pm.create_project("Test")
        pm.create_status(p["id"], "C", "#000", 2)
        pm.create_status(p["id"], "A", "#000", 0)
        pm.create_status(p["id"], "B", "#000", 1)
        statuses = pm.list_statuses(p["id"])
        assert [s["name"] for s in statuses] == ["A", "B", "C"]

    def test_list_filters_by_project(self, pm):
        p1 = pm.create_project("Project 1")
        p2 = pm.create_project("Project 2")
        pm.create_status(p1["id"], "S1", "#000", 0)
        pm.create_status(p2["id"], "S2", "#000", 0)
        assert len(pm.list_statuses(p1["id"])) == 1
        assert len(pm.list_statuses(p2["id"])) == 1


class TestUpdateStatus:
    def test_update_name(self, pm):
        p = pm.create_project("Test")
        s = pm.create_status(p["id"], "Old", "#000", 0)
        updated = pm.update_status(s["id"], {"name": "New"})
        assert updated["name"] == "New"

    def test_update_nonexistent(self, pm):
        assert pm.update_status("nonexistent", {"name": "X"}) is None


class TestDeleteStatus:
    def test_delete_existing(self, pm):
        p = pm.create_project("Test")
        s = pm.create_status(p["id"], "To Delete", "#000", 0)
        assert pm.delete_status(s["id"]) is True
        assert len(pm.list_statuses(p["id"])) == 0


class TestEnsureDefaultStatuses:
    def test_creates_defaults(self, pm):
        p = pm.create_project("Test")
        statuses = pm.ensure_default_statuses(p["id"])
        assert len(statuses) == len(DEFAULT_STATUSES)
        names = [s["name"] for s in statuses]
        assert "Backlog" in names
        assert "Todo" in names
        assert "In Progress" in names
        assert "Done" in names

    def test_idempotent(self, pm):
        p = pm.create_project("Test")
        s1 = pm.ensure_default_statuses(p["id"])
        s2 = pm.ensure_default_statuses(p["id"])
        # Second call should not create more statuses (returns existing)
        assert len(s2) >= 1  # at least some exist
        assert len(s2) <= len(s1)  # not more than first call


class TestBulkUpdateStatuses:
    def test_bulk_reorder(self, pm):
        p = pm.create_project("Test")
        s1 = pm.create_status(p["id"], "A", "#000", 0)
        s2 = pm.create_status(p["id"], "B", "#000", 1)
        results = pm.bulk_update_statuses([
            {"id": s1["id"], "sort_order": 1},
            {"id": s2["id"], "sort_order": 0},
        ])
        assert len(results) == 2


# ── Tags ──

class TestCreateTag:
    def test_create_basic(self, pm):
        p = pm.create_project("Test")
        t = pm.create_tag(p["id"], "bug", "#ef4444")
        assert t["name"] == "bug"
        assert t["color"] == "#ef4444"
        assert t["project_id"] == p["id"]


class TestListTags:
    def test_list_tags(self, pm):
        p = pm.create_project("Test")
        pm.create_tag(p["id"], "bug")
        pm.create_tag(p["id"], "feature")
        tags = pm.list_tags(p["id"])
        assert len(tags) == 2

    def test_filters_by_project(self, pm):
        p1 = pm.create_project("Project 1")
        p2 = pm.create_project("Project 2")
        pm.create_tag(p1["id"], "tag1")
        pm.create_tag(p2["id"], "tag2")
        assert len(pm.list_tags(p1["id"])) == 1
        assert len(pm.list_tags(p2["id"])) == 1


class TestUpdateTag:
    def test_update_name(self, pm):
        p = pm.create_project("Test")
        t = pm.create_tag(p["id"], "old-name")
        updated = pm.update_tag(t["id"], {"name": "new-name"})
        assert updated["name"] == "new-name"


class TestDeleteTag:
    def test_delete_existing(self, pm):
        p = pm.create_project("Test")
        t = pm.create_tag(p["id"], "to-delete")
        assert pm.delete_tag(t["id"]) is True
        assert len(pm.list_tags(p["id"])) == 0

    def test_delete_nonexistent(self, pm):
        assert pm.delete_tag("nonexistent") is False
