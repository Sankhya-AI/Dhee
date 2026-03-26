from __future__ import annotations

import json
from pathlib import Path

from scripts.run_locomo_plus_engram import (
    _build_output_record,
    _extract_question_from_prompt,
    _load_existing_predictions,
    _parse_cognitive_sessions,
    _parse_locomo_sessions,
    _strip_question_block,
)


def test_parse_locomo_sessions_with_date_blocks_and_question_strip() -> None:
    prompt = (
        "DATE: 1:56 pm on 8 May, 2023\n"
        "CONVERSATION:\n"
        'Alice said, "Hi Bob."\n'
        'Bob said, "Hello Alice."\n'
        "\n"
        "DATE: 9:00 am on 9 May, 2023\n"
        "CONVERSATION:\n"
        'Alice said, "I spent $50 yesterday."\n'
        "\n"
        "Question: When did Alice spend money?\n"
    )

    cleaned = _strip_question_block(prompt)
    assert "Question:" not in cleaned

    sessions = _parse_locomo_sessions(prompt)
    assert len(sessions) == 2
    assert sessions[0]["session_id"] == "S1"
    assert sessions[0]["session_date"] == "1:56 pm on 8 May, 2023"
    assert sessions[0]["turns"][0]["speaker"] == "Alice"
    assert sessions[0]["turns"][0]["text"] == "Hi Bob."
    assert sessions[1]["turns"][0]["text"] == "I spent $50 yesterday."


def test_parse_cognitive_sessions_from_stitched_dialogue() -> None:
    prompt = (
        'Caroline said, "I started saying no to extra work."\n'
        'Melanie said, "That probably lowered your stress."\n'
    )
    sessions = _parse_cognitive_sessions(prompt, time_gap="two weeks later")
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "S1"
    assert sessions[0]["session_date"] == "two weeks later"
    assert len(sessions[0]["turns"]) == 2
    assert sessions[0]["turns"][1]["speaker"] == "Melanie"


def test_extract_question_from_prompt() -> None:
    prompt = "DATE: x\nCONVERSATION:\nAlice said, \"a\"\n\nQuestion: What happened?"
    assert _extract_question_from_prompt(prompt) == "What happened?"
    assert _extract_question_from_prompt("No explicit question marker") == ""


def test_build_output_record_schema_optional_time_gap() -> None:
    row = _build_output_record(
        sample_index=12,
        question_input="What changed?",
        evidence="Alice: changed jobs",
        category="temporal",
        ground_truth="May 2023",
        prediction="May 2023",
        model="deepseek-ai/deepseek-v3.1",
    )
    assert row["sample_index"] == 12
    assert row["question_input"] == "What changed?"
    assert row["category"] == "temporal"
    assert row["ground_truth"] == "May 2023"
    assert row["prediction"] == "May 2023"
    assert row["model"] == "deepseek-ai/deepseek-v3.1"
    assert "time_gap" not in row

    row2 = _build_output_record(
        sample_index=13,
        question_input="A: I feel overwhelmed.",
        evidence="Alice: learned boundaries",
        category="Cognitive",
        ground_truth="",
        prediction="That makes sense given what you went through.",
        model="deepseek-ai/deepseek-v3.1",
        time_gap="two weeks later",
    )
    assert row2["time_gap"] == "two weeks later"


def test_load_existing_predictions_uses_sample_index_and_order_fallback(tmp_path: Path) -> None:
    payload = [
        {
            "sample_index": 3,
            "question_input": "q3",
            "category": "temporal",
            "ground_truth": "g3",
            "prediction": "p3",
            "model": "m",
            "evidence": "",
        },
        {
            # no sample_index -> fallback to row order index (2)
            "question_input": "q2",
            "category": "temporal",
            "ground_truth": "g2",
            "prediction": "p2",
            "model": "m",
            "evidence": "",
        },
    ]
    out = tmp_path / "predictions.json"
    out.write_text(json.dumps(payload), encoding="utf-8")

    by_index = _load_existing_predictions(out)
    assert sorted(by_index.keys()) == [2, 3]
    assert by_index[3]["question_input"] == "q3"
    assert by_index[2]["question_input"] == "q2"
