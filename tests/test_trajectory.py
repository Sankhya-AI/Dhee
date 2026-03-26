"""Tests for engram.skills.trajectory — recorder, store, hash determinism."""

import pytest
import time

from dhee.skills.trajectory import TrajectoryRecorder, TrajectoryStore
from dhee.skills.schema import Trajectory, TrajectoryStep


class TestTrajectoryRecorder:
    def test_record_steps(self):
        recorder = TrajectoryRecorder(
            task_description="fix a bug",
            user_id="test-user",
            agent_id="test-agent",
        )
        recorder.record_step(
            action="search",
            tool="grep",
            args={"pattern": "error"},
            result_summary="found 3 matches",
        )
        recorder.record_step(
            action="edit",
            tool="write",
            args={"file": "main.py"},
            result_summary="fixed typo",
        )
        assert len(recorder.steps) == 2
        assert recorder.steps[0].action == "search"
        assert recorder.steps[1].action == "edit"

    def test_finalize_success(self):
        recorder = TrajectoryRecorder(task_description="test task")
        recorder.record_step(action="test", tool="pytest", result_summary="pass")
        trajectory = recorder.finalize(success=True, outcome_summary="All tests pass")
        assert trajectory.success is True
        assert trajectory.outcome_summary == "All tests pass"
        assert len(trajectory.steps) == 1
        assert trajectory.completed_at is not None
        assert len(trajectory.trajectory_hash_val) == 64  # SHA-256 hex

    def test_finalize_failure(self):
        recorder = TrajectoryRecorder(task_description="broken task")
        recorder.record_step(action="test", error="AssertionError")
        trajectory = recorder.finalize(success=False, outcome_summary="Test failed")
        assert trajectory.success is False

    def test_hash_determinism(self):
        """Same steps should produce the same trajectory hash."""
        r1 = TrajectoryRecorder(task_description="task A")
        r1.record_step(action="search", tool="grep", args={"pattern": "x"})
        r1.record_step(action="edit", tool="write", args={"file": "f.py"})
        t1 = r1.finalize(success=True)

        r2 = TrajectoryRecorder(task_description="task B")
        r2.record_step(action="search", tool="grep", args={"pattern": "x"})
        r2.record_step(action="edit", tool="write", args={"file": "f.py"})
        t2 = r2.finalize(success=True)

        # Same steps → same hash (task description excluded from hash)
        assert t1.trajectory_hash_val == t2.trajectory_hash_val

    def test_different_steps_different_hash(self):
        r1 = TrajectoryRecorder(task_description="task")
        r1.record_step(action="search", tool="grep")
        t1 = r1.finalize(success=True)

        r2 = TrajectoryRecorder(task_description="task")
        r2.record_step(action="edit", tool="write")
        t2 = r2.finalize(success=True)

        assert t1.trajectory_hash_val != t2.trajectory_hash_val

    def test_recorder_id_unique(self):
        r1 = TrajectoryRecorder(task_description="a")
        r2 = TrajectoryRecorder(task_description="b")
        assert r1.id != r2.id

    def test_step_error_recorded(self):
        recorder = TrajectoryRecorder(task_description="error test")
        recorder.record_step(
            action="compile",
            tool="gcc",
            error="syntax error at line 42",
        )
        assert recorder.steps[0].error == "syntax error at line 42"


class TestTrajectoryStore:
    """Tests using a mock DB that stores in-memory."""

    class MockDB:
        def __init__(self):
            self._store = {}

        def add_memory(self, data):
            self._store[data["id"]] = data

        def get_memory(self, memory_id):
            return self._store.get(memory_id)

        def get_all_memories(self, user_id=None, agent_id=None, limit=100, **kwargs):
            results = list(self._store.values())
            if user_id:
                results = [r for r in results if r.get("user_id") == user_id]
            return results[:limit]

    @pytest.fixture
    def store(self):
        return TrajectoryStore(db=self.MockDB())

    def test_save_and_get(self, store):
        recorder = TrajectoryRecorder(task_description="save test")
        recorder.record_step(action="test", tool="pytest", result_summary="ok")
        trajectory = recorder.finalize(success=True, outcome_summary="done")

        store.save(trajectory)
        retrieved = store.get(trajectory.id)
        assert retrieved is not None
        assert retrieved.task_description == "save test"
        assert retrieved.success is True
        assert len(retrieved.steps) == 1

    def test_find_successful(self, store):
        # Save 2 successful and 1 failed
        for i, success in enumerate([True, True, False]):
            recorder = TrajectoryRecorder(task_description=f"task {i}")
            recorder.record_step(action="step", tool="tool")
            trajectory = recorder.finalize(success=success, outcome_summary=f"result {i}")
            store.save(trajectory)

        successful = store.find_successful()
        assert len(successful) == 2

    def test_find_successful_with_query(self, store):
        for desc in ["fix python bug", "deploy to prod", "fix javascript error"]:
            recorder = TrajectoryRecorder(task_description=desc)
            recorder.record_step(action="do", tool="t")
            store.save(recorder.finalize(success=True, outcome_summary="done"))

        results = store.find_successful(task_query="fix")
        assert len(results) == 2

    def test_find_by_hash(self, store):
        recorder = TrajectoryRecorder(task_description="hash test")
        recorder.record_step(action="x", tool="y")
        trajectory = recorder.finalize(success=True)
        store.save(trajectory)

        found = store.find_by_hash(trajectory.trajectory_hash_val)
        assert found is not None
        assert found.id == trajectory.id

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None
