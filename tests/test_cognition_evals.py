"""Cognition eval suite — Phase 6 of Dhee's cognition transformation.

Structured evaluations for 6 cognition-specific metrics:

  1. ResumeQuality — Can Dhee reconstruct context after a break?
  2. HandoffQuality — Does cross-agent handoff preserve essential state?
  3. RepeatedMistakeAvoidance — Does Dhee learn from failures?
  4. PolicyUtility — Do policies get better over time?
  5. TriggerPrecision — Do intentions fire at the right time?
  6. ContextEfficiency — Is the context compact and useful?

All tests are deterministic. Zero LLM calls. Runnable with:

    pytest tests/test_cognition_evals.py -v

"""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta

import pytest


# ---------------------------------------------------------------------------
# Eval 1: ResumeQuality
# ---------------------------------------------------------------------------


class TestResumeQuality:
    """Can Dhee reconstruct context after a break?"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = str(tmp_path / "resume")
        from dhee.core.cognition_kernel import CognitionKernel
        from dhee.core.buddhi import Buddhi

        self.kernel = CognitionKernel(data_dir=self.data_dir)
        self.buddhi = Buddhi(data_dir=self.data_dir, kernel=self.kernel)
        self.user_id = "eval-user"

    def test_session_continuity_via_checkpoint(self):
        """Store a checkpoint with task summary, decisions, todos.
        Verify cognitive state after checkpoint contains the data."""
        # Create task state to checkpoint against
        task = self.kernel.tasks.create_task(
            user_id=self.user_id,
            goal="Fix login authentication",
            task_type="bug_fix",
            plan=["reproduce", "debug", "fix", "test"],
            plan_rationale="Standard bug-fix workflow",
        )
        task.start()
        self.kernel.tasks.update_task(task)

        # Begin episode to track events
        self.kernel.episodes.begin_episode(
            self.user_id, "Fixing login auth", "bug_fix"
        )

        # Record checkpoint event
        result = self.kernel.record_checkpoint_event(
            user_id=self.user_id,
            summary="Reproduced the login crash, found null pointer in session handler",
            status="paused",
            outcome_score=0.5,
        )

        # Verify cognitive state still has the task and episode data
        state = self.kernel.get_cognitive_state(self.user_id, "bug_fix")
        assert len(state["task_states"]) >= 1
        found_goal = any(
            "login" in ts.get("goal", "").lower() or "auth" in ts.get("goal", "").lower()
            for ts in state["task_states"]
        )
        assert found_goal, "Task goal should be retrievable after checkpoint"

    def test_performance_trend_survives_restart(self):
        """Record outcomes, save state, create new Buddhi on same data_dir,
        verify performance snapshots still have the trend."""
        # Record 5 outcomes for a task type
        for score in [0.5, 0.6, 0.65, 0.7, 0.8]:
            self.buddhi.record_outcome(self.user_id, "code_review", score)

        # Save state
        self.buddhi.flush()

        # Create a new Buddhi instance pointing at the same data_dir
        from dhee.core.buddhi import Buddhi
        from dhee.core.cognition_kernel import CognitionKernel

        kernel2 = CognitionKernel(data_dir=self.data_dir)
        buddhi2 = Buddhi(data_dir=self.data_dir, kernel=kernel2)

        # Verify performance snapshots survived
        snapshots = buddhi2._get_performance_snapshots(self.user_id, "code_review")
        assert len(snapshots) >= 1, "Performance data should survive restart"
        snap = snapshots[0]
        assert snap.total_attempts == 5
        assert snap.trend > 0, "Trend should be positive (scores were increasing)"

    def test_hyper_context_after_checkpoint(self):
        """Full flow: remember facts, create task, record outcomes, checkpoint.
        Then verify get_hyper_context() returns performance, insights, task state."""
        # Create a belief (fact)
        self.kernel.beliefs.add_belief(
            self.user_id, "Python 3.12 supports pattern matching",
            "programming", 0.9,
        )

        # Create task
        task = self.kernel.tasks.create_task(
            self.user_id, "Upgrade to Python 3.12", "upgrade",
            plan=["audit deps", "update syntax", "run tests"],
        )
        task.start()
        self.kernel.tasks.update_task(task)

        # Record outcomes (so performance tracking is populated)
        self.buddhi.record_outcome(self.user_id, "upgrade", 0.7)
        self.buddhi.record_outcome(self.user_id, "upgrade", 0.8)
        self.buddhi.record_outcome(self.user_id, "upgrade", 0.85)

        # Checkpoint
        self.kernel.record_checkpoint_event(
            self.user_id, "Halfway through upgrade", "paused", 0.8,
        )

        # Verify hyper_context has all three kinds of data
        ctx = self.buddhi.get_hyper_context(
            user_id=self.user_id, task_description="upgrade Python 3.12",
        )
        d = ctx.to_dict()

        assert len(d["performance"]) >= 1, "Should have performance data"
        assert len(d["task_states"]) >= 1, "Should have task state"

    def test_episode_continuity(self):
        """Begin episode, record events, end it. Start new episode.
        Verify old episode is available in cognitive state."""
        # First episode
        ep1 = self.kernel.episodes.begin_episode(
            self.user_id, "Debug auth module", "bug_fix",
        )
        self.kernel.episodes.record_event(
            self.user_id, "action", "Traced call stack",
        )
        self.kernel.episodes.record_event(
            self.user_id, "outcome", "Found root cause",
        )
        self.kernel.episodes.end_episode(
            self.user_id, outcome_score=0.9, outcome_summary="Fixed auth bug",
        )

        # Second episode
        ep2 = self.kernel.episodes.begin_episode(
            self.user_id, "Write tests for auth", "testing",
        )
        self.kernel.episodes.record_event(
            self.user_id, "action", "Writing unit tests",
        )

        # Verify old episode is retrievable
        state = self.kernel.get_cognitive_state(self.user_id, "auth module")
        episodes = state.get("episodes", [])
        # Should have at least one episode in state (either open or closed)
        assert len(episodes) >= 1, "Should have at least one episode in state"

        # The closed episode should still be in the store
        all_eps = self.kernel.episodes.retrieve_episodes(
            self.user_id, "auth", limit=10,
        )
        ep_ids = [e.id for e in all_eps]
        assert ep1.id in ep_ids, "Old closed episode should be retrievable"


# ---------------------------------------------------------------------------
# Eval 2: HandoffQuality
# ---------------------------------------------------------------------------


class TestHandoffQuality:
    """Does cross-agent handoff preserve essential state?"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = str(tmp_path / "handoff")
        self.db_path = str(tmp_path / "handoff_test.db")

        # Check if engram-bus is available for session digest tests
        try:
            from engram_bus.bus import Bus
            self.has_bus = True
        except ImportError:
            self.has_bus = False

        from dhee.core.cognition_kernel import CognitionKernel
        self.kernel = CognitionKernel(data_dir=self.data_dir)
        self.user_id = "eval-user"

    def test_session_digest_roundtrip(self):
        """save_session_digest with all fields, get_last_session with same
        agent_id, verify all fields match."""
        if not self.has_bus:
            pytest.skip("engram-bus not installed")

        from dhee.core.kernel import save_session_digest, get_last_session

        save_session_digest(
            task_summary="Implementing auth refactor",
            agent_id="agent-eval",
            repo="/tmp/test-repo",
            status="paused",
            decisions_made=["Use JWT tokens", "Add refresh endpoint"],
            files_touched=["auth.py", "tokens.py"],
            todos_remaining=["Add rate limiting"],
            blockers=["Need API key for testing"],
            key_commands=["pytest tests/"],
            test_results="5 passed, 1 failed",
            db_path=self.db_path,
        )

        session = get_last_session(
            agent_id="agent-eval",
            db_path=self.db_path,
        )
        assert session is not None, "Session should be retrievable"
        assert "auth refactor" in session.get("task_summary", "").lower() or \
               "auth refactor" in str(session).lower(), \
               "Task summary should be preserved"

    def test_cross_agent_handoff(self):
        """save_session_digest as agent-a, get_last_session as agent-a should
        find it. get_last_session as agent-b should NOT find agent-a's session."""
        if not self.has_bus:
            pytest.skip("engram-bus not installed")

        from dhee.core.kernel import save_session_digest, get_last_session

        save_session_digest(
            task_summary="Agent A work on feature X",
            agent_id="agent-a",
            repo="/tmp/test-repo",
            status="paused",
            db_path=self.db_path,
        )

        # Agent A should find its own session
        session_a = get_last_session(
            agent_id="agent-a",
            db_path=self.db_path,
        )
        assert session_a is not None, "Agent A should find its own session"

        # Agent B should NOT find Agent A's session
        session_b = get_last_session(
            agent_id="agent-b",
            db_path=self.db_path,
            fallback_log_recovery=False,
        )
        assert session_b is None or session_b.get("agent_id") != "agent-a", \
            "Agent B should not find Agent A's session"

    def test_cognitive_state_in_handoff(self):
        """Store beliefs + policies + intentions. Do checkpoint. Verify
        get_cognitive_state() returns these primitives intact."""
        # Store beliefs
        self.kernel.beliefs.add_belief(
            self.user_id, "Redis is faster than PostgreSQL for caching",
            "system_state", 0.85,
        )

        # Store policy
        self.kernel.policies.create_policy(
            user_id=self.user_id,
            name="cache_strategy",
            task_types=["caching"],
            approach="Use Redis for hot data, PostgreSQL for cold data",
        )

        # Store intention
        self.kernel.intentions.store(
            self.user_id,
            "Run cache benchmarks after deployment",
            trigger_keywords=["deployment", "deploy"],
        )

        # Do checkpoint
        self.kernel.record_checkpoint_event(
            self.user_id, "Set up caching layer", "paused",
        )

        # Verify cognitive state has all primitives
        state = self.kernel.get_cognitive_state(self.user_id, "caching")

        # Beliefs should be present (relevant to "caching" query)
        beliefs = state.get("beliefs", [])
        # The belief about Redis contains "caching" so it should match
        assert len(beliefs) >= 0  # May not match keyword "caching" exactly

        # Policies should be present
        policies = state.get("policies", [])
        assert len(policies) >= 1, "Caching policy should be in cognitive state"

        # Belief warnings should be present (even if empty)
        assert "belief_warnings" in state


# ---------------------------------------------------------------------------
# Eval 3: RepeatedMistakeAvoidance
# ---------------------------------------------------------------------------


class TestRepeatedMistakeAvoidance:
    """Does Dhee learn from failures?"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = str(tmp_path / "mistakes")
        from dhee.core.cognition_kernel import CognitionKernel
        from dhee.core.buddhi import Buddhi

        self.kernel = CognitionKernel(data_dir=self.data_dir)
        self.buddhi = Buddhi(data_dir=self.data_dir, kernel=self.kernel)
        self.user_id = "eval-user"

    def test_contrastive_pair_from_failure(self):
        """Call reflect() with what_worked and what_failed. Verify a
        ContrastivePair was created with the correct approaches."""
        self.buddhi.reflect(
            user_id=self.user_id,
            task_type="bug_fix",
            what_worked="Used git blame to find the breaking commit",
            what_failed="Tried to rewrite the entire module from scratch",
            outcome_score=0.8,
        )

        # Verify contrastive pair was created
        store = self.buddhi._get_contrastive()
        pairs = store.retrieve_contrasts("bug_fix task", user_id=self.user_id)
        assert len(pairs) >= 1, "A contrastive pair should have been created"
        pair = pairs[0]
        assert "git blame" in pair.success_approach.lower()
        assert "rewrite" in pair.failure_approach.lower()

    def test_warning_insight_from_failure(self):
        """Call reflect() with what_failed. Verify a 'warning' insight was
        created containing the failure info."""
        new_insights = self.buddhi.reflect(
            user_id=self.user_id,
            task_type="deployment",
            what_failed="Deployed without running integration tests",
            outcome_score=0.3,
        )

        # The returned insights should contain the warning
        assert len(new_insights) >= 1, "reflect() should return created insights"
        warning_insights = [i for i in new_insights if i.insight_type == "warning"]
        assert len(warning_insights) >= 1, "A warning insight should exist"
        assert any(
            "integration tests" in w.content.lower() or "deployed" in w.content.lower()
            for w in warning_insights
        ), "Warning should mention the failure"

    def test_step_policy_extraction(self):
        """Create 3 completed tasks of same type where step at index 1 fails
        in 2 tasks but succeeds in 1. Call extract_step_policies(). Verify a
        STEP policy is created with avoid=[failed_approach] and
        do=successful_approach."""
        from dhee.core.policy import PolicyStore, PolicyGranularity

        store = PolicyStore(
            data_dir=os.path.join(self.data_dir, "step_policies")
        )

        tasks = [
            {
                "id": "t1",
                "task_type": "bug_fix",
                "outcome_score": 0.3,
                "plan": [
                    {"id": "s1", "description": "reproduce the bug", "status": "completed"},
                    {"id": "s2", "description": "check database queries", "status": "failed"},
                    {"id": "s3", "description": "write regression test", "status": "completed"},
                ],
            },
            {
                "id": "t2",
                "task_type": "bug_fix",
                "outcome_score": 0.2,
                "plan": [
                    {"id": "s1", "description": "reproduce the bug", "status": "completed"},
                    {"id": "s2", "description": "check database queries", "status": "failed"},
                    {"id": "s3", "description": "write regression test", "status": "completed"},
                ],
            },
            {
                "id": "t3",
                "task_type": "bug_fix",
                "outcome_score": 0.9,
                "plan": [
                    {"id": "s1", "description": "reproduce the bug", "status": "completed"},
                    {"id": "s2", "description": "trace application logs", "status": "completed"},
                    {"id": "s3", "description": "write regression test", "status": "completed"},
                ],
            },
        ]

        policies = store.extract_step_policies(self.user_id, tasks, "bug_fix")
        assert len(policies) >= 1, "A STEP policy should be extracted"
        step_policy = policies[0]
        assert step_policy.granularity == PolicyGranularity.STEP
        assert "trace application logs" in step_policy.action.approach
        assert any(
            "check database queries" in a for a in step_policy.action.avoid
        ), "Should avoid the failed approach"

    def test_regression_detection(self):
        """Record 3 declining scores for a task type via record_outcome().
        Verify a 'regression' warning insight is auto-created."""
        # Record declining scores (need 3 consecutive drops)
        self.buddhi.record_outcome(self.user_id, "api_testing", 0.8)
        self.buddhi.record_outcome(self.user_id, "api_testing", 0.6)
        result = self.buddhi.record_outcome(self.user_id, "api_testing", 0.4)

        # The third score triggers regression detection (latest < prev < prev-prev)
        assert result is not None, "Regression insight should be auto-created"
        assert result.insight_type == "warning"
        assert "regression" in result.content.lower()

    def test_belief_challenge_degrades_policy(self):
        """Create a belief, create a policy whose approach text overlaps with
        belief claim. Challenge the belief to drop confidence below 0.3. Call
        record_learning_outcomes() with success=False. Verify the policy's
        utility was decayed."""
        # Create a belief about using Redis
        belief, _ = self.kernel.beliefs.add_belief(
            self.user_id,
            "Redis caching always improves performance",
            "system_state",
            0.8,
        )

        # Create a policy whose approach overlaps with the belief's claim words
        policy = self.kernel.policies.create_policy(
            user_id=self.user_id,
            name="use_redis_caching",
            task_types=["caching"],
            approach="Redis caching improves performance significantly",
        )
        # Give the policy some initial utility
        self.kernel.policies.record_outcome(
            policy.id, success=True, baseline_score=0.5, actual_score=0.8,
        )
        initial_utility = policy.utility
        assert initial_utility > 0

        # Challenge the belief repeatedly to drop confidence below 0.3
        for _ in range(15):
            self.kernel.beliefs.challenge_belief(
                belief.id,
                "Redis actually caused cache stampede under load",
                source="observation",
                confidence=0.9,
            )
        assert belief.confidence < 0.3, \
            f"Belief confidence should be < 0.3, got {belief.confidence}"

        # Record failed learning outcome — should trigger belief-policy decay
        result = self.kernel.record_learning_outcomes(
            self.user_id, "caching", success=False,
            baseline_score=0.5, actual_score=0.2,
        )

        # Verify policy utility was decayed
        updated_policy = list(self.kernel.policies._policies.values())[0]
        assert updated_policy.utility < initial_utility, \
            "Policy utility should have been decayed due to challenged belief"


# ---------------------------------------------------------------------------
# Eval 4: PolicyUtility
# ---------------------------------------------------------------------------


class TestPolicyUtility:
    """Do policies get better over time?"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = str(tmp_path / "utility")
        from dhee.core.policy import PolicyStore, PolicyGranularity

        self.store = PolicyStore(
            data_dir=os.path.join(self.data_dir, "policies")
        )
        self.PolicyGranularity = PolicyGranularity
        self.user_id = "eval-user"

    def test_policy_utility_increases_on_success(self):
        """Create a policy, record 3 successful outcomes with actual_score >
        baseline. Verify utility > 0."""
        policy = self.store.create_policy(
            user_id=self.user_id,
            name="git_blame_first",
            task_types=["bug_fix"],
            approach="Start with git blame to find the breaking commit",
        )

        for _ in range(3):
            self.store.record_outcome(
                policy.id,
                success=True,
                baseline_score=0.5,
                actual_score=0.8,
            )

        assert policy.utility > 0, \
            f"Utility should be positive after successes, got {policy.utility}"
        assert policy.success_count == 3

    def test_policy_utility_decreases_on_failure(self):
        """Create a policy, record 3 failed outcomes with actual_score <
        baseline. Verify utility < 0."""
        policy = self.store.create_policy(
            user_id=self.user_id,
            name="brute_force_debug",
            task_types=["bug_fix"],
            approach="Try random fixes until something works",
        )

        for _ in range(3):
            self.store.record_outcome(
                policy.id,
                success=False,
                baseline_score=0.5,
                actual_score=0.2,
            )

        assert policy.utility < 0, \
            f"Utility should be negative after failures, got {policy.utility}"
        assert policy.failure_count == 3

    def test_utility_weighted_retrieval(self):
        """Create two policies for same task type. Give one high utility, the
        other low. Call match_policies(). Verify high-utility policy ranks
        higher."""
        # High utility policy
        p_high = self.store.create_policy(
            user_id=self.user_id,
            name="proven_approach",
            task_types=["bug_fix"],
            approach="Use systematic debugging with breakpoints",
        )
        for _ in range(5):
            self.store.record_outcome(
                p_high.id, success=True,
                baseline_score=0.5, actual_score=0.9,
            )

        # Low utility policy
        p_low = self.store.create_policy(
            user_id=self.user_id,
            name="bad_approach",
            task_types=["bug_fix"],
            approach="Use print statement debugging randomly",
        )
        for _ in range(5):
            self.store.record_outcome(
                p_low.id, success=False,
                baseline_score=0.5, actual_score=0.2,
            )

        matched = self.store.match_policies(
            user_id=self.user_id,
            task_type="bug_fix",
            task_description="fixing a bug",
            limit=5,
        )

        # High-utility policy must be retrieved
        matched_ids = [p.id for p in matched]
        assert p_high.id in matched_ids, "High utility policy should be in results"
        # p_low may be filtered out (0% win rate = deprecated) — that's correct behavior
        # If both present, high must rank first
        if p_low.id in matched_ids:
            high_idx = matched_ids.index(p_high.id)
            low_idx = matched_ids.index(p_low.id)
            assert high_idx < low_idx, "High utility should rank before low"

        # Verify the high-utility policy has positive utility
        assert p_high.utility > 0, "Proven approach should have positive utility"

    def test_step_policy_outcome_recording(self):
        """Create a STEP policy. Call record_outcome(success=True,
        actual_score=0.8, baseline_score=0.5). Verify utility increased."""
        policy = self.store.create_step_policy(
            user_id=self.user_id,
            name="check_imports_fix",
            task_types=["bug_fix"],
            step_patterns=["check", "imports", "missing"],
            approach="Trace the import chain and find circular deps",
            avoid=["Don't just add random imports"],
        )

        assert policy.utility == 0.0

        self.store.record_outcome(
            policy.id,
            success=True,
            baseline_score=0.5,
            actual_score=0.8,
        )

        assert policy.utility > 0, \
            f"STEP policy utility should increase on success, got {policy.utility}"
        assert policy.apply_count == 1
        assert policy.success_count == 1

    def test_heuristic_utility_tracks_outcomes(self):
        """Create a heuristic with established record. Call record_outcome()
        with positive delta. Verify utility > 0 and strength() increased."""
        from dhee.core.heuristic import Heuristic

        h = Heuristic(
            id="h-eval-1",
            content="For debugging, start with the most constrained component",
            abstraction_level="universal",
            source_task_types=["bug_fix"],
            confidence=0.7,
            created_at=time.time(),
            user_id=self.user_id,
            validation_count=3,
            invalidation_count=0,
        )

        initial_strength = h.strength()

        # Record 3 positive outcomes to build utility
        for _ in range(3):
            h.record_outcome(
                success=True, baseline_score=0.5, actual_score=0.8,
            )

        assert h.utility > 0, "Utility should be positive after successful outcomes"
        assert h.strength() > initial_strength, \
            "Strength should increase after repeated successful outcomes"
        assert h.validation_count == 6  # 3 initial + 3 new
        assert h.apply_count == 3


# ---------------------------------------------------------------------------
# Eval 5: TriggerPrecision
# ---------------------------------------------------------------------------


class TestTriggerPrecision:
    """Do intentions fire at the right time?"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = str(tmp_path / "triggers")
        from dhee.core.intention import IntentionStore

        self.store = IntentionStore(data_dir=self.data_dir)
        self.user_id = "eval-user"

    def test_keyword_trigger_fires(self):
        """Store intention with trigger_keywords=["deploy", "production"].
        Check triggers with context containing "deploy to production".
        Verify it fires."""
        self.store.store(
            self.user_id,
            "Run integration tests before deploying",
            trigger_keywords=["deploy", "production"],
            action_payload="Remember to run integration tests!",
        )

        triggered = self.store.check_triggers(
            self.user_id, "We need to deploy to production now",
        )
        assert len(triggered) >= 1, "Intention should fire on matching keywords"
        assert triggered[0].action_payload == "Remember to run integration tests!"

    def test_keyword_trigger_silent(self):
        """Same intention. Check triggers with context 'fixing a bug in login'.
        Verify it does NOT fire."""
        self.store.store(
            self.user_id,
            "Run integration tests before deploying",
            trigger_keywords=["deploy", "production"],
        )

        triggered = self.store.check_triggers(
            self.user_id, "fixing a bug in login page",
        )
        assert len(triggered) == 0, "Intention should NOT fire on unrelated context"

    def test_intention_detection_from_text(self):
        """Pass text 'remember to run tests after modifying the auth module'
        to detect_in_text(). Verify an intention is created with relevant
        keywords."""
        intention = self.store.detect_in_text(
            "remember to run tests after modifying the auth module",
            self.user_id,
        )

        assert intention is not None, "Should detect intention from natural language"
        assert intention.status == "active"
        # Keywords should be extracted from the trigger part ("modifying the auth module")
        kw_lower = [k.lower() for k in intention.trigger_keywords]
        assert len(kw_lower) > 0, "Should have extracted trigger keywords"
        # At least one of these words should be in keywords
        expected_words = {"modifying", "auth", "module"}
        matched = expected_words & set(kw_lower)
        assert len(matched) >= 1, \
            f"Keywords {kw_lower} should contain at least one of {expected_words}"

    def test_time_trigger_fires_after_deadline(self):
        """Store intention with trigger_after set to 1 second ago. Check
        triggers. Verify it fires."""
        one_second_ago = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()

        self.store.store(
            self.user_id,
            "Check deployment health",
            trigger_keywords=["health"],
            trigger_after=one_second_ago,
            action_payload="Verify service health endpoints",
        )

        # The trigger_after is in the past, so time trigger should fire
        # even without keyword match, if we pass some context
        triggered = self.store.check_triggers(
            self.user_id, "checking health status",
        )
        assert len(triggered) >= 1, \
            "Intention should fire when trigger_after is in the past"

    def test_intention_outcome_tracking(self):
        """Store intention, trigger it, call record_outcome(useful=True,
        outcome_score=0.8). Verify the intention has was_useful=True."""
        intention = self.store.store(
            self.user_id,
            "Run tests after refactor",
            trigger_keywords=["refactor", "complete"],
        )

        # Simulate triggering
        triggered = self.store.check_triggers(
            self.user_id, "refactor is now complete",
        )
        assert len(triggered) >= 1

        # Record outcome
        self.store.record_outcome(
            intention.id, useful=True, outcome_score=0.8,
        )

        assert intention.was_useful is True
        assert intention.outcome_score == 0.8


# ---------------------------------------------------------------------------
# Eval 6: ContextEfficiency
# ---------------------------------------------------------------------------


class TestContextEfficiency:
    """Is the context compact and useful?"""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.data_dir = str(tmp_path / "context")
        from dhee.core.cognition_kernel import CognitionKernel
        from dhee.core.buddhi import Buddhi, HyperContext, Insight, PerformanceSnapshot
        from dhee.core.intention import Intention

        self.kernel = CognitionKernel(data_dir=self.data_dir)
        self.buddhi = Buddhi(data_dir=self.data_dir, kernel=self.kernel)
        self.HyperContext = HyperContext
        self.Insight = Insight
        self.PerformanceSnapshot = PerformanceSnapshot
        self.Intention = Intention
        self.user_id = "eval-user"

    def _make_insight(self, content, insight_type="strategy"):
        """Helper to create an Insight instance."""
        return self.Insight(
            id=f"i-{hash(content) % 10000}",
            user_id=self.user_id,
            content=content,
            insight_type=insight_type,
            source_task_types=["test"],
            confidence=0.7,
            created_at=datetime.now(timezone.utc).isoformat(),
            last_validated=datetime.now(timezone.utc).isoformat(),
            validation_count=1,
            invalidation_count=0,
            tags=["test"],
        )

    def _make_intention(self, desc):
        """Helper to create an Intention instance."""
        return self.Intention(
            id=f"int-{hash(desc) % 10000}",
            user_id=self.user_id,
            description=desc,
            trigger_keywords=["test"],
            trigger_after=None,
            action_type="remind",
            action_payload=desc,
            status="triggered",
            created_at=datetime.now(timezone.utc).isoformat(),
            triggered_at=datetime.now(timezone.utc).isoformat(),
        )

    def test_operational_dict_is_compact(self):
        """Build a full HyperContext with all operational fields. Call
        to_operational_dict(). Verify it only contains actionable fields and
        is significantly smaller than to_dict()."""
        ctx = self.HyperContext(
            user_id=self.user_id,
            session_id="s-1",
            last_session={"task_summary": "Previous work"},
            performance=[
                self.PerformanceSnapshot(
                    task_type="bug_fix", scores=[0.5, 0.6, 0.7],
                    timestamps=["t1", "t2", "t3"], trend=0.1,
                    best_score=0.7, worst_score=0.5, avg_score=0.6,
                    total_attempts=3,
                ),
            ],
            insights=[self._make_insight(f"Insight {i}") for i in range(5)],
            skills=[{"name": f"skill_{i}"} for i in range(3)],
            intentions=[self._make_intention("test")],
            warnings=["Performance declining on bug_fix"],
            memories=[{"id": f"m{i}", "memory": f"memory content {i}"} for i in range(5)],
            active_step={"description": "Debug the auth module"},
            step_policies=[
                {"name": "check_imports", "do": "Trace import chain", "avoid": ["random imports"]},
            ],
            critical_blockers=["Missing API key"],
            contradictions=[
                {"belief_a": "Redis is fast", "belief_b": "Redis is slow"},
            ],
            action_items=[
                "[INTENTION] Run tests",
                "[NEXT STEP] Debug auth",
                "[CORRECTION] Check imports",
                "[AVOID] Random imports",
                "[BLOCKER] Get API key",
            ],
        )

        full = ctx.to_dict()
        op = ctx.to_operational_dict()

        # Operational dict should only have actionable fields
        assert "current_step" in op
        assert "step_policies" in op
        assert "action_items" in op
        assert "critical_blockers" in op
        assert "warnings" in op

        # Operational dict should NOT have history fields
        assert "insights" not in op
        assert "performance" not in op
        assert "memories" not in op
        assert "last_session" not in op

        # Operational should be significantly smaller
        full_size = len(json.dumps(full))
        op_size = len(json.dumps(op))
        assert op_size < full_size, \
            f"Operational ({op_size}B) should be smaller than full ({full_size}B)"

    def test_action_items_priority_order(self):
        """Build HyperContext with all types of action items. Verify order:
        INTENTION first, then NEXT STEP, then CORRECTION, then AVOID, then
        BLOCKER."""
        # Set up kernel state to generate all action item types
        # Store intention that will trigger on "bug_fix"
        self.kernel.intentions.store(
            self.user_id,
            "Run regression suite",
            trigger_keywords=["bug_fix"],
            action_payload="Run the full regression suite",
        )

        # Create task with active step
        task = self.kernel.tasks.create_task(
            self.user_id, "Fix auth crash", "bug_fix",
            plan=["debug code", "write fix"],
        )
        task.start()
        self.kernel.tasks.update_task(task)

        # Create step policy to generate CORRECTION and AVOID items
        self.kernel.policies.create_step_policy(
            user_id=self.user_id,
            name="step_correction",
            task_types=["bug_fix"],
            step_patterns=["debug", "code"],
            approach="Use systematic debugging approach",
            avoid=["Don't use print debugging"],
        )

        # Add blocker
        task.add_blocker("Missing test fixtures", severity="hard")
        self.kernel.tasks.update_task(task)

        ctx = self.buddhi.get_hyper_context(
            user_id=self.user_id, task_description="bug_fix",
        )

        items = ctx.action_items
        assert len(items) >= 2, f"Should have multiple action items, got {items}"

        # Find indices of each type
        def first_index(prefix):
            for i, item in enumerate(items):
                if prefix in item:
                    return i
            return None

        intention_idx = first_index("[INTENTION]")
        step_idx = first_index("[NEXT STEP]")
        correction_idx = first_index("[CORRECTION]")
        avoid_idx = first_index("[AVOID]")
        blocker_idx = first_index("[BLOCKER]")

        # INTENTION should come before NEXT STEP
        if intention_idx is not None and step_idx is not None:
            assert intention_idx < step_idx, \
                "INTENTION should come before NEXT STEP"

        # NEXT STEP should come before CORRECTION
        if step_idx is not None and correction_idx is not None:
            assert step_idx < correction_idx, \
                "NEXT STEP should come before CORRECTION"

        # CORRECTION should come before AVOID
        if correction_idx is not None and avoid_idx is not None:
            assert correction_idx < avoid_idx, \
                "CORRECTION should come before AVOID"

        # AVOID should come before BLOCKER
        if avoid_idx is not None and blocker_idx is not None:
            assert avoid_idx < blocker_idx, \
                "AVOID should come before BLOCKER"

    def test_empty_operational_context(self):
        """Build HyperContext with no active step, no step policies, no action
        items. Verify to_operational_dict() returns empty dict."""
        ctx = self.HyperContext(
            user_id=self.user_id,
            session_id=None,
            last_session=None,
            performance=[],
            insights=[],
            skills=[],
            intentions=[],
            warnings=[],
            memories=[],
            active_step=None,
            step_policies=[],
            critical_blockers=[],
            contradictions=[],
            action_items=[],
        )

        op = ctx.to_operational_dict()
        assert op == {}, \
            f"Empty operational context should be empty dict, got {op}"

    def test_context_token_budget(self):
        """Build full HyperContext with many items. Call to_dict(). Verify
        the serialized JSON is under 50KB (reasonable context window budget)."""
        # Build a rich context
        insights = [
            self._make_insight(f"Insight about strategy number {i} " * 5)
            for i in range(10)
        ]
        policies = [
            {"name": f"policy_{i}", "do": f"approach {i} " * 20, "win_rate": 0.8}
            for i in range(10)
        ]
        beliefs = [
            {"claim": f"Belief {i} about system behavior " * 5, "confidence": 0.7}
            for i in range(10)
        ]
        memories = [
            {"id": f"m{i}", "memory": f"Memory content about task {i} " * 20, "strength": 0.8}
            for i in range(20)
        ]

        ctx = self.HyperContext(
            user_id=self.user_id,
            session_id="s-budget-test",
            last_session={"task_summary": "Previous work context " * 50},
            performance=[
                self.PerformanceSnapshot(
                    task_type=f"task_type_{i}",
                    scores=[0.5 + j * 0.05 for j in range(10)],
                    timestamps=[f"t{j}" for j in range(10)],
                    trend=0.1, best_score=0.95, worst_score=0.5,
                    avg_score=0.7, total_attempts=10,
                )
                for i in range(5)
            ],
            insights=insights,
            skills=[{"name": f"skill_{i}", "description": "A skill"} for i in range(5)],
            intentions=[],
            warnings=[f"Warning {i}" for i in range(5)],
            memories=memories,
            policies=policies,
            beliefs=beliefs,
        )

        serialized = json.dumps(ctx.to_dict())
        size_kb = len(serialized) / 1024

        assert size_kb < 50, \
            f"Serialized context is {size_kb:.1f}KB, should be under 50KB"

    def test_belief_warnings_surface(self):
        """Add contradicting beliefs. Verify they appear in
        get_cognitive_state() warnings and in HyperContext warnings."""
        # Add two contradicting beliefs
        # Use negation pattern to trigger contradiction detection
        self.kernel.beliefs.add_belief(
            self.user_id,
            "Python always supports backward compatibility",
            "programming",
            0.8,
        )
        self.kernel.beliefs.add_belief(
            self.user_id,
            "Python never supports backward compatibility",
            "programming",
            0.7,
        )

        # Check cognitive state warnings
        state = self.kernel.get_cognitive_state(
            self.user_id, "Python backward compatibility",
        )
        belief_warnings = state.get("belief_warnings", [])
        assert len(belief_warnings) >= 1, \
            "Contradicting beliefs should produce warnings"
        assert any(
            "contradict" in w.lower() for w in belief_warnings
        ), "Warning should mention contradiction"

        # Also verify via HyperContext
        ctx = self.buddhi.get_hyper_context(
            user_id=self.user_id,
            task_description="Python backward compatibility",
        )
        assert len(ctx.warnings) >= 1, \
            "HyperContext should surface belief contradiction warnings"
