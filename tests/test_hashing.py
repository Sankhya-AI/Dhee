"""Tests for engram.skills.hashing — determinism, normalization, order sensitivity."""

import pytest

from dhee.skills.hashing import (
    content_hash,
    skill_signature_hash,
    stable_json,
    trajectory_hash,
)


class TestStableJson:
    def test_deterministic_output(self):
        obj = {"b": 2, "a": 1, "c": [3, 2, 1]}
        assert stable_json(obj) == stable_json(obj)

    def test_key_order_irrelevant(self):
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        assert stable_json(a) == stable_json(b)

    def test_no_whitespace(self):
        result = stable_json({"key": "value"})
        assert " " not in result


class TestContentHash:
    def test_deterministic(self):
        assert content_hash("hello world") == content_hash("hello world")

    def test_case_insensitive(self):
        assert content_hash("Hello World") == content_hash("hello world")

    def test_whitespace_normalized(self):
        assert content_hash("  hello world  ") == content_hash("hello world")

    def test_different_content_different_hash(self):
        assert content_hash("hello") != content_hash("world")


class TestTrajectoryHash:
    def test_deterministic(self):
        steps = [
            {"action": "search", "tool": "grep", "args": {"pattern": "error"}},
            {"action": "edit", "tool": "write", "args": {"file": "main.py"}},
        ]
        assert trajectory_hash(steps) == trajectory_hash(steps)

    def test_result_variations_ignored(self):
        """Same actions with different results should produce the same hash."""
        steps_a = [
            {"action": "search", "tool": "grep", "args": {"pattern": "error"}, "result": "found 3"},
        ]
        steps_b = [
            {"action": "search", "tool": "grep", "args": {"pattern": "error"}, "result": "found 5"},
        ]
        assert trajectory_hash(steps_a) == trajectory_hash(steps_b)

    def test_different_actions_different_hash(self):
        steps_a = [{"action": "search", "tool": "grep", "args": {}}]
        steps_b = [{"action": "edit", "tool": "write", "args": {}}]
        assert trajectory_hash(steps_a) != trajectory_hash(steps_b)

    def test_order_sensitive(self):
        """Step order matters for trajectory identity."""
        step_a = {"action": "search", "tool": "grep", "args": {"pattern": "x"}}
        step_b = {"action": "edit", "tool": "write", "args": {"file": "y"}}
        assert trajectory_hash([step_a, step_b]) != trajectory_hash([step_b, step_a])

    def test_empty_steps(self):
        assert trajectory_hash([]) == trajectory_hash([])


class TestSkillSignatureHash:
    def test_deterministic(self):
        h = skill_signature_hash(
            preconditions=["repo exists"],
            steps=["search for error", "fix bug"],
            tags=["debugging"],
        )
        assert h == skill_signature_hash(
            preconditions=["repo exists"],
            steps=["search for error", "fix bug"],
            tags=["debugging"],
        )

    def test_name_excluded(self):
        """Two skills with same content but different names should have same sig hash."""
        h1 = skill_signature_hash(
            preconditions=["a"], steps=["b"], tags=["c"]
        )
        h2 = skill_signature_hash(
            preconditions=["a"], steps=["b"], tags=["c"]
        )
        assert h1 == h2

    def test_tag_order_irrelevant(self):
        """Tags are sorted, so order shouldn't matter."""
        h1 = skill_signature_hash(
            preconditions=[], steps=["step"], tags=["z", "a", "m"]
        )
        h2 = skill_signature_hash(
            preconditions=[], steps=["step"], tags=["a", "m", "z"]
        )
        assert h1 == h2

    def test_precondition_order_irrelevant(self):
        """Preconditions are sorted."""
        h1 = skill_signature_hash(
            preconditions=["z", "a"], steps=["step"], tags=[]
        )
        h2 = skill_signature_hash(
            preconditions=["a", "z"], steps=["step"], tags=[]
        )
        assert h1 == h2

    def test_step_order_sensitive(self):
        """Steps are NOT sorted — order matters."""
        h1 = skill_signature_hash(
            preconditions=[], steps=["first", "second"], tags=[]
        )
        h2 = skill_signature_hash(
            preconditions=[], steps=["second", "first"], tags=[]
        )
        assert h1 != h2

    def test_different_content_different_hash(self):
        h1 = skill_signature_hash(
            preconditions=["a"], steps=["b"], tags=["c"]
        )
        h2 = skill_signature_hash(
            preconditions=["x"], steps=["y"], tags=["z"]
        )
        assert h1 != h2
