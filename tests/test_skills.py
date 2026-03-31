"""Tests for engram.skills — schema, store, executor, outcomes, discovery."""

import os
import pytest
import tempfile

from dhee.skills.schema import Skill, TrajectoryStep, Trajectory
from dhee.skills.store import SkillStore
from dhee.skills.executor import SkillExecutor
from dhee.skills.outcomes import OutcomeTracker, compute_confidence
from dhee.skills.discovery import discover_skill_dirs, scan_skill_files, load_skill_file


# ── Schema tests ──


class TestSkillSchema:
    def test_skill_roundtrip(self):
        """Serialize to SKILL.md and parse back."""
        skill = Skill(
            name="Fix Typos",
            description="Find and fix typos in code",
            tags=["debugging", "text"],
            preconditions=["repo exists", "file has content"],
            steps=["search for misspellings", "apply corrections", "run tests"],
            confidence=0.75,
            source="authored",
        )
        md = skill.to_skill_md()
        assert "---" in md
        assert "Fix Typos" in md

        parsed = Skill.from_skill_md(md)
        assert parsed.name == "Fix Typos"
        assert parsed.description == "Find and fix typos in code"
        assert parsed.tags == ["debugging", "text"]
        assert parsed.preconditions == ["repo exists", "file has content"]
        assert len(parsed.steps) == 3
        assert parsed.confidence == 0.75
        assert parsed.source == "authored"

    def test_skill_signature_hash_computed(self):
        """Signature hash should be auto-computed."""
        skill = Skill(
            name="Test",
            preconditions=["a"],
            steps=["b"],
            tags=["c"],
        )
        assert len(skill.signature_hash) == 64  # SHA-256 hex

    def test_skill_to_dict(self):
        skill = Skill(name="Test", description="A test skill")
        d = skill.to_dict()
        assert d["name"] == "Test"
        assert "id" in d
        assert "confidence" in d

    def test_skill_from_md_no_frontmatter(self):
        """Content without frontmatter treated as body."""
        skill = Skill.from_skill_md("Just some markdown content")
        assert skill.body_markdown == "Just some markdown content"

    def test_skill_from_md_empty(self):
        skill = Skill.from_skill_md("")
        assert skill.body_markdown == ""


class TestTrajectorySchema:
    def test_trajectory_step_to_dict(self):
        step = TrajectoryStep(
            action="search",
            tool="grep",
            args={"pattern": "error"},
            result_summary="found 3 matches",
        )
        d = step.to_dict()
        assert d["action"] == "search"
        assert d["tool"] == "grep"

    def test_trajectory_compute_hash(self):
        t = Trajectory(
            task_description="fix a bug",
            steps=[
                TrajectoryStep(action="search", tool="grep", args={"pattern": "error"}),
                TrajectoryStep(action="edit", tool="write", args={"file": "main.py"}),
            ],
        )
        h = t.compute_hash()
        assert len(h) == 64
        # Deterministic
        assert t.compute_hash() == h

    def test_trajectory_to_dict(self):
        t = Trajectory(task_description="test task")
        d = t.to_dict()
        assert d["task_description"] == "test task"
        assert "id" in d


# ── Store tests ──


class TestSkillStore:
    @pytest.fixture
    def store(self, tmp_path):
        skill_dir = str(tmp_path / "skills")
        os.makedirs(skill_dir, exist_ok=True)
        return SkillStore(skill_dirs=[skill_dir])

    def test_save_and_get(self, store):
        skill = Skill(name="Test Skill", description="A test")
        store.save(skill)
        retrieved = store.get(skill.id)
        assert retrieved is not None
        assert retrieved.name == "Test Skill"

    def test_save_creates_file(self, store):
        skill = Skill(name="File Test", description="Check file creation")
        store.save(skill)
        filepath = os.path.join(store.primary_dir, f"{skill.id}.skill.md")
        assert os.path.isfile(filepath)

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent-id") is None

    def test_delete(self, store):
        skill = Skill(name="Delete Me")
        store.save(skill)
        assert store.get(skill.id) is not None
        store.delete(skill.id)
        assert store.get(skill.id) is None

    def test_text_search(self, store):
        store.save(Skill(name="Python Debugging", description="Debug Python code", tags=["python"]))
        store.save(Skill(name="JS Linting", description="Lint JavaScript", tags=["javascript"]))
        results = store.search("python", limit=5)
        assert len(results) >= 1
        assert results[0].name == "Python Debugging"

    def test_get_by_signature(self, store):
        skill = Skill(
            name="Unique",
            preconditions=["a"],
            steps=["b"],
            tags=["c"],
        )
        store.save(skill)
        found = store.get_by_signature(skill.signature_hash)
        assert found is not None
        assert found.id == skill.id

    def test_list_all(self, store):
        store.save(Skill(name="S1"))
        store.save(Skill(name="S2"))
        all_skills = store.list_all()
        assert len(all_skills) == 2

    def test_sync_from_filesystem(self, tmp_path):
        skill_dir = str(tmp_path / "sync_skills")
        os.makedirs(skill_dir, exist_ok=True)

        # Write a skill file manually
        skill = Skill(name="Manual Skill", description="Manually written")
        filepath = os.path.join(skill_dir, f"{skill.id}.skill.md")
        with open(filepath, "w") as f:
            f.write(skill.to_skill_md())

        # Create store and sync
        store = SkillStore(skill_dirs=[skill_dir])
        count = store.sync_from_filesystem()
        assert count == 1
        assert store.get(skill.id) is not None


# ── Executor tests ──


class TestSkillExecutor:
    @pytest.fixture
    def executor(self, tmp_path):
        skill_dir = str(tmp_path / "exec_skills")
        os.makedirs(skill_dir, exist_ok=True)
        store = SkillStore(skill_dirs=[skill_dir])
        return SkillExecutor(store), store

    def test_apply_skill(self, executor):
        exec_, store = executor
        skill = Skill(
            name="Fix Bugs",
            description="Standard bug fix workflow",
            steps=["reproduce", "diagnose", "fix", "test"],
            confidence=0.8,
        )
        store.save(skill)
        result = exec_.apply(skill.id)
        assert result["injected"] is True
        assert "recipe" in result
        assert "Fix Bugs" in result["recipe"]
        assert result["confidence"] == 0.8

    def test_apply_increments_use_count(self, executor):
        exec_, store = executor
        skill = Skill(name="Counter Test", use_count=0)
        store.save(skill)
        exec_.apply(skill.id)
        updated = store.get(skill.id)
        assert updated.use_count == 1

    def test_apply_nonexistent(self, executor):
        exec_, _ = executor
        result = exec_.apply("nonexistent")
        assert result["injected"] is False

    def test_search_and_apply(self, executor):
        exec_, store = executor
        skill = Skill(
            name="Python Debugging",
            description="Debug Python errors",
            tags=["python", "debug"],
            confidence=0.7,
        )
        store.save(skill)
        result = exec_.search_and_apply("debug python")
        assert result["injected"] is True

    def test_search(self, executor):
        exec_, store = executor
        store.save(Skill(name="Skill A", description="Do A", tags=["a"]))
        store.save(Skill(name="Skill B", description="Do B", tags=["b"]))
        results = exec_.search("Skill")
        assert len(results) >= 1


# ── Outcome tracking tests ──


class TestOutcomeTracker:
    @pytest.fixture
    def tracker(self, tmp_path):
        skill_dir = str(tmp_path / "outcome_skills")
        os.makedirs(skill_dir, exist_ok=True)
        store = SkillStore(skill_dirs=[skill_dir])
        return OutcomeTracker(store), store

    def test_log_success(self, tracker):
        tr, store = tracker
        skill = Skill(name="Test", confidence=0.5)
        store.save(skill)
        result = tr.log_outcome(skill.id, success=True)
        assert result["success"] is True
        assert result["new_confidence"] > 0

    def test_log_failure_lowers_confidence(self, tracker):
        tr, store = tracker
        # Start with balanced counts so a failure clearly lowers confidence
        skill = Skill(name="Test", confidence=0.5, success_count=5, fail_count=5)
        store.save(skill)
        result = tr.log_outcome(skill.id, success=False)
        assert result["new_confidence"] < result["old_confidence"]

    def test_log_nonexistent(self, tracker):
        tr, _ = tracker
        result = tr.log_outcome("nonexistent", success=True)
        assert "error" in result


class TestComputeConfidence:
    def test_neutral_prior(self):
        assert compute_confidence(0, 0) == 0.5

    def test_all_success_high(self):
        c = compute_confidence(100, 0)
        assert c > 0.5

    def test_all_failure_low(self):
        c = compute_confidence(0, 100)
        assert c < 0.5

    def test_asymmetric_penalty(self):
        """Equal success/fail should be below 0.5 due to asymmetric weighting."""
        c = compute_confidence(10, 10)
        assert c < 0.5

    def test_bounded(self):
        assert 0.0 <= compute_confidence(1000, 0) <= 1.0
        assert 0.0 <= compute_confidence(0, 1000) <= 1.0


# ── Discovery tests ──


class TestDiscovery:
    def test_discover_skill_dirs_global(self):
        dirs = discover_skill_dirs()
        assert any(".dhee/skills" in d for d in dirs)

    def test_discover_skill_dirs_with_repo(self, tmp_path):
        dirs = discover_skill_dirs(repo_path=str(tmp_path))
        assert any(".dhee/skills" in d for d in dirs)
        assert str(tmp_path) in dirs[0]

    def test_scan_skill_files(self, tmp_path):
        skill_dir = str(tmp_path / "skills")
        os.makedirs(skill_dir)
        # Create a skill file
        with open(os.path.join(skill_dir, "test-id.skill.md"), "w") as f:
            f.write("---\nname: Test\n---\nBody")
        results = scan_skill_files([skill_dir])
        assert len(results) == 1
        assert results[0][1] == "test-id"

    def test_load_skill_file(self, tmp_path):
        skill = Skill(name="Load Test", description="Test loading")
        filepath = str(tmp_path / "test.skill.md")
        with open(filepath, "w") as f:
            f.write(skill.to_skill_md())
        loaded = load_skill_file(filepath)
        assert loaded.name == "Load Test"
