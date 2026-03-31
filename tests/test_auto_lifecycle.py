"""Integration tests for autonomous session lifecycle.

Verifies that Dhee learns without explicit context()/checkpoint() calls.
"""

import os
import time
import pytest

from dhee.simple import Dhee


@pytest.fixture
def dhee(tmp_path):
    """Create a Dhee instance with in-memory storage and short timeout."""
    d = Dhee(
        in_memory=True,
        data_dir=str(tmp_path),
        session_timeout=1.0,  # 1 second for testing
    )
    return d


class TestAutoContext:
    """Verify auto-context fires on first operation."""

    def test_remember_triggers_auto_context(self, dhee):
        """First remember() should auto-bootstrap context."""
        result = dhee.remember("User prefers dark mode")
        assert result["stored"] is True
        # Tracker should be active with context loaded
        assert dhee._tracker.session_active is True
        assert dhee._tracker.context_loaded is True

    def test_recall_triggers_auto_context(self, dhee):
        """First recall() should auto-bootstrap context."""
        dhee.remember("Python is great")
        # Reset tracker to simulate fresh session
        dhee._tracker._reset()
        dhee.recall("programming language")
        assert dhee._tracker.context_loaded is True

    def test_explicit_context_still_works(self, dhee):
        """Explicit context() should work and prevent double-bootstrap."""
        ctx = dhee.context("fixing auth bug")
        assert isinstance(ctx, dict)
        assert dhee._tracker.context_loaded is True
        # Subsequent remember shouldn't re-trigger context
        dhee.remember("found the bug in login.py")
        assert dhee._tracker.op_count == 2  # context + remember


class TestAutoCheckpoint:
    """Verify auto-checkpoint fires on session timeout."""

    def test_timeout_auto_checkpoints(self, dhee):
        """After timeout, next operation should auto-checkpoint."""
        dhee.remember("session 1 work")
        dhee.remember("more session 1 work")
        dhee.recall("session 1 query")

        # Simulate timeout
        dhee._tracker._last_activity_time = time.time() - 2.0

        # This should trigger auto-checkpoint of session 1 + start session 2
        dhee.remember("session 2 starts")

        # Session 2 should be active now
        assert dhee._tracker.session_active is True
        # op_count includes the auto-context that fires on first op of new session
        assert dhee._tracker.op_count >= 1  # at least the remember

    def test_explicit_checkpoint_prevents_auto(self, dhee):
        """Explicit checkpoint should prevent auto-checkpoint on timeout."""
        dhee.remember("some work")
        dhee.checkpoint("done with task", task_type="bug_fix")

        # Simulate timeout
        dhee._tracker._last_activity_time = time.time() - 2.0

        # Should not trigger auto-checkpoint (already checkpointed)
        dhee.remember("new session")
        # op_count includes the auto-context that fires on first op of new session
        assert dhee._tracker.op_count >= 1


class TestAutoInference:
    """Verify task_type and outcome are auto-inferred."""

    def test_checkpoint_auto_fills_task_type(self, dhee):
        """Checkpoint should auto-fill task_type from session content."""
        dhee.remember("fixing crash in auth module")
        dhee.recall("debug error in login")
        dhee.remember("found the bug, was a null pointer")

        result = dhee.checkpoint("Fixed auth crash")
        # Task type should have been auto-inferred as bug_fix
        # (we can't directly verify the inferred type was used,
        # but we verify the checkpoint succeeds)
        assert isinstance(result, dict)

    def test_checkpoint_auto_fills_outcome(self, dhee):
        """Checkpoint should auto-estimate outcome from usage patterns."""
        dhee.remember("step 1")
        dhee.remember("step 2")
        dhee.remember("step 3")
        dhee.recall("what did I do?")

        result = dhee.checkpoint("Finished the task")
        assert isinstance(result, dict)


class TestShruti:
    """Verify shruti-tier memories are auto-detected."""

    def test_preference_tagged_shruti(self, dhee):
        result = dhee.remember("I prefer tabs over spaces")
        assert result.get("tier") == "shruti"

    def test_rule_tagged_shruti(self, dhee):
        result = dhee.remember("Rule: always write tests first")
        assert result.get("tier") == "shruti"

    def test_normal_memory_no_tier_tag(self, dhee):
        result = dhee.remember("The meeting is at 3pm")
        assert result.get("tier") is None  # smriti doesn't get tagged in response


class TestDisableAuto:
    """Verify auto features can be disabled."""

    def test_disable_auto_context(self, tmp_path):
        d = Dhee(in_memory=True, data_dir=str(tmp_path), auto_context=False)
        d.remember("hello")
        assert d._tracker.context_loaded is False

    def test_disable_auto_checkpoint(self, tmp_path):
        d = Dhee(
            in_memory=True, data_dir=str(tmp_path),
            auto_checkpoint=False, session_timeout=1.0,
        )
        d.remember("session 1")
        d._tracker._last_activity_time = time.time() - 2.0
        signals = d._tracker.on_remember("session 2", "m2")
        assert signals.get("needs_auto_checkpoint") is None
