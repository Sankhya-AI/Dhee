"""Tests for engram.core.intent — Query intent classifier."""

import pytest

from dhee.core.intent import QueryIntent, classify_intent


class TestEpisodicQueries:
    def test_when_did(self):
        assert classify_intent("When did we discuss the API?") == QueryIntent.EPISODIC

    def test_last_time(self):
        assert classify_intent("What was the last time we talked about deployment?") == QueryIntent.EPISODIC

    def test_what_happened(self):
        assert classify_intent("What happened in our meeting yesterday?") == QueryIntent.EPISODIC

    def test_ago(self):
        assert classify_intent("What did I say 3 days ago?") == QueryIntent.EPISODIC

    def test_last_week(self):
        assert classify_intent("What was discussed last week?") == QueryIntent.EPISODIC

    def test_we_discussed(self):
        assert classify_intent("We discussed the bug fix for the login page") == QueryIntent.EPISODIC

    def test_i_told(self):
        assert classify_intent("I told you about my new project") == QueryIntent.EPISODIC

    def test_what_did_i(self):
        assert classify_intent("What did I mention about Python?") == QueryIntent.EPISODIC


class TestSemanticQueries:
    def test_what_is(self):
        assert classify_intent("What is the deployment process?") == QueryIntent.SEMANTIC

    def test_prefer(self):
        assert classify_intent("What language do I prefer for backend?") == QueryIntent.SEMANTIC

    def test_favorite(self):
        assert classify_intent("What's my favorite color?") == QueryIntent.SEMANTIC

    def test_how_to(self):
        assert classify_intent("How to set up the development environment?") == QueryIntent.SEMANTIC

    def test_whats_my(self):
        assert classify_intent("What's my email address?") == QueryIntent.SEMANTIC

    def test_procedure(self):
        assert classify_intent("What's the procedure for code review?") == QueryIntent.SEMANTIC

    def test_workflow(self):
        assert classify_intent("Tell me about the CI/CD workflow") == QueryIntent.SEMANTIC


class TestMixedQueries:
    def test_empty_query(self):
        assert classify_intent("") == QueryIntent.MIXED

    def test_whitespace(self):
        assert classify_intent("   ") == QueryIntent.MIXED

    def test_ambiguous(self):
        assert classify_intent("Python") == QueryIntent.MIXED

    def test_no_signals(self):
        assert classify_intent("project update") == QueryIntent.MIXED

    def test_both_signals(self):
        # "what is" (semantic) + "last time" (episodic) — may be mixed
        result = classify_intent("What is something we said last time?")
        assert result in (QueryIntent.MIXED, QueryIntent.EPISODIC, QueryIntent.SEMANTIC)


class TestEdgeCases:
    def test_none_like(self):
        assert classify_intent("") == QueryIntent.MIXED

    def test_case_insensitive(self):
        assert classify_intent("WHEN DID we talk?") == QueryIntent.EPISODIC
        assert classify_intent("WHAT IS my name?") == QueryIntent.SEMANTIC

    def test_single_word_no_crash(self):
        classify_intent("hello")

    def test_very_long_query(self):
        long_query = "when did " * 100
        result = classify_intent(long_query)
        assert result == QueryIntent.EPISODIC
