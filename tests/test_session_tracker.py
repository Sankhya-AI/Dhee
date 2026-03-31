"""Unit tests for SessionTracker — passive session observer."""

import time
import pytest

from dhee.core.session_tracker import (
    SessionTracker,
    classify_tier,
    infer_task_type,
    TIER_SHRUTI,
    TIER_SMRITI,
    TIER_VASANA,
)


# ── Tier classification ──────────────────────────────────────────────


class TestClassifyTier:
    def test_normal_content_is_smriti(self):
        assert classify_tier("The meeting is at 3pm tomorrow") == TIER_SMRITI

    def test_preference_is_shruti(self):
        assert classify_tier("I prefer Python over JavaScript") == TIER_SHRUTI

    def test_rule_is_shruti(self):
        assert classify_tier("Rule: always run tests before committing") == TIER_SHRUTI

    def test_never_is_shruti(self):
        assert classify_tier("Never deploy on Fridays") == TIER_SHRUTI

    def test_system_instruction_is_shruti(self):
        assert classify_tier("System: use dark mode for all UI") == TIER_SHRUTI

    def test_identity_is_shruti(self):
        assert classify_tier("I am a backend engineer at Acme Corp") == TIER_SHRUTI

    def test_short_content_is_smriti(self):
        assert classify_tier("hello") == TIER_SMRITI


# ── Task-type inference ──────────────────────────────────────────────


class TestInferTaskType:
    def test_bug_fix_keywords(self):
        assert infer_task_type(["fix the crash in login", "debug auth error"]) == "bug_fix"

    def test_feature_keywords(self):
        assert infer_task_type(["implement new endpoint", "add user profile"]) == "feature"

    def test_refactor_keywords(self):
        assert infer_task_type(["refactor the auth module", "extract helper"]) == "refactor"

    def test_testing_keywords(self):
        assert infer_task_type(["write pytest for auth", "add test coverage"]) == "testing"

    def test_general_fallback(self):
        assert infer_task_type(["hello world"]) == "general"

    def test_empty_is_general(self):
        assert infer_task_type([]) == "general"

    def test_needs_two_keywords_minimum(self):
        # Single keyword match isn't enough
        assert infer_task_type(["deploy"]) == "general"

    def test_deploy_with_context(self):
        assert infer_task_type(["deploy to production", "release pipeline"]) == "deploy"


# ── Session Tracker lifecycle ────────────────────────────────────────


class TestSessionTracker:
    def test_first_remember_triggers_auto_context(self):
        t = SessionTracker()
        signals = t.on_remember("user likes dark mode", "m1")
        assert signals.get("needs_auto_context") is True
        assert t.session_active is True

    def test_second_remember_no_auto_context(self):
        t = SessionTracker()
        t.on_remember("first", "m1")
        signals = t.on_remember("second", "m2")
        assert signals.get("needs_auto_context") is None

    def test_first_recall_triggers_auto_context(self):
        t = SessionTracker()
        signals = t.on_recall("user preferences", [{"id": "m1", "memory": "dark mode"}])
        assert signals.get("needs_auto_context") is True

    def test_explicit_context_suppresses_auto(self):
        t = SessionTracker()
        t.on_context("fixing auth bug")
        assert t.context_loaded is True
        signals = t.on_remember("some fact", "m1")
        assert signals.get("needs_auto_context") is None

    def test_auto_context_disabled(self):
        t = SessionTracker(auto_context=False)
        signals = t.on_remember("hello", "m1")
        assert signals.get("needs_auto_context") is None

    def test_checkpoint_marks_session(self):
        t = SessionTracker()
        t.on_remember("x", "m1")
        t.on_checkpoint()
        assert t._checkpoint_called is True

    def test_op_count_increments(self):
        t = SessionTracker()
        t.on_remember("a", "m1")
        t.on_recall("b", [])
        t.on_context("c")
        t.on_checkpoint()
        assert t.op_count == 4


# ── Timeout detection ────────────────────────────────────────────────


class TestSessionTimeout:
    def test_timeout_triggers_auto_checkpoint(self):
        t = SessionTracker(session_timeout=1.0)  # 1 second timeout
        t.on_remember("hello", "m1")
        t.on_remember("world", "m2")
        t.on_recall("test", [{"id": "m1", "memory": "hello"}])

        # Simulate timeout
        t._last_activity_time = time.time() - 2.0

        signals = t.on_remember("new session", "m3")
        assert signals.get("needs_auto_checkpoint") is True
        args = signals["auto_checkpoint_args"]
        assert "summary" in args
        assert args["status"] == "completed"

    def test_no_timeout_within_window(self):
        t = SessionTracker(session_timeout=3600.0)
        t.on_remember("hello", "m1")
        signals = t.on_remember("world", "m2")
        assert signals.get("needs_auto_checkpoint") is None

    def test_timeout_resets_session(self):
        t = SessionTracker(session_timeout=1.0)
        t.on_remember("first session", "m1")
        t._last_activity_time = time.time() - 2.0
        t.on_remember("second session", "m2")
        # After timeout, a new session starts
        assert t.op_count == 1  # reset + 1 new op

    def test_no_auto_checkpoint_after_explicit(self):
        t = SessionTracker(session_timeout=1.0)
        t.on_remember("hello", "m1")
        t.on_checkpoint()
        t._last_activity_time = time.time() - 2.0
        signals = t.on_remember("new", "m2")
        # Should not trigger auto-checkpoint since explicit was called
        assert signals.get("needs_auto_checkpoint") is None


# ── Outcome inference ────────────────────────────────────────────────


class TestOutcomeInference:
    def test_outcome_with_recalls(self):
        t = SessionTracker()
        t.on_remember("setup", "m1")
        t.on_recall("test", [{"id": "m1", "memory": "setup"}])
        t.on_remember("result", "m2")
        t.on_remember("more", "m3")

        outcome = t.get_outcome_signals()
        assert 0.0 < outcome["outcome_score"] <= 1.0
        assert "signals" in outcome

    def test_empty_session_neutral(self):
        t = SessionTracker()
        outcome = t.get_outcome_signals()
        assert outcome["outcome_score"] >= 0.1

    def test_what_worked_from_top_recall(self):
        t = SessionTracker()
        t.on_recall("help", [{"id": "m1", "memory": "git blame first"}])
        t.on_recall("more", [{"id": "m1", "memory": "git blame first"}])

        outcome = t.get_outcome_signals()
        assert outcome["what_worked"] == "git blame first"


# ── Finalize (atexit) ────────────────────────────────────────────────


class TestFinalize:
    def test_finalize_returns_args_for_active_session(self):
        t = SessionTracker()
        t.on_remember("some work", "m1")
        t.on_recall("query", [{"id": "m1", "memory": "some work"}])

        args = t.finalize()
        assert args is not None
        assert "summary" in args
        assert args["status"] == "completed"

    def test_finalize_returns_none_after_checkpoint(self):
        t = SessionTracker()
        t.on_remember("done", "m1")
        t.on_checkpoint()
        assert t.finalize() is None

    def test_finalize_returns_none_for_empty_session(self):
        t = SessionTracker()
        assert t.finalize() is None

    def test_inferred_task_type_in_auto_checkpoint(self):
        t = SessionTracker()
        t.on_remember("fix the crash in auth module", "m1")
        t.on_recall("debug error in login", [])
        args = t.finalize()
        assert args is not None
        assert args.get("task_type") == "bug_fix"
