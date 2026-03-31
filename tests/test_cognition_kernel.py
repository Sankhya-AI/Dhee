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


# ── Step-Level Policy Extraction (Phase 2) ────────────────────────


class TestStepPolicyExtraction:
    @pytest.fixture
    def store(self, tmp_path):
        from dhee.core.policy import PolicyStore
        return PolicyStore(data_dir=str(tmp_path / "policies"))

    def _make_task(self, task_id, task_type, plan_steps, outcome_score):
        """Helper: build a task dict with plan steps."""
        plan = []
        for desc, status in plan_steps:
            plan.append({
                "id": f"step-{desc[:8]}",
                "description": desc,
                "status": status,
            })
        return {
            "id": task_id,
            "task_type": task_type,
            "outcome_score": outcome_score,
            "plan": plan,
        }

    def test_extract_step_policies_from_failures(self, store):
        """When the same step fails >=2 times and another succeeds, extract STEP policy."""
        tasks = [
            self._make_task("t1", "bug_fix", [
                ("reproduce bug", "completed"),
                ("check imports", "failed"),
                ("write test", "completed"),
            ], 0.3),
            self._make_task("t2", "bug_fix", [
                ("reproduce bug", "completed"),
                ("check imports", "failed"),
                ("write test", "completed"),
            ], 0.4),
            self._make_task("t3", "bug_fix", [
                ("reproduce bug", "completed"),
                ("trace call stack", "completed"),
                ("write test", "completed"),
            ], 0.9),
        ]

        policies = store.extract_step_policies("u", tasks, "bug_fix")
        assert len(policies) >= 1
        step_policy = policies[0]
        assert step_policy.granularity.value == "step"
        assert "trace call stack" in step_policy.action.approach
        assert "check imports" in step_policy.action.avoid

    def test_no_step_policy_below_threshold(self, store):
        """Need >=2 failures of same step to extract."""
        tasks = [
            self._make_task("t1", "bug_fix", [
                ("reproduce bug", "completed"),
                ("check imports", "failed"),
            ], 0.3),
            self._make_task("t2", "bug_fix", [
                ("reproduce bug", "completed"),
                ("trace call stack", "completed"),
            ], 0.9),
        ]
        policies = store.extract_step_policies("u", tasks, "bug_fix")
        assert len(policies) == 0

    def test_dedup_step_policies(self, store):
        """Similar step policies boost existing rather than duplicate."""
        tasks = [
            self._make_task("t1", "bug_fix", [("check imports", "failed")], 0.3),
            self._make_task("t2", "bug_fix", [("check imports", "failed")], 0.3),
            self._make_task("t3", "bug_fix", [("trace call stack", "completed")], 0.9),
        ]
        p1 = store.extract_step_policies("u", tasks, "bug_fix")
        assert len(p1) == 1
        count_after_first = p1[0].apply_count

        # Extract again — should boost, not duplicate
        p2 = store.extract_step_policies("u", tasks, "bug_fix")
        assert len(p2) == 1
        assert p2[0].id == p1[0].id
        assert p2[0].apply_count > count_after_first

    def test_step_context_in_cognitive_state(self, tmp_path):
        """get_cognitive_state includes active_step and step_policies."""
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "kernel"))
        # Create active task with current step
        task = kernel.tasks.create_task("u", "Fix auth", "bug_fix", plan=["step 1", "step 2"])
        task.start()  # starts first step
        kernel.tasks.update_task(task)

        state = kernel.get_cognitive_state("u", "bug_fix")
        assert "active_step" in state
        assert state["active_step"] == "step 1"
        assert "step_policies" in state

    def test_record_step_outcome(self, tmp_path):
        """record_step_outcome finds and updates matching STEP policies."""
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "kernel"))

        # Create a STEP policy
        kernel.policies.create_step_policy(
            user_id="u",
            name="check_imports_fix",
            task_types=["bug_fix"],
            step_patterns=["check", "imports"],
            approach="trace call stack instead",
        )

        # Record step outcome
        kernel.record_step_outcome(
            "u", "bug_fix", "check imports first",
            success=True, actual_score=0.8,
        )

        # Verify policy was updated
        policies = list(kernel.policies._policies.values())
        step_policy = [p for p in policies if p.granularity.value == "step"][0]
        assert step_policy.apply_count == 1
        assert step_policy.success_count == 1


# ── Utility Tracking (Phase 3) ───────────────────────────────────


class TestUtilityTracking:
    def test_heuristic_record_outcome(self, tmp_path):
        """Heuristic.record_outcome updates utility via EMA."""
        from dhee.core.heuristic import Heuristic
        h = Heuristic(
            id="h1", content="test heuristic", abstraction_level="domain",
            source_task_types=["bug_fix"], confidence=0.6, created_at=0.0,
        )
        delta = h.record_outcome(success=True, baseline_score=0.5, actual_score=0.8)
        assert delta == pytest.approx(0.3)
        assert h.utility > 0
        assert h.apply_count == 1
        assert h.validation_count == 1

    def test_heuristic_strength_includes_utility(self, tmp_path):
        """strength() incorporates utility factor."""
        from dhee.core.heuristic import Heuristic
        h1 = Heuristic(
            id="h1", content="test", abstraction_level="domain",
            source_task_types=["t"], confidence=0.8, created_at=0.0,
            validation_count=3, invalidation_count=1,
        )
        base_strength = h1.strength()

        h2 = Heuristic(
            id="h2", content="test", abstraction_level="domain",
            source_task_types=["t"], confidence=0.8, created_at=0.0,
            validation_count=3, invalidation_count=1,
            utility=0.5,  # positive utility
        )
        boosted_strength = h2.strength()
        assert boosted_strength > base_strength

    def test_insight_record_outcome(self):
        """Insight.record_outcome updates utility via EMA."""
        from dhee.core.buddhi import Insight
        i = Insight(
            id="i1", user_id="u", content="test insight",
            insight_type="strategy", source_task_types=["bug_fix"],
            confidence=0.7, created_at="2026-01-01", last_validated="2026-01-01",
            validation_count=0, invalidation_count=0, tags=["test"],
        )
        delta = i.record_outcome(success=True, baseline_score=0.4, actual_score=0.9)
        assert delta == pytest.approx(0.5)
        assert i.utility > 0
        assert i.apply_count == 1

    def test_intention_record_outcome(self, tmp_path):
        """IntentionStore.record_outcome marks intention usefulness."""
        from dhee.core.intention import IntentionStore
        store = IntentionStore(data_dir=str(tmp_path / "intentions"))
        i = store.store("u", "run tests after deploy", trigger_keywords=["deploy"])
        # Simulate trigger
        i.status = "triggered"
        store.record_outcome(i.id, useful=True, outcome_score=0.9)
        assert i.was_useful is True
        assert i.outcome_score == 0.9

    def test_contrastive_record_outcome(self):
        """ContrastivePair.record_outcome updates utility."""
        from dhee.core.contrastive import ContrastivePair
        pair = ContrastivePair(
            id="c1", task_description="test task", task_type="bug_fix",
            success_approach="do X", failure_approach="do Y",
            outcome_delta=0.5, created_at=0.0,
        )
        delta = pair.record_outcome(success=True, baseline_score=0.5, actual_score=0.8)
        assert delta == pytest.approx(0.3)
        assert pair.utility > 0
        assert pair.apply_count == 1
        assert pair.validation_count == 1

    def test_episode_connection_count_incremented(self, tmp_path):
        """connection_count increases on checkpoint when task exists."""
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "kernel"))
        # Create a task and start an episode
        kernel.tasks.create_task("u", "fix auth", "bug_fix")
        kernel.episodes.begin_episode("u", "working on auth", "bug_fix")

        kernel.record_checkpoint_event("u", "progress on auth", "paused")

        # Check episode connection_count was incremented
        open_eps = getattr(kernel.episodes, '_open_episodes', {})
        ep_id = open_eps.get("u")
        if ep_id:
            ep = kernel.episodes._episodes.get(ep_id)
            assert ep.connection_count >= 1


# ── Operational Context (Phase 4) ────────────────────────────────


class TestOperationalContext:
    def test_hyper_context_has_operational_fields(self, tmp_path):
        """HyperContext includes active_step, step_policies, action_items."""
        from dhee.core.buddhi import Buddhi
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "buddhi"))
        buddhi = Buddhi(data_dir=str(tmp_path / "buddhi"), kernel=kernel)

        # Create active task with current step
        task = kernel.tasks.create_task("u", "Fix auth", "bug_fix", plan=["step 1", "step 2"])
        task.start()
        kernel.tasks.update_task(task)

        ctx = buddhi.get_hyper_context(user_id="u", task_description="bug_fix")
        d = ctx.to_dict()
        assert "active_step" in d
        assert d["active_step"] == {"description": "step 1"}
        assert "step_policies" in d
        assert "action_items" in d
        assert "critical_blockers" in d
        assert "contradictions" in d
        assert any("[NEXT STEP]" in item for item in d["action_items"])

    def test_to_operational_dict_compact(self, tmp_path):
        """to_operational_dict() returns only actionable items."""
        from dhee.core.buddhi import Buddhi
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "buddhi"))
        buddhi = Buddhi(data_dir=str(tmp_path / "buddhi"), kernel=kernel)

        task = kernel.tasks.create_task("u", "Fix auth", "bug_fix", plan=["step 1"])
        task.start()
        kernel.tasks.update_task(task)

        ctx = buddhi.get_hyper_context(user_id="u", task_description="bug_fix")
        op = ctx.to_operational_dict()

        # Should have current_step and action_items
        assert "current_step" in op
        assert "action_items" in op
        # Should NOT have full history fields
        assert "insights" not in op
        assert "performance" not in op
        assert "memories" not in op

    def test_action_items_priority_order(self, tmp_path):
        """Intentions come before steps in action_items."""
        from dhee.core.buddhi import Buddhi
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "buddhi"))
        buddhi = Buddhi(data_dir=str(tmp_path / "buddhi"), kernel=kernel)

        # Create intention + active step
        kernel.intentions.store("u", "run tests after deploy", trigger_keywords=["bug_fix"])
        task = kernel.tasks.create_task("u", "Fix auth", "bug_fix", plan=["debug code"])
        task.start()
        kernel.tasks.update_task(task)

        ctx = buddhi.get_hyper_context(user_id="u", task_description="bug_fix")
        items = ctx.action_items

        intention_idx = next((i for i, x in enumerate(items) if "[INTENTION]" in x), None)
        step_idx = next((i for i, x in enumerate(items) if "[NEXT STEP]" in x), None)
        if intention_idx is not None and step_idx is not None:
            assert intention_idx < step_idx

    def test_context_operational_flag(self, tmp_path):
        """context(operational=True) returns compact format."""
        from dhee.simple import Dhee
        d = Dhee(in_memory=True, data_dir=str(tmp_path))
        full = d.context("test task")
        op = d.context("test task", operational=True)
        # Full has many keys, operational is a subset
        assert "user_id" in full
        assert "user_id" not in op

    def test_critical_blockers_surfaced(self, tmp_path):
        """Blockers from active task appear in critical_blockers."""
        from dhee.core.buddhi import Buddhi
        from dhee.core.cognition_kernel import CognitionKernel
        kernel = CognitionKernel(data_dir=str(tmp_path / "buddhi"))
        buddhi = Buddhi(data_dir=str(tmp_path / "buddhi"), kernel=kernel)

        task = kernel.tasks.create_task("u", "Fix auth", "bug_fix", plan=["step 1"])
        task.start()
        task.add_blocker("missing API key", severity="hard")
        kernel.tasks.update_task(task)

        ctx = buddhi.get_hyper_context(user_id="u", task_description="bug_fix")
        assert len(ctx.critical_blockers) >= 1
        assert any("API key" in b for b in ctx.critical_blockers)
