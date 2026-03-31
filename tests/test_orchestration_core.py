from __future__ import annotations

from typing import Any, Dict, List

import pytest

from dhee.configs.base import MemoryConfig
from dhee.core.episodic_index import extract_episodic_events
from dhee.core.profile import ProfileProcessor
from dhee.memory.main import FullMemory


def test_episodic_extraction_money_duration_and_non_money_numbers() -> None:
    content = (
        "Session ID: S1\n"
        "Session Date: 2026-02-20\n"
        "User Transcript:\n"
        "Alice: I spent $3,750 on tools.\n"
        "Alice: The migration took 4 months.\n"
        "Alice: I own 4 guitars.\n"
    )
    events = extract_episodic_events(
        memory_id="m1",
        user_id="u1",
        content=content,
        metadata={"sample_id": "locomo_x"},
    )

    money_events = [e for e in events if e.get("event_type") == "money"]
    duration_events = [e for e in events if e.get("event_type") == "duration"]
    utterance_events = [e for e in events if e.get("event_type") == "utterance"]

    assert utterance_events
    assert money_events
    assert any(abs(float(e.get("value_num") or 0.0) - 3750.0) < 1e-6 for e in money_events)
    assert duration_events
    assert any(str(e.get("value_unit")) == "month" for e in duration_events)

    # The plain numeric "4 guitars" line should not be misclassified as money.
    assert not any("guitars" in str(e.get("value_text", "")).lower() for e in money_events)
    assert any(str(e.get("normalized_time_start") or "") for e in events)
    assert any(str(e.get("time_granularity") or "") for e in events)
    assert any(str(e.get("entity_key") or "") for e in events)
    assert any(str(e.get("value_norm") or "") for e in events)


def test_profile_speaker_anchoring_prefers_transcript_speakers() -> None:
    processor = ProfileProcessor(db=None, embedder=None, llm=None, config={"use_llm_extraction": False})
    content = (
        "Session ID: S2\n"
        "Session Date: 2026-02-21\n"
        "User Transcript:\n"
        "Assistant: What are your goals this quarter?\n"
        "Chitranjan: I want to launch a memory API.\n"
        "I: I prefer extractive summaries over generative summaries.\n"
    )

    updates = processor.extract_profile_mentions_from_speakers(content=content)
    names = {(u.profile_name, u.profile_type) for u in updates}

    assert ("Chitranjan", "contact") in names
    assert ("self", "self") in names
    # Generic role speakers should not create noisy profiles.
    assert ("Assistant", "contact") not in names


@pytest.fixture()
def memory_instance(tmp_path):
    cfg = MemoryConfig.minimal()
    cfg.history_db_path = str(tmp_path / "history.db")
    cfg.cost_guardrail.enable_cost_counters = False
    cfg.orchestration.enable_orchestrated_search = True
    cfg.orchestration.enable_episodic_index = True
    cfg.orchestration.enable_hierarchical_retrieval = True
    mem = FullMemory(cfg)
    try:
        yield mem
    finally:
        mem.close()


def test_search_orchestrated_skips_map_reduce_when_coverage_sufficient(memory_instance, monkeypatch) -> None:
    mem = memory_instance

    def fake_search(**kwargs: Any) -> Dict[str, Any]:
        return {"results": [{"id": "m1", "memory": "foo", "composite_score": 0.9}]}

    def fake_search_episodes(**kwargs: Any) -> Dict[str, Any]:
        return {
            "results": [{"memory_id": "m1", "value_text": "foo", "event_type": "utterance"}],
            "coverage": {"sufficient": True, "coverage_ratio": 1.0, "event_hit_count": 1, "unique_canonical_keys": 1},
        }

    monkeypatch.setattr(mem, "search", fake_search)
    monkeypatch.setattr(mem, "search_episodes", fake_search_episodes)

    def fail_extract_atomic_facts(**kwargs: Any) -> List[Dict[str, Any]]:
        raise AssertionError("map stage should be skipped when episodic coverage is sufficient")

    monkeypatch.setattr("dhee.memory.main.extract_atomic_facts", fail_extract_atomic_facts)

    payload = mem.search_orchestrated(
        query="How many projects?",
        user_id="u1",
        question_type="multi-session",
        orchestration_mode="hybrid",
        orchestrator_llm=object(),
        rerank=False,
    )

    assert payload["orchestration"]["map_reduce_used"] is False
    assert payload["orchestration"]["reflection_hops"] == 0


def test_search_orchestrated_reflection_hard_caps_to_one_hop(memory_instance, monkeypatch) -> None:
    mem = memory_instance
    search_calls: List[int] = []
    reduce_calls: List[int] = []

    def fake_search(**kwargs: Any) -> Dict[str, Any]:
        search_calls.append(1)
        if len(search_calls) == 1:
            return {"results": [{"id": "m1", "memory": "first", "composite_score": 0.8}]}
        return {"results": [{"id": "m1", "memory": "first", "composite_score": 0.8}, {"id": "m2", "memory": "second", "composite_score": 0.7}]}

    def fake_search_episodes(**kwargs: Any) -> Dict[str, Any]:
        return {
            "results": [{"memory_id": "m1", "value_text": "first", "event_type": "utterance"}],
            "coverage": {"sufficient": False, "coverage_ratio": 0.2, "event_hit_count": 1, "unique_canonical_keys": 1},
        }

    def fake_extract_atomic_facts(**kwargs: Any) -> List[Dict[str, Any]]:
        return [{"value": "4", "relevant": True, "canonical_key": "k1"}]

    def fake_reduce_atomic_facts(**kwargs: Any):
        reduce_calls.append(1)
        if len(reduce_calls) == 1:
            return "I don't know", {}
        return "4", {}

    monkeypatch.setattr(mem, "search", fake_search)
    monkeypatch.setattr(mem, "search_episodes", fake_search_episodes)
    monkeypatch.setattr("dhee.memory.main.extract_atomic_facts", fake_extract_atomic_facts)
    monkeypatch.setattr("dhee.memory.main.reduce_atomic_facts", fake_reduce_atomic_facts)

    payload = mem.search_orchestrated(
        query="How many projects?",
        user_id="u1",
        question_type="multi-session",
        orchestration_mode="hybrid",
        orchestrator_llm=object(),
        reflection_max_hops=1,
        base_search_limit=4,
        search_cap=20,
        rerank=False,
    )

    assert payload["orchestration"]["map_reduce_used"] is True
    assert payload["orchestration"]["reflection_hops"] == 1
    assert payload["reduced_answer"] == "4"
    # Initial retrieval + exactly one reflection retrieval.
    assert len(search_calls) == 2


def test_search_orchestrated_inconsistency_can_trigger_map_reduce(memory_instance, monkeypatch) -> None:
    mem = memory_instance

    def fake_search(**kwargs: Any) -> Dict[str, Any]:
        return {
            "results": [
                {"id": "m1", "memory": "I led 2 projects.", "evidence_text": "I led 2 projects.", "composite_score": 0.9},
                {"id": "m2", "memory": "I led 5 projects.", "evidence_text": "I led 5 projects.", "composite_score": 0.8},
            ]
        }

    def fake_search_episodes(**kwargs: Any) -> Dict[str, Any]:
        return {
            "results": [
                {"memory_id": "m1", "value_text": "I led 2 projects", "event_type": "utterance"},
                {"memory_id": "m2", "value_text": "I led 5 projects", "event_type": "utterance"},
            ],
            "coverage": {
                "sufficient": True,
                "coverage_ratio": 1.0,
                "intent_coverage": 1.0,
                "event_hit_count": 2,
                "unique_canonical_keys": 2,
            },
        }

    monkeypatch.setattr(mem, "search", fake_search)
    monkeypatch.setattr(mem, "search_episodes", fake_search_episodes)
    monkeypatch.setattr("dhee.memory.main.extract_atomic_facts", lambda **kwargs: [{"value": "5", "relevant": True}])
    monkeypatch.setattr("dhee.memory.main.reduce_atomic_facts", lambda **kwargs: ("5", {}))

    payload = mem.search_orchestrated(
        query="How many projects have I led?",
        user_id="u1",
        question_type="multi-session",
        orchestration_mode="hybrid",
        orchestrator_llm=object(),
        rerank=False,
    )

    assert payload["orchestration"]["map_reduce_used"] is True
    assert "count_numeric_conflict" in payload["orchestration"]["reason_codes"]


def test_search_orchestrated_respects_query_llm_budget(memory_instance, monkeypatch) -> None:
    mem = memory_instance
    mem.config.orchestration.max_query_llm_calls = 0

    def fake_search(**kwargs: Any) -> Dict[str, Any]:
        return {"results": [{"id": "m1", "memory": "I led 4 projects.", "composite_score": 0.9}]}

    def fake_search_episodes(**kwargs: Any) -> Dict[str, Any]:
        return {
            "results": [{"memory_id": "m1", "value_text": "I led 4 projects", "event_type": "utterance"}],
            "coverage": {"sufficient": False, "coverage_ratio": 0.2, "intent_coverage": 0.2, "event_hit_count": 1, "unique_canonical_keys": 1},
        }

    monkeypatch.setattr(mem, "search", fake_search)
    monkeypatch.setattr(mem, "search_episodes", fake_search_episodes)

    payload = mem.search_orchestrated(
        query="How many projects have I led?",
        user_id="u1",
        question_type="multi-session",
        orchestration_mode="hybrid",
        orchestrator_llm=object(),
        rerank=False,
    )

    assert payload["orchestration"]["map_reduce_used"] is False
    assert "query_llm_budget_exhausted" in payload["orchestration"]["reason_codes"]


def test_search_orchestrated_reducer_cache_hit(memory_instance, monkeypatch) -> None:
    mem = memory_instance
    extract_calls = {"count": 0}

    def fake_search(**kwargs: Any) -> Dict[str, Any]:
        return {"results": [{"id": "m1", "memory": "I led 4 projects.", "evidence_text": "I led 4 projects.", "composite_score": 0.9}]}

    def fake_search_episodes(**kwargs: Any) -> Dict[str, Any]:
        return {
            "results": [{"memory_id": "m1", "value_text": "I led 4 projects", "event_type": "utterance"}],
            "coverage": {"sufficient": False, "coverage_ratio": 0.2, "intent_coverage": 0.2, "event_hit_count": 1, "unique_canonical_keys": 1},
        }

    def fake_extract_atomic_facts(**kwargs: Any) -> List[Dict[str, Any]]:
        extract_calls["count"] += 1
        return [{"value": "4", "relevant": True, "canonical_key": "projects"}]

    monkeypatch.setattr(mem, "search", fake_search)
    monkeypatch.setattr(mem, "search_episodes", fake_search_episodes)
    monkeypatch.setattr("dhee.memory.main.extract_atomic_facts", fake_extract_atomic_facts)
    monkeypatch.setattr("dhee.memory.main.reduce_atomic_facts", lambda **kwargs: ("4", {}))

    first = mem.search_orchestrated(
        query="How many projects have I led?",
        user_id="u1",
        question_type="multi-session",
        orchestration_mode="hybrid",
        orchestrator_llm=object(),
        rerank=False,
    )
    second = mem.search_orchestrated(
        query="How many projects have I led?",
        user_id="u1",
        question_type="multi-session",
        orchestration_mode="hybrid",
        orchestrator_llm=object(),
        rerank=False,
    )

    assert first["orchestration"]["cache_hit"] is False
    assert second["orchestration"]["cache_hit"] is True
    assert extract_calls["count"] == 1
