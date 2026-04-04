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


