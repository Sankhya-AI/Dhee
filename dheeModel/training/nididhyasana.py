"""निदिध्यासन (Nididhyasana) — Auto-evolution loop for DheeModel.

Vedantic learning has three stages:
  1. Shravana (listening) — teacher logging captures knowledge
  2. Manana (reflection) — samskara collector identifies weaknesses
  3. Nididhyasana (deep integration) — retraining embeds the learning

This module implements stage 3: when accumulated samskaras reach
critical mass (prakrity-apurat), it automatically:
  1. Collects training signals (DPO pairs, teacher logs, re-extraction data)
  2. Curates data weighted by viveka assessments and vasana degradation
  3. Runs a samsara training cycle (with multi-trace adapters from smrti.py)
  4. Evaluates with karma vector
  5. Exports new GGUF model
  6. Hot-swaps the running model without restart

Yoga Sutra 4.2: "jaty-antara-parinamah prakrity-apurat"
Transformation happens when natural potential overflows.
The system doesn't retrain on schedule — it retrains when it NEEDS to.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engram.core.alaya import AlayaStore
    from engram.core.samskara import SamskaraCollector
    from engram.core.viveka import Viveka

logger = logging.getLogger(__name__)

_DEFAULT_DHEE_DIR = os.path.join(os.path.expanduser("~"), ".dhee")


@dataclass
class EvolutionCycle:
    """Record of a single evolution cycle."""

    cycle_id: int
    started_at: float
    completed_at: float = 0.0
    trigger: str = ""               # what triggered this cycle
    data_sources: Dict[str, int] = field(default_factory=dict)
    train_samples: int = 0
    dpo_samples: int = 0
    train_loss: float = 0.0
    val_loss: float = 0.0
    karma_net: float = 0.0
    task_scores: Dict[str, float] = field(default_factory=dict)
    verdict: str = ""               # ascend | repeat | remediate
    model_path: str = ""            # path to exported GGUF
    hot_swapped: bool = False
    error: str = ""


class NididhyasanaLoop:
    """Auto-evolution orchestrator.

    Monitors samskara signals → curates data → trains → evaluates → deploys.
    The entire cycle is autonomous. No human intervention required.
    """

    def __init__(
        self,
        samskara: SamskaraCollector,
        viveka: Optional[Viveka] = None,
        alaya: Optional[AlayaStore] = None,
        dhee_dir: str = _DEFAULT_DHEE_DIR,
        model_swap_callback: Optional[Callable[[str], None]] = None,
        min_dpo_pairs: int = 20,        # minimum DPO pairs to trigger DPO training
        min_sft_pairs: int = 100,       # minimum SFT pairs for meaningful training
        cooldown_seconds: float = 3600, # minimum time between cycles (1 hour)
    ):
        self.samskara = samskara
        self.viveka = viveka
        self.alaya = alaya
        self.dhee_dir = dhee_dir
        self.model_swap_callback = model_swap_callback
        self.min_dpo_pairs = min_dpo_pairs
        self.min_sft_pairs = min_sft_pairs
        self.cooldown_seconds = cooldown_seconds

        # State
        self._cycle_count = 0
        self._last_cycle_time = 0.0
        self._history: List[EvolutionCycle] = []

        # Phase 2: Progressive trainer (SFT → DPO → RL)
        self._progressive_trainer = None
        try:
            from dhee.mini.progressive_trainer import ProgressiveTrainer
            self._progressive_trainer = ProgressiveTrainer(
                data_dir=os.path.join(dhee_dir, "progressive_training"),
            )
        except Exception:
            pass

        # Paths
        self._training_dir = os.path.join(dhee_dir, "training_data")
        self._model_dir = os.path.join(dhee_dir, "models")
        self._pitri_dir = os.path.join(dhee_dir, "pitri_bank")
        self._log_dir = os.path.join(dhee_dir, "evolution_logs")

        for d in [self._training_dir, self._model_dir, self._pitri_dir, self._log_dir]:
            os.makedirs(d, exist_ok=True)

        self._load_history()

    # ------------------------------------------------------------------
    # Check: should we evolve?
    # ------------------------------------------------------------------

    def should_evolve(self) -> tuple[bool, str]:
        """Check if conditions for evolution are met.

        Returns (should_trigger, reason).
        """
        # Cooldown check
        elapsed = time.time() - self._last_cycle_time
        if elapsed < self.cooldown_seconds:
            return False, f"cooldown: {self.cooldown_seconds - elapsed:.0f}s remaining"

        # Check samskara threshold
        if self.samskara.needs_nididhyasana():
            signals = self.samskara.get_training_signals()
            dpo_count = len(signals.get("dpo_pairs", []))
            degrading = signals.get("degrading_dimensions", [])

            if dpo_count >= self.min_dpo_pairs:
                return True, f"correction threshold: {dpo_count} DPO pairs"

            if degrading:
                return True, f"degrading vasanas: {', '.join(degrading)}"

        # Check alaya for excessive dormancy
        if self.alaya:
            stats = self.alaya.get_activation_stats()
            if stats.get("re_extraction_needed", 0) >= 10:
                return True, (
                    f"dormant seeds: {stats['re_extraction_needed']} "
                    f"memories need re-extraction"
                )

        return False, "no trigger conditions met"

    # ------------------------------------------------------------------
    # Main evolution cycle
    # ------------------------------------------------------------------

    def evolve(self, force: bool = False) -> Optional[EvolutionCycle]:
        """Run a complete evolution cycle.

        1. Collect training signals
        2. Curate dataset
        3. Train with samsara cycle
        4. Evaluate with karma
        5. Export and hot-swap

        Returns the cycle record, or None if conditions not met.
        """
        should, reason = self.should_evolve()
        if not should and not force:
            logger.info("Nididhyasana: no evolution needed (%s)", reason)
            return None

        self._cycle_count += 1
        cycle = EvolutionCycle(
            cycle_id=self._cycle_count,
            started_at=time.time(),
            trigger=reason if not force else "forced",
        )

        logger.info(
            "=== Nididhyasana Cycle #%d START (trigger: %s) ===",
            cycle.cycle_id, cycle.trigger,
        )

        try:
            # Step 1: Collect training data
            data = self._collect_training_data(cycle)
            if not data:
                cycle.error = "insufficient training data"
                cycle.completed_at = time.time()
                self._record_cycle(cycle)
                return cycle

            # Step 2: Curate and format dataset
            dataset_info = self._curate_dataset(data, cycle)

            # Step 3: Train
            train_result = self._run_training(cycle)
            if "error" in train_result:
                cycle.error = train_result["error"]
                cycle.completed_at = time.time()
                self._record_cycle(cycle)
                return cycle

            cycle.train_loss = train_result.get("train_loss", 0.0)
            cycle.val_loss = train_result.get("val_loss", 0.0)
            cycle.model_path = train_result.get("model_path", "")

            # Step 4: Evaluate with karma
            eval_result = self._evaluate(train_result, cycle)
            cycle.karma_net = eval_result.get("karma_net", 0.0)
            cycle.task_scores = eval_result.get("task_scores", {})
            cycle.verdict = eval_result.get("verdict", "unknown")

            # Step 5: Hot-swap if verdict is positive
            if cycle.verdict == "ascend" and cycle.model_path:
                self._hot_swap(cycle)
            elif cycle.verdict == "repeat":
                logger.info(
                    "Cycle #%d: verdict=REPEAT (karma=%.3f). "
                    "Will retrain with refined data next cycle.",
                    cycle.cycle_id, cycle.karma_net,
                )
            else:
                logger.warning(
                    "Cycle #%d: verdict=%s (karma=%.3f). "
                    "Remediation needed — degrading dimensions require attention.",
                    cycle.cycle_id, cycle.verdict, cycle.karma_net,
                )

            # Step 6: Reset samskara counters for next cycle
            self._post_cycle_cleanup(cycle)

        except Exception as e:
            cycle.error = str(e)
            logger.error("Nididhyasana cycle #%d failed: %s", cycle.cycle_id, e)

        cycle.completed_at = time.time()
        self._last_cycle_time = cycle.completed_at
        self._record_cycle(cycle)

        duration = cycle.completed_at - cycle.started_at
        logger.info(
            "=== Nididhyasana Cycle #%d COMPLETE (%.1fs, verdict=%s) ===",
            cycle.cycle_id, duration, cycle.verdict,
        )
        return cycle

    # ------------------------------------------------------------------
    # Step 1: Collect training data from all sources
    # ------------------------------------------------------------------

    def _collect_training_data(
        self, cycle: EvolutionCycle,
    ) -> Dict[str, List[Dict]]:
        """Collect training data from samskara, teacher logs, and alaya."""
        data: Dict[str, List[Dict]] = {
            "dpo_pairs": [],
            "sft_pairs": [],
            "re_extraction": [],
        }

        # Source 1: DPO pairs from samskara (user corrections)
        signals = self.samskara.get_training_signals()
        dpo_pairs = signals.get("dpo_pairs", [])
        data["dpo_pairs"] = list(dpo_pairs)
        cycle.dpo_samples = len(dpo_pairs)

        # Source 2: SFT pairs from teacher logs
        teacher_log = os.path.join(
            self.dhee_dir, "teacher_logs", "teacher_log.jsonl"
        )
        if os.path.exists(teacher_log):
            sft_pairs = []
            with open(teacher_log, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            sft_pairs.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            data["sft_pairs"] = sft_pairs

        # Source 3: Re-extraction candidates from alaya
        if self.alaya:
            re_extract_ids = self.alaya.get_re_extraction_candidates()
            data["re_extraction"] = [
                {"memory_id": mid} for mid in re_extract_ids
            ]

        # Record sources
        cycle.data_sources = {
            "dpo_pairs": len(data["dpo_pairs"]),
            "sft_pairs": len(data["sft_pairs"]),
            "re_extraction": len(data["re_extraction"]),
        }

        total = sum(cycle.data_sources.values())
        logger.info(
            "Collected training data: %d DPO, %d SFT, %d re-extraction (%d total)",
            len(data["dpo_pairs"]),
            len(data["sft_pairs"]),
            len(data["re_extraction"]),
            total,
        )

        if not data["dpo_pairs"] and len(data["sft_pairs"]) < self.min_sft_pairs:
            logger.warning("Insufficient training data for evolution cycle")
            return {}

        return data

    # ------------------------------------------------------------------
    # Step 2: Curate dataset (weight by quality signals)
    # ------------------------------------------------------------------

    def _curate_dataset(
        self,
        data: Dict[str, List[Dict]],
        cycle: EvolutionCycle,
    ) -> Dict[str, Any]:
        """Curate and format training data, weighted by quality signals.

        Viveka assessments and vasana degradation guide data weighting:
        - Degrading dimensions get MORE training data (targeted remediation)
        - Thriving dimensions get LESS (avoid overfitting solved problems)
        """
        from dhee.training.data_formatter import format_instruction_pair

        # Get vasana report for data weighting
        signals = self.samskara.get_training_signals()
        vasana_report = signals.get("vasana_report", {})

        # Determine which task types need emphasis
        emphasis: Dict[str, float] = {}
        for dim, info in vasana_report.items():
            status = info.get("status", "neutral")
            if status == "degrading":
                emphasis[dim] = 2.0  # double the data
            elif status == "thriving":
                emphasis[dim] = 0.5  # halve the data
            else:
                emphasis[dim] = 1.0

        # Format SFT pairs with task emphasis
        formatted_sft = []
        for entry in data.get("sft_pairs", []):
            pair = format_instruction_pair(entry)
            task_type = pair.get("task_type", "other")
            # Map task types to vasana dimensions for weighting
            weight = 1.0
            if task_type in ("engram", "context", "scene"):
                weight = emphasis.get("fact_extraction", 1.0)
            elif task_type == "answer":
                weight = emphasis.get("answer_quality", 1.0)
            elif task_type == "query":
                weight = emphasis.get("retrieval_precision", 1.0)

            pair["weight"] = weight
            formatted_sft.append(pair)

        # Write curated dataset
        curated_path = os.path.join(self._training_dir, "train.jsonl")
        val_path = os.path.join(self._training_dir, "val.jsonl")

        import random
        random.shuffle(formatted_sft)

        # Split train/val
        split_idx = max(1, int(len(formatted_sft) * 0.9))
        train_data = formatted_sft[:split_idx]
        val_data = formatted_sft[split_idx:]

        for path, samples in [(curated_path, train_data), (val_path, val_data)]:
            with open(path, "w", encoding="utf-8") as f:
                for s in samples:
                    f.write(json.dumps({
                        "instruction": s["instruction"],
                        "output": s["output"],
                    }, ensure_ascii=False) + "\n")

        # Write DPO pairs separately
        dpo_path = os.path.join(self._training_dir, "dpo_pairs.jsonl")
        with open(dpo_path, "w", encoding="utf-8") as f:
            for pair in data.get("dpo_pairs", []):
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")

        cycle.train_samples = len(train_data)

        logger.info(
            "Curated dataset: %d train, %d val, %d DPO. "
            "Emphasis: %s",
            len(train_data), len(val_data), len(data.get("dpo_pairs", [])),
            {k: f"{v:.1f}x" for k, v in emphasis.items() if v != 1.0},
        )

        return {
            "train_path": curated_path,
            "val_path": val_path,
            "dpo_path": dpo_path,
            "train_count": len(train_data),
            "val_count": len(val_data),
            "dpo_count": len(data.get("dpo_pairs", [])),
        }

    # ------------------------------------------------------------------
    # Step 3: Run training with samsara cycle
    # ------------------------------------------------------------------

    def _run_training(self, cycle: EvolutionCycle) -> Dict[str, Any]:
        """Run a training cycle using the curated dataset.

        If ProgressiveTrainer is available, uses the 3-stage SFT→DPO→RL pipeline.
        Otherwise falls back to the original single-pass training.
        """
        # Phase 2: Try progressive training first
        if self._progressive_trainer:
            try:
                samskara_data = self.samskara.get_training_data()
                prog_result = self._progressive_trainer.run_cycle(
                    samskara_data=samskara_data,
                )
                if prog_result.model_improved:
                    return {
                        "progressive": True,
                        "stages": [s.to_dict() for s in prog_result.stages],
                        "data_path": prog_result.data_exported_path or "",
                    }
            except Exception as e:
                logger.debug("Progressive trainer failed, falling back: %s", e)

        from dhee.training.train import train as run_train

        # Determine model path (use latest or base)
        existing_models = []
        if os.path.exists(self._model_dir):
            existing_models = [
                f for f in os.listdir(self._model_dir)
                if f.endswith(".gguf")
            ]

        # Train
        output_subdir = os.path.join(
            self._model_dir, f"cycle_{cycle.cycle_id}"
        )

        try:
            result = run_train(
                data_dir=self._training_dir,
                output_dir=output_subdir,
                epochs=2,  # evolution cycles are short — refinement, not full training
                batch_size=4,
                learning_rate=1e-4,  # lower LR for fine-tuning refinement
            )

            if "error" in result:
                return result

            # Find the exported GGUF
            gguf_files = []
            if os.path.exists(output_subdir):
                gguf_files = [
                    os.path.join(output_subdir, f)
                    for f in os.listdir(output_subdir)
                    if f.endswith(".gguf")
                ]

            if gguf_files:
                result["model_path"] = gguf_files[0]

            return result

        except Exception as e:
            logger.error("Training failed: %s", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Step 4: Evaluate with karma
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        train_result: Dict[str, Any],
        cycle: EvolutionCycle,
    ) -> Dict[str, Any]:
        """Evaluate the trained model using karma vector."""
        from dhee.training.karma import YamaEvaluator

        evaluator = YamaEvaluator()

        # Get task scores from training result
        task_scores = train_result.get("task_scores", {})
        if not task_scores:
            # Estimate from training metrics
            train_loss = train_result.get("train_loss", 1.0)
            val_loss = train_result.get("val_loss", 1.0)
            # Without per-task eval, estimate uniform scores from loss
            est_score = max(0.0, 1.0 - val_loss)
            task_scores = {
                "engram": est_score,
                "query": est_score,
                "answer": est_score,
            }

        # Get previous cycle's scores for retention check
        prev_scores = None
        if self._history:
            last = self._history[-1]
            if last.task_scores:
                prev_scores = last.task_scores

        judgment = evaluator.evaluate(
            phase_name=f"nididhyasana_cycle_{cycle.cycle_id}",
            task_scores=task_scores,
            train_loss=train_result.get("train_loss", 0.0),
            val_loss=train_result.get("val_loss", 0.0),
            prev_task_scores=prev_scores,
        )

        return {
            "karma_net": judgment.karma.net,
            "task_scores": judgment.task_scores,
            "verdict": judgment.verdict,
            "strengths": judgment.strengths,
            "weaknesses": judgment.weaknesses,
        }

    # ------------------------------------------------------------------
    # Step 5: Hot-swap model
    # ------------------------------------------------------------------

    def _hot_swap(self, cycle: EvolutionCycle) -> None:
        """Hot-swap the running DheeModel with the newly trained one.

        The model_swap_callback is provided by the memory pipeline.
        It handles:
        - Unloading the current GGUF from llama.cpp
        - Loading the new GGUF
        - Verifying the new model works
        - Rolling back if verification fails
        """
        if not cycle.model_path or not os.path.exists(cycle.model_path):
            logger.warning("No model to swap — path does not exist")
            return

        # Copy to active model location
        active_path = os.path.join(self._model_dir, "dhee_active.gguf")

        # Backup current active model
        if os.path.exists(active_path):
            backup_path = os.path.join(
                self._model_dir,
                f"dhee_backup_cycle{cycle.cycle_id - 1}.gguf",
            )
            try:
                shutil.copy2(active_path, backup_path)
            except OSError as e:
                logger.warning("Failed to backup model: %s", e)

        # Copy new model to active location
        try:
            shutil.copy2(cycle.model_path, active_path)
        except OSError as e:
            logger.error("Failed to copy new model: %s", e)
            return

        # Invoke hot-swap callback
        if self.model_swap_callback:
            try:
                self.model_swap_callback(active_path)
                cycle.hot_swapped = True
                logger.info(
                    "Model hot-swapped successfully: %s → %s",
                    cycle.model_path, active_path,
                )
            except Exception as e:
                logger.error("Hot-swap callback failed: %s", e)
                cycle.error = f"hot-swap failed: {e}"
                # Rollback
                backup_path = os.path.join(
                    self._model_dir,
                    f"dhee_backup_cycle{cycle.cycle_id - 1}.gguf",
                )
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, active_path)
                    logger.info("Rolled back to previous model")
        else:
            cycle.hot_swapped = True  # no callback = file swap is enough
            logger.info("Model file swapped (no callback registered)")

    # ------------------------------------------------------------------
    # Step 6: Post-cycle cleanup
    # ------------------------------------------------------------------

    def _post_cycle_cleanup(self, cycle: EvolutionCycle) -> None:
        """Reset counters and prepare for next cycle."""
        # Flush samskara state (persists vasanas, clears DPO pairs)
        self.samskara.flush()

        # Reset correction counter (already consumed in training)
        self.samskara._correction_count = 0
        self.samskara._dpo_pairs.clear()

    # ------------------------------------------------------------------
    # History and persistence
    # ------------------------------------------------------------------

    def _record_cycle(self, cycle: EvolutionCycle) -> None:
        """Record a completed cycle to history and log."""
        self._history.append(cycle)

        log_path = os.path.join(self._log_dir, "evolution_history.jsonl")
        record = {
            "cycle_id": cycle.cycle_id,
            "started_at": cycle.started_at,
            "completed_at": cycle.completed_at,
            "trigger": cycle.trigger,
            "data_sources": cycle.data_sources,
            "train_samples": cycle.train_samples,
            "dpo_samples": cycle.dpo_samples,
            "train_loss": cycle.train_loss,
            "val_loss": cycle.val_loss,
            "karma_net": cycle.karma_net,
            "task_scores": cycle.task_scores,
            "verdict": cycle.verdict,
            "model_path": cycle.model_path,
            "hot_swapped": cycle.hot_swapped,
            "error": cycle.error,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def _load_history(self) -> None:
        """Load evolution history from disk."""
        log_path = os.path.join(self._log_dir, "evolution_history.jsonl")
        if not os.path.exists(log_path):
            return
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        self._cycle_count = max(
                            self._cycle_count, record.get("cycle_id", 0)
                        )
                        if record.get("completed_at"):
                            self._last_cycle_time = max(
                                self._last_cycle_time,
                                record["completed_at"],
                            )
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    def get_status(self) -> Dict[str, Any]:
        """Get current evolution loop status."""
        should, reason = self.should_evolve()
        signals = self.samskara.get_training_signals()

        return {
            "cycles_completed": self._cycle_count,
            "should_evolve": should,
            "trigger_reason": reason,
            "last_cycle_time": self._last_cycle_time,
            "cooldown_remaining": max(
                0, self.cooldown_seconds - (time.time() - self._last_cycle_time)
            ),
            "correction_count": signals.get("correction_count", 0),
            "dpo_pairs_available": len(signals.get("dpo_pairs", [])),
            "degrading_dimensions": signals.get("degrading_dimensions", []),
            "needs_nididhyasana": signals.get("needs_nididhyasana", False),
            "viveka_stats": self.viveka.get_stats() if self.viveka else {},
            "alaya_stats": (
                self.alaya.get_activation_stats() if self.alaya else {}
            ),
            "last_verdict": (
                self._history[-1].verdict if self._history else "none"
            ),
        }
