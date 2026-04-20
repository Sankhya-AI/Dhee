"""M7.3 regression — ReplayGate + ProgressiveTrainer integration.

Plan reference: encapsulated-rolling-bengio.md, Movement 7.3.

These tests lock in the "no silent promotion" rule: model_improved only
flips True when an evidence-backed evaluator says the candidate beat the
incumbent by at least the promote_delta. Every other path — missing
corpus, missing evaluator, missing incumbent, thin corpus, evaluator
crash, below-threshold delta — must leave model_improved False and
surface a structured reason.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

from dhee.mini import ProgressiveTrainer, ReplayGate
from dhee.mini.replay_gate import GATE_MIN_SAMPLES, GateVerdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_corpus(corpus_dir: str, records: List[Dict[str, Any]]) -> None:
    os.makedirs(corpus_dir, exist_ok=True)
    with open(os.path.join(corpus_dir, "shard-000.jsonl"), "w",
              encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _full_corpus(n: int = GATE_MIN_SAMPLES) -> List[Dict[str, Any]]:
    return [{"prompt": f"p{i}", "expected": f"e{i}"} for i in range(n)]


def _scoring_evaluator(scores: Dict[str, float]):
    def _fn(model_path: str, _corpus: List[Dict[str, Any]]) -> float:
        return scores[model_path]
    return _fn


# ---------------------------------------------------------------------------
# ReplayGate unit tests
# ---------------------------------------------------------------------------


class TestGateVerdict:
    def test_verdict_to_dict_round_trip(self):
        v = GateVerdict(
            passed=True, reason="promoted",
            candidate_score=0.8, incumbent_score=0.7, delta=0.1,
            corpus_size=5, metrics={"promote_delta": 0.02},
        )
        d = v.to_dict()
        assert d["passed"] is True
        assert d["reason"] == "promoted"
        assert d["delta"] == pytest.approx(0.1)


class TestReplayGate:
    def test_missing_corpus_dir_returns_no_corpus(self, tmp_path):
        gate = ReplayGate(str(tmp_path / "does-not-exist"))
        verdict = gate.evaluate("/tmp/cand.gguf", "/tmp/incumbent.gguf")
        assert verdict.passed is False
        assert verdict.reason == "no_corpus"

    def test_thin_corpus_returns_insufficient_samples(self, tmp_path):
        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus(n=2))
        gate = ReplayGate(
            corpus,
            evaluator=_scoring_evaluator({"cand": 1.0, "base": 0.5}),
        )
        verdict = gate.evaluate("cand", "base")
        assert verdict.reason == "insufficient_samples"
        assert verdict.corpus_size == 2
        assert verdict.passed is False

    def test_no_evaluator_returns_no_evaluator(self, tmp_path, monkeypatch):
        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())
        monkeypatch.setattr(
            "dhee.mini.replay_gate._default_karma_evaluator",
            lambda: None,
        )
        gate = ReplayGate(corpus)  # default evaluator is unavailable
        verdict = gate.evaluate("cand", "base")
        assert verdict.reason == "no_evaluator"
        assert verdict.corpus_size == GATE_MIN_SAMPLES
        assert verdict.passed is False

    def test_no_incumbent_refuses_auto_promotion(self, tmp_path):
        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())
        gate = ReplayGate(
            corpus, evaluator=_scoring_evaluator({"cand": 0.9}),
        )
        verdict = gate.evaluate("cand")
        assert verdict.reason == "no_incumbent"
        assert verdict.candidate_score == pytest.approx(0.9)
        assert verdict.passed is False

    def test_candidate_beats_incumbent_above_threshold(self, tmp_path):
        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())
        gate = ReplayGate(
            corpus,
            evaluator=_scoring_evaluator({"cand": 0.80, "base": 0.70}),
        )
        verdict = gate.evaluate("cand", "base")
        assert verdict.passed is True
        assert verdict.reason == "promoted"
        assert verdict.delta == pytest.approx(0.10)

    def test_candidate_below_threshold_regressed(self, tmp_path):
        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())
        # delta = 0.005, below default promote_delta=0.02
        gate = ReplayGate(
            corpus,
            evaluator=_scoring_evaluator({"cand": 0.705, "base": 0.700}),
        )
        verdict = gate.evaluate("cand", "base")
        assert verdict.passed is False
        assert verdict.reason == "regressed"

    def test_evaluator_error_captured_structurally(self, tmp_path):
        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())

        def _boom(*_args, **_kwargs):
            raise RuntimeError("inference runtime offline")

        gate = ReplayGate(corpus, evaluator=_boom)
        verdict = gate.evaluate("cand", "base")
        assert verdict.passed is False
        assert verdict.reason == "evaluator_error"
        assert "RuntimeError" in verdict.metrics["error"]


# ---------------------------------------------------------------------------
# ProgressiveTrainer integration
# ---------------------------------------------------------------------------


def _sft_samples(n: int) -> List[Dict[str, Any]]:
    return [
        {"input": f"p{i}", "output": f"o{i}"}
        for i in range(n)
    ]


class TestProgressiveTrainerReplayGate:
    def test_no_corpus_dir_preserves_legacy_not_implemented(
        self, tmp_path, monkeypatch
    ):
        # With no replay_corpus_dir the trainer behaves exactly as
        # before M7.3: rl_gate is not_implemented, model_improved False.
        def fake_train(**_kwargs):
            return {"model_path": "/tmp/cand.gguf"}

        import dhee.training.train as train_mod
        monkeypatch.setattr(train_mod, "train", fake_train)

        trainer = ProgressiveTrainer(
            data_dir=str(tmp_path / "prog"),
            min_sft_samples=1,
        )
        result = trainer.run_cycle({"sft_samples": _sft_samples(3)})
        gate = next(s for s in result.stages if s.name == "rl_gate")
        assert gate.status == "not_implemented"
        assert result.model_improved is False

    def test_replay_gate_promotes_when_candidate_wins(
        self, tmp_path, monkeypatch
    ):
        def fake_train(**_kwargs):
            return {"model_path": "cand"}

        import dhee.training.train as train_mod
        monkeypatch.setattr(train_mod, "train", fake_train)

        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())

        trainer = ProgressiveTrainer(
            data_dir=str(tmp_path / "prog"),
            min_sft_samples=1,
            replay_corpus_dir=corpus,
            replay_evaluator=_scoring_evaluator({"cand": 0.9, "base": 0.5}),
            incumbent_model_path="base",
        )
        result = trainer.run_cycle({"sft_samples": _sft_samples(3)})
        gate = next(s for s in result.stages if s.name == "rl_gate")
        assert gate.status == "ok"
        assert gate.metrics["verdict"]["reason"] == "promoted"
        assert result.model_improved is True

    def test_replay_gate_refuses_when_candidate_regresses(
        self, tmp_path, monkeypatch
    ):
        def fake_train(**_kwargs):
            return {"model_path": "cand"}

        import dhee.training.train as train_mod
        monkeypatch.setattr(train_mod, "train", fake_train)

        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())

        trainer = ProgressiveTrainer(
            data_dir=str(tmp_path / "prog"),
            min_sft_samples=1,
            replay_corpus_dir=corpus,
            replay_evaluator=_scoring_evaluator({"cand": 0.70, "base": 0.71}),
            incumbent_model_path="base",
        )
        result = trainer.run_cycle({"sft_samples": _sft_samples(3)})
        gate = next(s for s in result.stages if s.name == "rl_gate")
        assert gate.status == "not_implemented"  # mapped from "regressed"
        assert gate.metrics["verdict"]["reason"] == "regressed"
        assert result.model_improved is False

    def test_replay_gate_without_incumbent_reports_no_incumbent(
        self, tmp_path, monkeypatch
    ):
        def fake_train(**_kwargs):
            return {"model_path": "cand"}

        import dhee.training.train as train_mod
        monkeypatch.setattr(train_mod, "train", fake_train)

        corpus = str(tmp_path / "corpus")
        _write_corpus(corpus, _full_corpus())

        trainer = ProgressiveTrainer(
            data_dir=str(tmp_path / "prog"),
            min_sft_samples=1,
            replay_corpus_dir=corpus,
            replay_evaluator=_scoring_evaluator({"cand": 0.9}),
        )
        result = trainer.run_cycle({"sft_samples": _sft_samples(3)})
        gate = next(s for s in result.stages if s.name == "rl_gate")
        assert gate.status == "not_available"
        assert gate.metrics["verdict"]["reason"] == "no_incumbent"
        assert result.model_improved is False
