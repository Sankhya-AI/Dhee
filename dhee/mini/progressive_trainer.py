"""ProgressiveTrainer — 3-stage SFT \u2192 DPO \u2192 RL-gate pipeline.

Called from ``dhee.training.nididhyasana.NididhyasanaLoop._run_training``
once ``should_evolve()`` has fired. The trainer\'s job is to take what
Samskara has collected during live use and produce a candidate
DheeModel \u2014 *only* when enough signal has accumulated AND the
heavyweight training deps (unsloth / transformers / trl / datasets)
are installed.

Design guarantees (non-negotiable, see the brutal-honesty feedback):

1. **No fake work.** When training deps are missing, every stage\'s
   status is ``"not_available"`` and ``model_improved=False``. The
   caller gets a structured result they can log and surface, not a
   silent stub.
2. **No silent promotion.** Even when SFT succeeds, ``model_improved``
   only flips to True when the RL gate passes. Until the gate is wired
   (M7.3), ``model_improved`` stays False by construction.
3. **Structured provenance.** Every ``run_cycle`` appends a line to
   ``<data_dir>/cycles.jsonl`` so ``dhee doctor`` can show real
   training activity.

The call contract matches Nididhyasana\'s expectation verbatim:

    prog_result = self._progressive_trainer.run_cycle(samskara_data=...)
    if prog_result.model_improved:
        return {"progressive": True, "stages": [s.to_dict() for s in
                prog_result.stages], "data_path":
                prog_result.data_exported_path or ""}
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Stage statuses are deliberately a small closed set so doctor output
# and downstream aggregation stay machine-readable.
_STAGE_OK = "ok"
_STAGE_SKIPPED = "skipped"
_STAGE_NOT_AVAILABLE = "not_available"
_STAGE_NOT_IMPLEMENTED = "not_implemented"
_STAGE_ERROR = "error"


@dataclass
class Stage:
    """One leg of the progressive pipeline."""

    name: str
    status: str = _STAGE_SKIPPED
    samples: int = 0
    metrics: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProgressiveResult:
    """Structured outcome of a ``run_cycle`` call."""

    cycle_id: str
    started_at: float
    completed_at: float = 0.0
    model_improved: bool = False
    stages: List[Stage] = field(default_factory=list)
    data_exported_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "model_improved": self.model_improved,
            "stages": [s.to_dict() for s in self.stages],
            "data_exported_path": self.data_exported_path,
            "error": self.error,
        }


class ProgressiveTrainer:
    """Thin orchestrator for the 3-stage training pipeline.

    The trainer itself holds no ML state. All ML work is delegated to
    ``dhee.training.train.train()`` (SFT) and \u2014 when wired \u2014 the DPO +
    RL-gate callables. This keeps the import cost of ``dhee.mini`` to a
    handful of dataclasses; the heavyweight imports only trigger if
    ``run_cycle`` is actually invoked AND the deps are present.
    """

    def __init__(
        self,
        data_dir: str,
        *,
        min_sft_samples: int = 50,
        min_dpo_pairs: int = 10,
        replay_corpus_dir: Optional[str] = None,
        replay_evaluator: Optional[Any] = None,
        incumbent_model_path: Optional[str] = None,
    ) -> None:
        self._data_dir = data_dir
        self._sft_data_dir = os.path.join(data_dir, "sft")
        self._dpo_data_dir = os.path.join(data_dir, "dpo")
        self._cycles_path = os.path.join(data_dir, "cycles.jsonl")
        self._min_sft_samples = int(min_sft_samples)
        self._min_dpo_pairs = int(min_dpo_pairs)
        self._replay_corpus_dir = replay_corpus_dir
        self._replay_evaluator = replay_evaluator
        self._incumbent_model_path = incumbent_model_path

        os.makedirs(self._sft_data_dir, exist_ok=True)
        os.makedirs(self._dpo_data_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        samskara_data: Dict[str, Any],
        *,
        base_model: Optional[str] = None,
    ) -> ProgressiveResult:
        """Run one SFT \u2192 DPO \u2192 RL-gate pass on the supplied samskara data.

        The returned ``ProgressiveResult`` is always structured; this
        method never raises on missing deps or insufficient data. On
        any unrecoverable failure we still return a result with an
        ``error`` field and all-zero stages.
        """
        cycle = ProgressiveResult(
            cycle_id=str(uuid.uuid4()),
            started_at=time.time(),
        )

        try:
            sft_samples = list(samskara_data.get("sft_samples") or [])
            dpo_pairs = list(samskara_data.get("dpo_pairs") or [])

            sft_stage = self._stage_sft(sft_samples, base_model=base_model)
            cycle.stages.append(sft_stage)
            cycle.data_exported_path = (
                sft_stage.metrics.get("train_path") or None
            )

            dpo_stage = self._stage_dpo(dpo_pairs)
            cycle.stages.append(dpo_stage)

            gate_stage = self._stage_rl_gate(sft_stage, dpo_stage)
            cycle.stages.append(gate_stage)

            cycle.model_improved = gate_stage.status == _STAGE_OK
        except Exception as exc:  # pragma: no cover \u2014 defensive
            logger.exception("ProgressiveTrainer cycle crashed")
            cycle.error = f"{type(exc).__name__}: {exc}"

        cycle.completed_at = time.time()
        self._append_cycle_record(cycle)
        return cycle

    # ------------------------------------------------------------------
    # Stage 1: SFT
    # ------------------------------------------------------------------

    def _stage_sft(
        self,
        sft_samples: List[Dict[str, Any]],
        *,
        base_model: Optional[str],
    ) -> Stage:
        stage = Stage(name="sft", samples=len(sft_samples))

        if len(sft_samples) < self._min_sft_samples:
            stage.status = _STAGE_SKIPPED
            stage.note = (
                f"insufficient SFT samples: {len(sft_samples)} < "
                f"{self._min_sft_samples} required"
            )
            return stage

        train_path = os.path.join(self._sft_data_dir, "train.jsonl")
        try:
            self._write_sft_jsonl(train_path, sft_samples)
        except OSError as exc:
            stage.status = _STAGE_ERROR
            stage.note = f"failed to write SFT data: {exc}"
            return stage

        stage.metrics["train_path"] = train_path

        # Import training entrypoint lazily so the ``dhee.mini`` import
        # cost stays tiny on machines that never train.
        try:
            from dhee.training.train import train as run_train
        except Exception as exc:
            stage.status = _STAGE_NOT_AVAILABLE
            stage.note = f"dhee.training.train not importable: {exc}"
            return stage

        try:
            kwargs: Dict[str, Any] = {"data_dir": self._sft_data_dir}
            if base_model:
                kwargs["base_model"] = base_model
            result = run_train(**kwargs)
        except Exception as exc:
            stage.status = _STAGE_ERROR
            stage.note = f"SFT train() raised: {type(exc).__name__}: {exc}"
            return stage

        if not isinstance(result, dict) or result.get("error"):
            # train() returns {"error": "..."} when unsloth / transformers
            # aren\'t installed. Treat that as not_available, not ok.
            stage.status = _STAGE_NOT_AVAILABLE
            stage.note = str(result.get("error") if isinstance(result, dict)
                             else "train() returned non-dict")
            return stage

        stage.status = _STAGE_OK
        stage.metrics.update({
            k: v for k, v in result.items()
            if k in {"model_path", "merged_model_dir", "epochs",
                     "train_samples", "val_samples", "quantization",
                     "base_model"}
        })
        stage.note = "SFT run completed"
        return stage

    # ------------------------------------------------------------------
    # Stage 2: DPO
    # ------------------------------------------------------------------

    def _stage_dpo(self, dpo_pairs: List[Dict[str, Any]]) -> Stage:
        stage = Stage(name="dpo", samples=len(dpo_pairs))

        if len(dpo_pairs) < self._min_dpo_pairs:
            stage.status = _STAGE_SKIPPED
            stage.note = (
                f"insufficient DPO pairs: {len(dpo_pairs)} < "
                f"{self._min_dpo_pairs} required"
            )
            return stage

        # We export DPO pairs to disk so future in-tree or external
        # trainers can pick them up. The DPO pass itself is not yet
        # wired \u2014 being truthful about this keeps doctor output honest.
        dpo_path = os.path.join(self._dpo_data_dir, "pairs.jsonl")
        try:
            with open(dpo_path, "w", encoding="utf-8") as f:
                for pair in dpo_pairs:
                    f.write(json.dumps(pair, ensure_ascii=False) + "\n")
            stage.metrics["dpo_path"] = dpo_path
        except OSError as exc:
            stage.status = _STAGE_ERROR
            stage.note = f"failed to write DPO pairs: {exc}"
            return stage

        stage.status = _STAGE_NOT_IMPLEMENTED
        stage.note = (
            "DPO pairs exported to disk; in-tree DPO training not yet wired"
        )
        return stage

    # ------------------------------------------------------------------
    # Stage 3: RL gate
    # ------------------------------------------------------------------

    def _stage_rl_gate(self, sft_stage: Stage, dpo_stage: Stage) -> Stage:
        stage = Stage(name="rl_gate")

        # If SFT itself didn\'t produce a model, no gate decision is
        # meaningful. Mark as skipped with the upstream reason.
        if sft_stage.status != _STAGE_OK:
            stage.status = _STAGE_SKIPPED
            stage.note = f"SFT stage did not produce a model ({sft_stage.status})"
            return stage

        # Prefer merged_model_dir (HF layout, scoreable by the default
        # karma evaluator); fall back to model_path (may be GGUF, which
        # needs a custom llama.cpp evaluator).
        candidate_path = (
            sft_stage.metrics.get("merged_model_dir")
            or sft_stage.metrics.get("model_path")
        )
        stage.metrics["sft_model_path"] = candidate_path

        # Without a corpus dir configured we preserve legacy behaviour:
        # honestly not_implemented, refuse to flip model_improved.
        if not self._replay_corpus_dir:
            stage.status = _STAGE_NOT_IMPLEMENTED
            stage.note = (
                "RL gate not wired (no replay_corpus_dir configured); "
                "candidate retained but not hot-swapped"
            )
            return stage

        try:
            from dhee.mini.replay_gate import ReplayGate
        except Exception as exc:
            stage.status = _STAGE_NOT_AVAILABLE
            stage.note = f"ReplayGate import failed: {exc}"
            return stage

        gate = ReplayGate(
            self._replay_corpus_dir,
            evaluator=self._replay_evaluator,
        )
        verdict = gate.evaluate(
            candidate_path or "",
            incumbent_model_path=self._incumbent_model_path,
        )

        stage.metrics["verdict"] = verdict.to_dict()
        stage.metrics["corpus_size"] = verdict.corpus_size

        if verdict.passed:
            stage.status = _STAGE_OK
            stage.note = (
                f"candidate beat incumbent by {verdict.delta:+.4f}"
            )
            return stage

        # Any non-passing verdict leaves model_improved False. Map the
        # reason to the right stage status so doctor output stays readable.
        if verdict.reason in {"no_corpus", "no_evaluator", "no_incumbent"}:
            stage.status = _STAGE_NOT_AVAILABLE
        elif verdict.reason in {
            "insufficient_samples",
            "insufficient_group_samples",
            "no_candidate",
        }:
            stage.status = _STAGE_SKIPPED
        elif verdict.reason == "evaluator_error":
            stage.status = _STAGE_ERROR
        else:
            stage.status = _STAGE_NOT_IMPLEMENTED
        stage.note = f"RL gate refused promotion: {verdict.reason}"
        return stage

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append_cycle_record(self, cycle: ProgressiveResult) -> None:
        try:
            with open(self._cycles_path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(cycle.to_dict(), ensure_ascii=False) + "\n"
                )
        except OSError as exc:
            logger.debug("Failed to append cycle record: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_sft_jsonl(
        self, path: str, samples: List[Dict[str, Any]]
    ) -> None:
        """Emit the ``{instruction, output}`` format ``dhee.training.train``
        expects. Samskara records arrive as ``{input, output, ...}``.
        """
        with open(path, "w", encoding="utf-8") as f:
            for s in samples:
                instr = str(s.get("input") or s.get("instruction") or "")
                out = str(s.get("corrected") or s.get("output") or "")
                if not instr or not out:
                    continue
                f.write(json.dumps(
                    {"instruction": instr, "output": out},
                    ensure_ascii=False,
                ) + "\n")
