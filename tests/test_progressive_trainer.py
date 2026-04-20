"""M7.2 regression \u2014 ProgressiveTrainer stages + honest gating.

Plan reference: encapsulated-rolling-bengio.md, Movement 7.2.

These tests lock in two non-negotiables:

  * when the heavyweight training deps are missing (CI case), every
    stage resolves to a structured status rather than raising, and
    ``model_improved`` stays False;
  * ``model_improved`` never flips True unless the RL gate passes \u2014 and
    the RL gate is deliberately not implemented today, so False is the
    correct answer even when SFT itself succeeds.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import pytest

from dhee.mini.progressive_trainer import (
    ProgressiveResult,
    ProgressiveTrainer,
    Stage,
)
import dhee.mini.progressive_trainer as pt_module


@pytest.fixture()
def trainer(tmp_path):
    return ProgressiveTrainer(
        data_dir=str(tmp_path / "progressive"),
        min_sft_samples=3,
        min_dpo_pairs=2,
    )


def _sft_samples(n: int) -> List[Dict[str, Any]]:
    return [
        {"input": f"[QA] prompt {i}", "output": f"answer {i}",
         "type": "qa", "valence": "positive"}
        for i in range(n)
    ]


def _dpo_pairs(n: int) -> List[Dict[str, Any]]:
    return [
        {"prompt": f"p{i}", "chosen": "good", "rejected": "bad"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Dataclass contracts \u2014 the caller (Nididhyasana) depends on these shapes.
# ---------------------------------------------------------------------------


class TestContracts:
    def test_stage_to_dict_is_round_trippable(self):
        stage = Stage(name="sft", status="ok", samples=5,
                      metrics={"model_path": "/tmp/x"}, note="done")
        d = stage.to_dict()
        assert d["name"] == "sft"
        assert d["status"] == "ok"
        assert d["samples"] == 5
        assert d["metrics"]["model_path"] == "/tmp/x"

    def test_progressive_result_surfaces_stages(self):
        r = ProgressiveResult(cycle_id="c1", started_at=0.0)
        r.stages.append(Stage(name="sft", status="ok"))
        d = r.to_dict()
        assert d["cycle_id"] == "c1"
        assert d["stages"][0]["name"] == "sft"


# ---------------------------------------------------------------------------
# Stage gating
# ---------------------------------------------------------------------------


class TestSFTStage:
    def test_insufficient_samples_skips_cleanly(self, trainer):
        result = trainer.run_cycle({"sft_samples": _sft_samples(1)})
        sft = next(s for s in result.stages if s.name == "sft")
        assert sft.status == "skipped"
        assert sft.samples == 1
        assert result.model_improved is False

    def test_training_deps_missing_returns_not_available(
        self, trainer, monkeypatch
    ):
        # Simulate the common CI case: unsloth/transformers aren\'t
        # installed so run_train returns {"error": ...}.
        def fake_train(**_kwargs):
            return {"error": "Unsloth training requires: pip install unsloth"}

        import dhee.training.train as train_mod
        monkeypatch.setattr(train_mod, "train", fake_train)

        result = trainer.run_cycle({"sft_samples": _sft_samples(5)})
        sft = next(s for s in result.stages if s.name == "sft")
        assert sft.status == "not_available"
        assert "unsloth" in sft.note.lower()
        assert result.model_improved is False

    def test_sft_ok_still_leaves_model_improved_false(
        self, trainer, monkeypatch
    ):
        # Even when SFT would report success, the RL gate is unwired
        # today, so model_improved must stay False \u2014 promotion requires
        # evidence the candidate beats the incumbent.
        def fake_train(**_kwargs):
            return {
                "model_path": "/tmp/candidate.gguf",
                "epochs": 1,
                "train_samples": 5,
                "quantization": "Q4_K_M",
            }

        import dhee.training.train as train_mod
        monkeypatch.setattr(train_mod, "train", fake_train)

        result = trainer.run_cycle({"sft_samples": _sft_samples(5)})
        sft = next(s for s in result.stages if s.name == "sft")
        gate = next(s for s in result.stages if s.name == "rl_gate")
        assert sft.status == "ok"
        assert sft.metrics["model_path"] == "/tmp/candidate.gguf"
        assert gate.status == "not_implemented"
        assert gate.metrics["sft_model_path"] == "/tmp/candidate.gguf"
        assert result.model_improved is False


class TestDPOStage:
    def test_insufficient_pairs_skips(self, trainer):
        result = trainer.run_cycle({
            "sft_samples": _sft_samples(0),
            "dpo_pairs": _dpo_pairs(1),
        })
        dpo = next(s for s in result.stages if s.name == "dpo")
        assert dpo.status == "skipped"
        assert dpo.samples == 1

    def test_sufficient_pairs_exports_and_marks_not_implemented(
        self, trainer
    ):
        result = trainer.run_cycle({
            "sft_samples": _sft_samples(0),
            "dpo_pairs": _dpo_pairs(5),
        })
        dpo = next(s for s in result.stages if s.name == "dpo")
        assert dpo.status == "not_implemented"
        assert "dpo_path" in dpo.metrics
        assert os.path.exists(dpo.metrics["dpo_path"])
        # And the exported content must match what we fed in.
        with open(dpo.metrics["dpo_path"], "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 5
        assert lines[0]["chosen"] == "good"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestCycleProvenance:
    def test_every_cycle_appends_jsonl_record(self, trainer):
        trainer.run_cycle({"sft_samples": _sft_samples(1)})
        trainer.run_cycle({"sft_samples": _sft_samples(0)})

        cycles_path = os.path.join(trainer._data_dir, "cycles.jsonl")
        assert os.path.exists(cycles_path)
        with open(cycles_path, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        assert len(records) == 2
        for r in records:
            assert "cycle_id" in r
            assert "stages" in r
            assert r["model_improved"] is False
