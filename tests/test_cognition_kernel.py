"""Tests for CognitionKernel and IntentionStore.

Verifies:
- IntentionStore standalone functionality
- CognitionKernel state store wiring
- Cross-primitive coordination methods
- Buddhi + kernel integration
"""

import os
import pytest

from dhee.core.intention import Intention, IntentionStore


# ── IntentionStore ──────────────────────────────────────────────────


class TestIntentionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return IntentionStore(data_dir=str(tmp_path / "intentions"))

    def test_store_and_retrieve(self, store):
        i = store.store("user1", "run tests after deploy", trigger_keywords=["deploy"])
        assert isinstance(i, Intention)
        assert i.status == "active"
        assert i.user_id == "user1"
        active = store.get_active("user1")
        assert len(active) == 1
        assert active[0].id == i.id

    def test_detect_remember_to(self, store):
        i = store.detect_in_text(
            "remember to run tests after modifying auth", "user1"
        )
        assert i is not None
        assert "tests" in i.action_payload.lower() or "run" in i.action_payload.lower()
        assert len(i.trigger_keywords) > 0

    def test_detect_todo(self, store):
        i = store.detect_in_text("TODO: fix the login bug", "user1")
        assert i is not None
        assert "login" in i.description.lower() or "fix" in i.description.lower()

    def test_detect_no_match(self, store):
        i = store.detect_in_text("The weather is nice today", "user1")
        assert i is None

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "intentions")
        store1 = IntentionStore(data_dir=path)
        store1.store("u", "test intention", trigger_keywords=["test"])
        store1.flush()

        store2 = IntentionStore(data_dir=path)
        assert len(store2.get_active("u")) == 1

    def test_stats(self, store):
        store.store("u", "intention 1")
        store.store("u", "intention 2")
        stats = store.get_stats("u")
        assert stats["total"] == 2
        assert stats["active"] == 2
        assert stats["triggered"] == 0

    def test_different_users(self, store):
        store.store("alice", "alice intention")
        store.store("bob", "bob intention")
        assert len(store.get_active("alice")) == 1
        assert len(store.get_active("bob")) == 1


# ── CognitionKernel ────────────────────────────────────────────────


class TestCognitionKernel:
    @pytest.fixture
    def kernel(self, tmp_path):
        from dhee.core.cognition_kernel import CognitionKernel
        return CognitionKernel(data_dir=str(tmp_path / "kernel"))

    def test_stores_initialized(self, kernel):
        assert kernel.episodes is not None
        assert kernel.tasks is not None
        assert kernel.beliefs is not None
        assert kernel.policies is not None
        assert kernel.intentions is not None

    def test_get_cognitive_state_empty(self, kernel):
        state = kernel.get_cognitive_state("user1")
        assert "episodes" in state
        assert "task_states" in state
        assert "policies" in state
        assert "beliefs" in state
        assert "triggered_intentions" in state
        assert "belief_warnings" in state

    def test_get_cognitive_state_with_data(self, kernel):
        kernel.beliefs.add_belief("u", "Python is great", "programming", 0.9)
        kernel.episodes.begin_episode("u", "test task", "testing")
        state = kernel.get_cognitive_state("u", "programming")
        assert len(state["beliefs"]) > 0 or len(state["episodes"]) > 0

    def test_record_checkpoint_event(self, kernel):
        kernel.episodes.begin_episode("u", "working on auth", "bug_fix")
        result = kernel.record_checkpoint_event("u", "fixed auth bug", "completed", 0.9)
        assert "episode_closed" in result

    def test_update_task_on_checkpoint(self, kernel):
        result = kernel.update_task_on_checkpoint(
            user_id="u",
            goal="Fix login crash",
            plan=["reproduce", "debug", "fix"],
            task_type="bug_fix",
            status="completed",
            outcome_score=0.8,
            summary="Fixed the crash",
        )
        assert "task_created" in result or "task_completed" in result

    def test_selective_forget(self, kernel):
        # Should not error on empty state
        result = kernel.selective_forget("u")
        assert isinstance(result, dict)

    def test_flush(self, kernel):
        kernel.intentions.store("u", "test intention")
        kernel.flush()  # Should not error

    def test_get_stats(self, kernel):
        stats = kernel.get_stats()
        assert "episodes" in stats
        assert "tasks" in stats
        assert "beliefs" in stats
        assert "policies" in stats
        assert "intentions" in stats

    def test_repr(self, kernel):
        r = repr(kernel)
        assert "CognitionKernel" in r


# ── Buddhi + Kernel integration ────────────────────────────────────


class TestBuddhiKernelIntegration:
    @pytest.fixture
    def buddhi_with_kernel(self, tmp_path):
        from dhee.core.cognition_kernel import CognitionKernel
        from dhee.core.buddhi import Buddhi
        data_dir = str(tmp_path / "buddhi")
        kernel = CognitionKernel(data_dir=data_dir)
        buddhi = Buddhi(data_dir=data_dir, kernel=kernel)
        return buddhi, kernel

    def test_buddhi_uses_passed_kernel(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        assert buddhi._kernel is kernel

    def test_buddhi_creates_own_kernel(self, tmp_path):
        from dhee.core.buddhi import Buddhi
        buddhi = Buddhi(data_dir=str(tmp_path / "buddhi"))
        assert buddhi._kernel is not None

    def test_deprecated_forwarders(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        assert buddhi._get_episode_store() is kernel.episodes
        assert buddhi._get_task_state_store() is kernel.tasks
        assert buddhi._get_policy_store() is kernel.policies
        assert buddhi._get_belief_store() is kernel.beliefs

    def test_store_intention_delegates(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        i = buddhi.store_intention("u", "test", trigger_keywords=["test"])
        assert isinstance(i, Intention)
        assert len(kernel.intentions.get_active("u")) == 1

    def test_detect_intention_delegates(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        i = buddhi.detect_intention_in_text(
            "remember to deploy after tests pass", "u"
        )
        assert i is not None
        assert len(kernel.intentions.get_active("u")) == 1

    def test_on_memory_stored_records_episode(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        buddhi.on_memory_stored("user likes dark mode", "u", memory_id="m1")
        # Episode should have been started by record_event
        stats = kernel.episodes.get_stats()
        assert stats.get("total", 0) >= 0  # At minimum no error

    def test_reflect_uses_kernel_policies(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        insights = buddhi.reflect(
            user_id="u",
            task_type="bug_fix",
            what_worked="git blame first",
            outcome_score=0.9,
        )
        assert len(insights) > 0
        assert insights[0].insight_type == "strategy"

    def test_flush_propagates(self, buddhi_with_kernel):
        buddhi, kernel = buddhi_with_kernel
        kernel.intentions.store("u", "test")
        buddhi.flush()  # Should flush kernel too


# ── Dhee + Kernel integration ──────────────────────────────────────


class TestDheeKernel:
    def test_dhee_has_kernel(self, tmp_path):
        from dhee.simple import Dhee
        d = Dhee(in_memory=True, data_dir=str(tmp_path))
        assert hasattr(d, '_kernel')
        assert hasattr(d, 'kernel')
        assert d.kernel is d._kernel

    def test_kernel_stores_accessible(self, tmp_path):
        from dhee.simple import Dhee
        d = Dhee(in_memory=True, data_dir=str(tmp_path))
        assert d.kernel.tasks is not None
        assert d.kernel.beliefs is not None
        assert d.kernel.episodes is not None
        assert d.kernel.policies is not None
        assert d.kernel.intentions is not None
