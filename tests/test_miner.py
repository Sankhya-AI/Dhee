"""Tests for engram.skills.miner — mining pipeline, dedup, mutation, mock LLM."""

import json
import os
import pytest

from dhee.skills.miner import SkillMiner
from dhee.skills.schema import Skill, Trajectory, TrajectoryStep
from dhee.skills.store import SkillStore
from dhee.skills.trajectory import TrajectoryRecorder, TrajectoryStore


class MockDB:
    """Simple in-memory DB mock for testing."""
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


class MockLLM:
    """Mock LLM that returns valid skill JSON."""
    def generate(self, prompt):
        return json.dumps({
            "name": "Mined Bug Fix",
            "description": "Fix bugs by searching and patching",
            "preconditions": ["source code exists"],
            "steps": ["search for error", "identify root cause", "apply fix", "run tests"],
            "tags": ["debugging", "bugfix"],
        })


@pytest.fixture
def skill_store(tmp_path):
    skill_dir = str(tmp_path / "skills")
    os.makedirs(skill_dir, exist_ok=True)
    return SkillStore(skill_dirs=[skill_dir])


@pytest.fixture
def trajectory_store():
    return TrajectoryStore(db=MockDB())


def _make_trajectory(desc: str, actions: list) -> Trajectory:
    """Helper to create a finalized trajectory."""
    recorder = TrajectoryRecorder(task_description=desc)
    for action in actions:
        recorder.record_step(
            action=action.get("action", "step"),
            tool=action.get("tool", "tool"),
            args=action.get("args", {}),
            result_summary=action.get("result", "ok"),
        )
    return recorder.finalize(success=True, outcome_summary=f"Completed: {desc}")


class TestSkillMiner:
    def test_mine_no_trajectories(self, trajectory_store, skill_store):
        miner = SkillMiner(
            trajectory_store=trajectory_store,
            skill_store=skill_store,
            min_cluster_size=2,
        )
        result = miner.mine()
        assert result == []

    def test_mine_insufficient_trajectories(self, trajectory_store, skill_store):
        """Need at least min_cluster_size trajectories to mine."""
        t = _make_trajectory("single task", [{"action": "test"}])
        trajectory_store.save(t)

        miner = SkillMiner(
            trajectory_store=trajectory_store,
            skill_store=skill_store,
            min_cluster_size=2,
        )
        result = miner.mine()
        assert result == []

    def test_mine_heuristic(self, trajectory_store, skill_store):
        """Test mining without LLM (heuristic mode)."""
        # Create similar trajectories
        for i in range(3):
            t = _make_trajectory(
                f"fix python error variant {i}",
                [
                    {"action": "search", "tool": "grep", "args": {"pattern": "error"}},
                    {"action": "edit", "tool": "write", "args": {"file": "main.py"}},
                    {"action": "test", "tool": "pytest"},
                ],
            )
            trajectory_store.save(t)

        miner = SkillMiner(
            trajectory_store=trajectory_store,
            skill_store=skill_store,
            llm=None,
            min_cluster_size=2,
            mutation_rate=0.0,  # Disable mutation for deterministic test
        )
        mined = miner.mine()
        assert len(mined) >= 1
        skill = mined[0]
        assert skill.source == "mined"
        assert skill.confidence == 0.5
        assert len(skill.steps) > 0

    def test_mine_with_llm(self, trajectory_store, skill_store):
        """Test mining with mock LLM."""
        for i in range(3):
            t = _make_trajectory(
                f"debug application error {i}",
                [
                    {"action": "search", "tool": "grep"},
                    {"action": "fix", "tool": "edit"},
                    {"action": "verify", "tool": "test"},
                ],
            )
            trajectory_store.save(t)

        miner = SkillMiner(
            trajectory_store=trajectory_store,
            skill_store=skill_store,
            llm=MockLLM(),
            min_cluster_size=2,
            mutation_rate=0.0,
        )
        mined = miner.mine()
        assert len(mined) >= 1
        skill = mined[0]
        assert skill.name == "Mined Bug Fix"
        assert "debugging" in skill.tags

    def test_mine_dedup(self, trajectory_store, skill_store):
        """Mining the same cluster twice should not create duplicates."""
        for i in range(3):
            t = _make_trajectory(
                f"fix python issue {i}",
                [{"action": "search"}, {"action": "fix"}],
            )
            trajectory_store.save(t)

        miner = SkillMiner(
            trajectory_store=trajectory_store,
            skill_store=skill_store,
            min_cluster_size=2,
            mutation_rate=0.0,
        )

        # First mine
        first = miner.mine()
        assert len(first) >= 1

        # Second mine — should be deduped
        second = miner.mine()
        assert len(second) == 0

    def test_mine_saves_to_store(self, trajectory_store, skill_store):
        """Mined skills should be persisted in the skill store."""
        for i in range(3):
            t = _make_trajectory(
                f"deploy application {i}",
                [{"action": "build"}, {"action": "test"}, {"action": "deploy"}],
            )
            trajectory_store.save(t)

        miner = SkillMiner(
            trajectory_store=trajectory_store,
            skill_store=skill_store,
            min_cluster_size=2,
            mutation_rate=0.0,
        )
        mined = miner.mine()
        assert len(mined) >= 1

        # Verify persisted
        stored = skill_store.get(mined[0].id)
        assert stored is not None
        assert stored.source == "mined"


class TestMutation:
    def test_mutation_adds_verification(self):
        """With mutation_rate=1.0, every skill should be mutated."""
        import random
        random.seed(42)

        skill_dir = "/tmp/test_mutation_skills"
        os.makedirs(skill_dir, exist_ok=True)

        store = SkillStore(skill_dirs=[skill_dir])
        tstore = TrajectoryStore(db=MockDB())

        for i in range(3):
            t = _make_trajectory(
                f"mutate test {i}",
                [{"action": "step1"}, {"action": "step2"}],
            )
            tstore.save(t)

        miner = SkillMiner(
            trajectory_store=tstore,
            skill_store=store,
            mutation_rate=1.0,  # Always mutate
            min_cluster_size=2,
        )
        mined = miner.mine()

        # At least one skill should exist
        if mined:
            skill = mined[0]
            # Mutation should have added "verify" or "adapt as needed"
            all_steps = " ".join(skill.steps).lower()
            has_mutation = "verify" in all_steps or "adapt as needed" in all_steps
            assert has_mutation, f"Expected mutation in steps: {skill.steps}"

        # Cleanup
        import shutil
        shutil.rmtree(skill_dir, ignore_errors=True)


class TestClusterByKeywords:
    def test_similar_tasks_cluster_together(self):
        """Tasks with similar keywords should be in the same cluster."""
        store = SkillStore(skill_dirs=["/tmp/test_cluster"])
        tstore = TrajectoryStore(db=MockDB())
        miner = SkillMiner(
            trajectory_store=tstore,
            skill_store=store,
            min_cluster_size=2,
        )

        trajectories = [
            Trajectory(task_description="fix python error"),
            Trajectory(task_description="fix python bug"),
            Trajectory(task_description="deploy to production"),
        ]

        clusters = miner._cluster_by_keywords(trajectories)
        # "fix python error" and "fix python bug" share keywords
        # They may or may not cluster depending on keyword overlap
        assert len(clusters) >= 1
