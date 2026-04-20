"""M7.5 regression — karma-based replay-gate evaluator.

Plan reference: encapsulated-rolling-bengio.md, Movement 7.5.

These tests lock in the "no fake work" rule for the default evaluator:
when torch/transformers aren't installed the gate honestly reports
``no_evaluator`` rather than inventing a plausible score. When callers
supply their own evaluator, the wiring still routes through ReplayGate.

The log-likelihood scoring math itself is exercised by ``transformers``'
own test suite whenever a user runs with torch installed; we don't
fabricate a torch stub here just to "cover" that line — that would be
coverage theatre.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

from dhee.mini import ReplayGate, build_karma_evaluator
from dhee.mini.replay_gate import _default_karma_evaluator


# In the Dhee dev/CI environment torch + transformers are deliberately
# not installed (keeping the package import cost tiny). The evaluator
# must honestly report that by returning None.
_HAS_TORCH = True
try:
    import torch  # noqa: F401
    import transformers  # noqa: F401
except Exception:
    _HAS_TORCH = False


class TestHonestDefault:
    @pytest.mark.skipif(_HAS_TORCH, reason="torch is installed; default path is the real evaluator")
    def test_build_karma_evaluator_returns_none_without_deps(self):
        assert build_karma_evaluator() is None

    @pytest.mark.skipif(_HAS_TORCH, reason="torch is installed")
    def test_replay_gate_default_is_none_without_deps(self):
        assert _default_karma_evaluator() is None

    @pytest.mark.skipif(_HAS_TORCH, reason="torch is installed")
    def test_replay_gate_reports_no_evaluator(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        with open(corpus / "shard.jsonl", "w", encoding="utf-8") as f:
            for i in range(6):
                f.write(json.dumps(
                    {"prompt": f"q{i}", "expected": f"a{i}"}
                ) + "\n")
        gate = ReplayGate(str(corpus))  # no evaluator supplied
        verdict = gate.evaluate("cand", "base")
        assert verdict.reason == "no_evaluator"
        assert verdict.passed is False


class TestPluggableEvaluator:
    """When a caller supplies their own evaluator, the gate honors it."""

    def test_custom_evaluator_still_drives_the_gate(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        with open(corpus / "shard.jsonl", "w", encoding="utf-8") as f:
            for i in range(6):
                f.write(json.dumps(
                    {"prompt": f"q{i}", "expected": f"a{i}"}
                ) + "\n")

        scores = {"cand": 0.92, "base": 0.80}

        def _my_eval(path: str, records: List[Dict[str, Any]]) -> float:
            # Receives the full corpus; must return a single float score.
            assert len(records) == 6
            return scores[path]

        gate = ReplayGate(str(corpus), evaluator=_my_eval)
        verdict = gate.evaluate("cand", "base")
        assert verdict.passed is True
        assert verdict.reason == "promoted"
        assert verdict.candidate_score == pytest.approx(0.92)
        assert verdict.incumbent_score == pytest.approx(0.80)


class TestHFDirectoryValidation:
    """build_karma_evaluator refuses paths that aren't HF model dirs."""

    @pytest.mark.skipif(not _HAS_TORCH, reason="requires torch + transformers")
    def test_non_hf_path_raises_clear_error(self, tmp_path):
        evaluator = build_karma_evaluator()
        assert evaluator is not None  # deps are present
        # Pass a random directory with no config.json — must raise a
        # clear message, not a transformers import error.
        with pytest.raises(RuntimeError, match="HF model directory"):
            evaluator(str(tmp_path), [{"prompt": "x", "expected": "y"}])

    @pytest.mark.skipif(not _HAS_TORCH, reason="requires torch + transformers")
    def test_gguf_path_raises_clear_error(self, tmp_path):
        evaluator = build_karma_evaluator()
        assert evaluator is not None
        gguf_path = tmp_path / "model.gguf"
        gguf_path.write_bytes(b"fake")
        with pytest.raises(RuntimeError, match="HF model directory"):
            evaluator(str(gguf_path), [{"prompt": "x", "expected": "y"}])
