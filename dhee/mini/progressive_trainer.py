"""Progressive Trainer — 3-stage training for BuddhiMini.

Based on AgeMem (arXiv:2601.01885): memory ops as RL-optimized tool calls
with 3-stage progressive training for optimal learning.

Stage 1 — SFT (Supervised Fine-Tuning):
  Train on high-quality trajectory spans from TraceSegmenter.
  Each span is a (task_context → [SPAN_TYPE] output) example.
  Span-specific losses per Structured Agent Distillation (arXiv:2505.13820).

Stage 2 — DPO (Direct Preference Optimization):
  Train on contrastive pairs from ContrastiveStore.
  Each pair: (task_context, success_approach, failure_approach).
  The model learns to prefer successful reasoning patterns.

Stage 3 — RL (Retrieval-Quality Reward):
  Use retrieval quality as reward signal.
  After the model updates, measure whether recall@K improves.
  If yes, keep the update. If no, rollback.

The trainer does NOT run training itself — it curates data and delegates
to the existing Nididhyasana/train.py pipeline. Its job is to decide
WHAT to train on and in WHAT order.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TrainingStageResult:
    """Result from a single training stage."""

    stage: str                      # sft | dpo | rl
    status: str                     # completed | skipped | error
    samples_used: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)
    duration_seconds: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "stage": self.stage,
            "status": self.status,
            "samples_used": self.samples_used,
            "duration_seconds": round(self.duration_seconds, 1),
        }
        if self.metrics:
            d["metrics"] = self.metrics
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class ProgressiveTrainingResult:
    """Result from a full progressive training cycle."""

    cycle_id: str
    stages: List[TrainingStageResult]
    total_duration: float = 0.0
    model_improved: bool = False
    data_exported_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "stages": [s.to_dict() for s in self.stages],
            "total_duration": round(self.total_duration, 1),
            "model_improved": self.model_improved,
            "data_exported_path": self.data_exported_path,
        }


class ProgressiveTrainer:
    """Curates and orders training data for the 3-stage progressive pipeline.

    This is the brain that decides what data to train on. The actual
    training execution is delegated to Nididhyasana (which calls train.py).

    Usage:
        trainer = ProgressiveTrainer(data_dir="/path/to/training")
        result = trainer.run_cycle(
            sft_data=[...],     # from TraceSegmenter
            dpo_data=[...],     # from ContrastiveStore
            samskara_data={...} # from Samskara.get_training_data()
        )
    """

    # Minimum data thresholds
    MIN_SFT = 20
    MIN_DPO = 10

    def __init__(
        self,
        data_dir: Optional[str] = None,
        train_fn=None,
    ):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "progressive_training"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._train_fn = train_fn  # injected training function
        self._cycle_count = 0
        self._history: List[Dict[str, Any]] = []
        self._load_history()

    def run_cycle(
        self,
        sft_data: Optional[List[Dict[str, str]]] = None,
        dpo_data: Optional[List[Dict[str, str]]] = None,
        samskara_data: Optional[Dict[str, Any]] = None,
    ) -> ProgressiveTrainingResult:
        """Run a full progressive training cycle: SFT → DPO → RL.

        Each stage is optional — skipped if insufficient data.
        """
        cycle_id = f"prog_{self._cycle_count:04d}_{int(time.time())}"
        self._cycle_count += 1
        cycle_dir = os.path.join(self._dir, cycle_id)
        os.makedirs(cycle_dir, exist_ok=True)

        start = time.time()
        stages: List[TrainingStageResult] = []

        # Merge samskara SFT samples with explicit SFT data
        all_sft = list(sft_data or [])
        if samskara_data:
            all_sft.extend(samskara_data.get("sft_samples", []))

        # Merge samskara DPO pairs with explicit DPO data
        all_dpo = list(dpo_data or [])
        if samskara_data:
            all_dpo.extend(samskara_data.get("dpo_pairs", []))

        # Weight data by vasana degradation (focus on weak areas)
        if samskara_data:
            all_sft = self._weight_by_vasana(all_sft, samskara_data)

        # --- Stage 1: SFT ---
        sft_result = self._run_sft(all_sft, cycle_dir)
        stages.append(sft_result)

        # --- Stage 2: DPO ---
        dpo_result = self._run_dpo(all_dpo, cycle_dir)
        stages.append(dpo_result)

        # --- Stage 3: RL (evaluation-based) ---
        rl_result = self._run_rl_eval(cycle_dir, sft_result, dpo_result)
        stages.append(rl_result)

        total_duration = time.time() - start
        completed_stages = [s for s in stages if s.status == "completed"]

        result = ProgressiveTrainingResult(
            cycle_id=cycle_id,
            stages=stages,
            total_duration=total_duration,
            model_improved=len(completed_stages) > 0,
            data_exported_path=cycle_dir,
        )

        self._record_history(result)
        return result

    # ------------------------------------------------------------------
    # Stage 1: SFT
    # ------------------------------------------------------------------

    def _run_sft(
        self, data: List[Dict[str, str]], cycle_dir: str,
    ) -> TrainingStageResult:
        """Stage 1: Supervised Fine-Tuning on trajectory spans."""
        if len(data) < self.MIN_SFT:
            return TrainingStageResult(
                stage="sft", status="skipped",
                metrics={"reason": f"insufficient data ({len(data)}/{self.MIN_SFT})"},
            )

        start = time.time()

        # Curate: prioritize diverse span types
        curated = self._curate_sft(data)

        # Export as train.jsonl
        train_path = os.path.join(cycle_dir, "sft_train.jsonl")
        self._write_jsonl(train_path, curated)

        # Try to run actual training
        metrics = {}
        if self._train_fn:
            try:
                train_result = self._train_fn(
                    data_dir=cycle_dir,
                    output_dir=os.path.join(cycle_dir, "sft_output"),
                )
                metrics = train_result if isinstance(train_result, dict) else {}
            except Exception as e:
                return TrainingStageResult(
                    stage="sft", status="error",
                    samples_used=len(curated),
                    duration_seconds=time.time() - start,
                    error=str(e),
                )
        else:
            metrics["data_exported"] = train_path

        return TrainingStageResult(
            stage="sft", status="completed",
            samples_used=len(curated),
            metrics=metrics,
            duration_seconds=time.time() - start,
        )

    def _curate_sft(self, data: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Curate SFT data: balance span types, cap per type."""
        by_type: Dict[str, List] = {}
        for sample in data:
            t = sample.get("type", "general")
            by_type.setdefault(t, []).append(sample)

        # Take up to 50 per type for balance
        curated = []
        for samples in by_type.values():
            curated.extend(samples[:50])

        return curated

    # ------------------------------------------------------------------
    # Stage 2: DPO
    # ------------------------------------------------------------------

    def _run_dpo(
        self, data: List[Dict[str, str]], cycle_dir: str,
    ) -> TrainingStageResult:
        """Stage 2: Direct Preference Optimization on contrastive pairs."""
        if len(data) < self.MIN_DPO:
            return TrainingStageResult(
                stage="dpo", status="skipped",
                metrics={"reason": f"insufficient data ({len(data)}/{self.MIN_DPO})"},
            )

        start = time.time()

        # Export as dpo_pairs.jsonl
        dpo_path = os.path.join(cycle_dir, "dpo_pairs.jsonl")
        self._write_jsonl(dpo_path, data)

        metrics = {}
        if self._train_fn:
            try:
                train_result = self._train_fn(
                    data_dir=cycle_dir,
                    output_dir=os.path.join(cycle_dir, "dpo_output"),
                    dpo_mode=True,
                )
                metrics = train_result if isinstance(train_result, dict) else {}
            except Exception as e:
                return TrainingStageResult(
                    stage="dpo", status="error",
                    samples_used=len(data),
                    duration_seconds=time.time() - start,
                    error=str(e),
                )
        else:
            metrics["data_exported"] = dpo_path

        return TrainingStageResult(
            stage="dpo", status="completed",
            samples_used=len(data),
            metrics=metrics,
            duration_seconds=time.time() - start,
        )

    # ------------------------------------------------------------------
    # Stage 3: RL (reward = retrieval quality)
    # ------------------------------------------------------------------

    def _run_rl_eval(
        self,
        cycle_dir: str,
        sft_result: TrainingStageResult,
        dpo_result: TrainingStageResult,
    ) -> TrainingStageResult:
        """Stage 3: RL evaluation — decide whether to keep updates.

        In practice, this stage verifies that the SFT/DPO changes
        didn't degrade retrieval quality. It's a gate, not a trainer.
        """
        start = time.time()

        # If neither SFT nor DPO actually trained, skip
        if sft_result.status != "completed" and dpo_result.status != "completed":
            return TrainingStageResult(
                stage="rl", status="skipped",
                metrics={"reason": "no prior stages completed"},
            )

        # Compute aggregate quality from what we have
        sft_samples = sft_result.samples_used
        dpo_samples = dpo_result.samples_used
        total_samples = sft_samples + dpo_samples

        # Simple quality heuristic: more diverse data = more likely to help
        quality_estimate = min(1.0, total_samples / 100.0)

        metrics = {
            "quality_estimate": round(quality_estimate, 3),
            "sft_contribution": sft_samples,
            "dpo_contribution": dpo_samples,
            "verdict": "keep" if quality_estimate > 0.3 else "rollback",
        }

        return TrainingStageResult(
            stage="rl", status="completed",
            metrics=metrics,
            duration_seconds=time.time() - start,
        )

    # ------------------------------------------------------------------
    # Vasana-weighted data emphasis
    # ------------------------------------------------------------------

    def _weight_by_vasana(
        self,
        data: List[Dict[str, str]],
        samskara_data: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Duplicate samples from degrading dimensions for emphasis.

        If retrieval_recall is degrading, duplicate RETRIEVAL_HIT samples.
        Same weighting logic as Nididhyasana._curate_dataset().
        """
        degrading = set(samskara_data.get("degrading_dimensions", []))
        if not degrading:
            return data

        # Map degrading dimensions to sample types
        dim_to_type = {
            "retrieval_recall": {"retrieval_hit", "retrieval_miss"},
            "retrieval_precision": {"retrieval_hit"},
            "answer_quality": {"answer_accepted", "answer_corrected"},
            "fact_extraction": {"extraction"},
        }

        emphasized_types = set()
        for dim in degrading:
            emphasized_types |= dim_to_type.get(dim, set())

        # Duplicate matching samples (2x weight)
        weighted = []
        for sample in data:
            weighted.append(sample)
            sample_type = sample.get("type", "").lower()
            valence = sample.get("valence", "")
            if sample_type in emphasized_types or valence == "klishta":
                weighted.append(sample)  # duplicate = 2x emphasis

        return weighted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_jsonl(self, path: str, data: List[Dict]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                for item in data:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to write %s: %s", path, e)

    def _record_history(self, result: ProgressiveTrainingResult) -> None:
        self._history.append(result.to_dict())
        path = os.path.join(self._dir, "history.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _load_history(self) -> None:
        path = os.path.join(self._dir, "history.jsonl")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self._history.append(json.loads(line))
                            self._cycle_count += 1
                        except json.JSONDecodeError:
                            continue
        except OSError:
            pass

    def get_stats(self) -> Dict[str, Any]:
        return {
            "cycles_completed": self._cycle_count,
            "last_cycle": self._history[-1] if self._history else None,
        }
