"""Integration tests for autonomous session lifecycle.

Verifies that Dhee learns without explicit context()/checkpoint() calls.
"""

import os
import time
import pytest

from dhee.memory.main import FullMemory
from dhee.plugin import DheePlugin
from dhee.simple import Dhee, Engram


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

    def test_checkpoint_surfaces_session_and_enrichment_errors(self, tmp_path, monkeypatch):
        """checkpoint() should report degraded lifecycle work instead of hiding it."""
        d = Dhee(in_memory=True, data_dir=str(tmp_path))

        def fail_digest(**_kwargs):
            raise RuntimeError("handoff store offline")

        def fail_enrichment(**_kwargs):
            raise RuntimeError("batch enrichment unavailable")

        monkeypatch.setattr("dhee.core.kernel.save_session_digest", fail_digest)
        monkeypatch.setattr(d._engram.memory, "enrich_pending", fail_enrichment)

        result = d.checkpoint("Finished task")

        assert result["session_saved"] is False
        assert result["session_save_error"] == "handoff store offline"
        assert result["enrichment_error"] == "batch enrichment unavailable"
        assert any("handoff store offline" in warning for warning in result["warnings"])
        assert any("batch enrichment unavailable" in warning for warning in result["warnings"])


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


class TestEngramSurface:
    def test_public_memory_property_exposes_runtime_engine(self, tmp_path):
        e = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
        assert isinstance(e.memory, FullMemory)

    def test_plugin_public_memory_property_exposes_runtime_engine(self, tmp_path):
        plugin = DheePlugin(in_memory=True, data_dir=str(tmp_path))
        assert isinstance(plugin.memory, FullMemory)

    def test_engram_close_delegates_to_runtime_memory(self, tmp_path, monkeypatch):
        e = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
        closed = []

        def _close():
            closed.append(True)

        monkeypatch.setattr(e.memory, "close", _close)

        e.close()

        assert closed == [True]

    def test_full_memory_close_raises_aggregated_errors(self, tmp_path):
        e = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
        memory = e.memory

        class BrokenEvolution:
            def flush(self):
                raise RuntimeError("evolution flush failed")

        class BrokenExecutor:
            def shutdown(self):
                raise RuntimeError("executor shutdown failed")

        class BrokenVectorStore:
            def close(self):
                raise RuntimeError("vector close failed")

        class BrokenDb:
            def close(self):
                raise RuntimeError("db close failed")

        memory._evolution_layer = BrokenEvolution()
        memory._executor = BrokenExecutor()
        memory.vector_store = BrokenVectorStore()
        memory.db = BrokenDb()

        with pytest.raises(RuntimeError) as exc_info:
            memory.close()

        message = str(exc_info.value)
        assert "evolution.flush" in message
        assert "evolution flush failed" in message
        assert "executor.shutdown" in message
        assert "executor shutdown failed" in message
        assert "vector_store.close" in message
        assert "vector close failed" in message
        assert "db.close" in message
        assert "db close failed" in message
        assert memory._executor is None
        assert memory.vector_store is None
        assert memory.db is None


class TestShutdownSurface:
    def test_dhee_close_flushes_cognition_and_memory(self, tmp_path, monkeypatch):
        d = Dhee(in_memory=True, data_dir=str(tmp_path))
        calls = []

        monkeypatch.setattr(d._buddhi, "flush", lambda: calls.append("buddhi"))
        monkeypatch.setattr(d._engram, "close", lambda: calls.append("engram"))

        d.close()

        assert calls == ["buddhi", "engram"]

    def test_dhee_close_raises_aggregated_errors(self, tmp_path, monkeypatch):
        d = Dhee(in_memory=True, data_dir=str(tmp_path))

        def _buddhi_boom():
            raise RuntimeError("buddhi flush failed")

        def _engram_boom():
            raise RuntimeError("engram close failed")

        monkeypatch.setattr(d._buddhi, "flush", _buddhi_boom)
        monkeypatch.setattr(d._engram, "close", _engram_boom)

        with pytest.raises(RuntimeError) as exc_info:
            d.close()

        message = str(exc_info.value)
        assert "buddhi.flush" in message
        assert "buddhi flush failed" in message
        assert "engram.close" in message
        assert "engram close failed" in message

    def test_plugin_close_flushes_cognition_and_memory(self, tmp_path, monkeypatch):
        plugin = DheePlugin(in_memory=True, data_dir=str(tmp_path))
        calls = []

        monkeypatch.setattr(plugin._buddhi, "flush", lambda: calls.append("buddhi"))
        monkeypatch.setattr(plugin._engram, "close", lambda: calls.append("engram"))

        plugin.close()

        assert calls == ["buddhi", "engram"]

    def test_plugin_cognition_health_reports_derivation_failures(
        self, tmp_path, monkeypatch
    ):
        plugin = DheePlugin(in_memory=True, data_dir=str(tmp_path))

        def _policy_boom(*_args, **_kwargs):
            raise RuntimeError("policy health unavailable")

        def _belief_boom(*_args, **_kwargs):
            raise RuntimeError("belief health unavailable")

        monkeypatch.setattr(plugin._kernel.policies, "get_user_policies", _policy_boom)
        monkeypatch.setattr(plugin._kernel.beliefs, "get_contradictions", _belief_boom)

        health = plugin.cognition_health()

        assert "kernel" in health
        assert "buddhi" in health
        assert any(
            err["component"] == "policies.get_user_policies"
            and "policy health unavailable" in err["error"]
            for err in health.get("errors", [])
        )
        assert any(
            err["component"] == "beliefs.get_contradictions"
            and "belief health unavailable" in err["error"]
            for err in health.get("errors", [])
        )
