"""M7.4 regression — Samskara → replay-gate corpus exporter.

Plan reference: encapsulated-rolling-bengio.md, Movement 7.4.

These tests lock in the memory-layer-native capture rule: the replay
corpus is derived from the *existing* durable samskara log, not from a
separate bolt-on collector. And it only emits records we actually have
ground truth for — ANSWER_ACCEPTED (prompt+output is validated) or
ANSWER_CORRECTED (prompt→corrected_text is direct supervision).
"""

from __future__ import annotations

import json
import os

import pytest

from dhee.core.samskara import SamskaraCollector
from dhee.mini.replay_gate import ReplayGate


@pytest.fixture()
def collector(tmp_path):
    return SamskaraCollector(log_dir=str(tmp_path / "samskaras"))


class TestExportShape:
    def test_no_log_returns_zero_records(self, collector, tmp_path):
        summary = collector.export_replay_corpus(
            str(tmp_path / "corpus")
        )
        assert summary["record_count"] == 0
        # path is None when we have nothing to write
        assert summary["path"] is None

    def test_accepted_and_corrected_become_corpus_records(
        self, collector, tmp_path
    ):
        collector.on_answer_accepted(
            query="who wrote the Yoga Sutras?",
            answer="Patanjali",
            memory_ids=["m1"],
        )
        collector.on_answer_corrected(
            query="when were the Yoga Sutras composed?",
            wrong_answer="800 BCE",
            correct_answer="circa 200 BCE",
            memory_ids=["m2"],
        )

        summary = collector.export_replay_corpus(str(tmp_path / "corpus"))
        assert summary["record_count"] == 2
        assert summary["accepted_count"] == 1
        assert summary["corrected_count"] == 1
        assert summary["path"] is not None
        assert os.path.exists(summary["path"])

        with open(summary["path"], "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        accepted = next(r for r in records
                        if r["metadata"]["source"] == "accepted")
        corrected = next(r for r in records
                         if r["metadata"]["source"] == "corrected")

        assert accepted["prompt"] == "who wrote the Yoga Sutras?"
        assert accepted["expected"] == "Patanjali"
        assert corrected["prompt"] == "when were the Yoga Sutras composed?"
        assert corrected["expected"] == "circa 200 BCE"

    def test_non_answer_samskaras_are_not_exported(
        self, collector, tmp_path
    ):
        # retrieval hit produces a samskara but no prompt/expected pair
        collector.on_retrieval(
            query="anything",
            retrieved_ids=["m1"],
            was_useful=True,
        )
        summary = collector.export_replay_corpus(
            str(tmp_path / "corpus")
        )
        assert summary["record_count"] == 0

    def test_max_records_keeps_most_recent(self, collector, tmp_path):
        for i in range(5):
            collector.on_answer_accepted(
                query=f"q{i}", answer=f"a{i}", memory_ids=[],
            )
        summary = collector.export_replay_corpus(
            str(tmp_path / "corpus"), max_records=2,
        )
        assert summary["record_count"] == 2
        with open(summary["path"], "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        # Most recent two — q3 and q4.
        prompts = {r["prompt"] for r in records}
        assert prompts == {"q3", "q4"}


class TestReplayGateIntegration:
    def test_exported_corpus_feeds_replay_gate(self, collector, tmp_path):
        for i in range(6):
            collector.on_answer_accepted(
                query=f"q{i}", answer=f"a{i}", memory_ids=[],
            )
        corpus_dir = str(tmp_path / "corpus")
        summary = collector.export_replay_corpus(corpus_dir)
        assert summary["record_count"] == 6

        def _eval(path, corpus):
            assert len(corpus) == 6
            # each record should have prompt + expected + metadata
            assert all({"prompt", "expected", "metadata"} <= r.keys()
                       for r in corpus)
            return {"cand": 0.8, "base": 0.6}[path]

        gate = ReplayGate(corpus_dir, evaluator=_eval)
        verdict = gate.evaluate("cand", "base")
        assert verdict.passed is True
        assert verdict.reason == "promoted"
        assert verdict.corpus_size == 6
