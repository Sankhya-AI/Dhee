"""BuddhiMini — small trainable model for self-evolving cognition.

NOT a separate model from DheeModel. This is DheeModel with 3 new task
heads + a trace-driven data pipeline that produces better training data.

The self-evolution loop:
  1. Agent uses Dhee (remember/recall/context/checkpoint)
  2. Samskara collects 12 signal types per operation
  3. TraceSegmenter splits trajectories into [REASON]/[ACT]/[MEMORY_OP]
  4. When signals reach critical mass → Nididhyasana triggers
  5. ProgressiveTrainer runs: SFT → DPO → RL
  6. DheeModel updates weights (LoRA merge or GGUF export)
  7. Hot-swapped without restart

Research basis:
  - Structured Agent Distillation (arXiv:2505.13820): span-specific losses
  - AgeMem (arXiv:2601.01885): memory ops as RL-optimized tool calls
  - EvolveR (arXiv:2510.16079): offline distillation → online retrieval

New task heads (added to DheeModel's existing 6):
  [MEMORY_OP]      — predict optimal memory operation for context
  [HEURISTIC]      — generate abstract heuristic from trajectory
  [RETRIEVAL_JUDGE] — predict whether retrieval results are sufficient
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dhee.mini.trace_segmenter import TraceSegmenter, TrainingSpan, SpanType

logger = logging.getLogger(__name__)

# Training thresholds
_MIN_SFT_SAMPLES = 50       # minimum samples to trigger SFT
_MIN_DPO_PAIRS = 20         # minimum pairs to trigger DPO
_ACCUMULATION_WINDOW = 3600  # seconds between training checks


@dataclass
class TrainingBuffer:
    """Accumulates training data between training cycles."""
    sft_samples: List[Dict[str, str]] = field(default_factory=list)
    dpo_pairs: List[Dict[str, Any]] = field(default_factory=list)
    trajectories_ingested: int = 0
    contrastive_pairs_ingested: int = 0
    last_train_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sft_samples": len(self.sft_samples),
            "dpo_pairs": len(self.dpo_pairs),
            "trajectories_ingested": self.trajectories_ingested,
            "contrastive_pairs_ingested": self.contrastive_pairs_ingested,
            "last_train_time": self.last_train_time,
        }


class BuddhiMini:
    """Small trainable model for self-evolving cognition.

    Wraps the existing DheeModel (Qwen3.5-2B) and adds:
    1. Trace ingestion pipeline (trajectories → training spans)
    2. Training data accumulation with thresholds
    3. Progressive training trigger (SFT → DPO → RL)
    4. 3 new inference task heads

    The model trains itself from the agent's own interaction traces.
    No external training data needed. Pure self-evolution.

    Args:
        data_dir: Directory for training data and checkpoints
        model_size: Not used yet — reserved for future model variants
        device: Device for inference (auto-detected if None)
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        model_size: str = "2B",
        device: Optional[str] = None,
    ):
        self._data_dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "mini"
        )
        os.makedirs(self._data_dir, exist_ok=True)

        self._segmenter = TraceSegmenter()
        self._buffer = TrainingBuffer()
        self._model = None  # lazy-loaded DheeLLM
        self._device = device

        # Load persisted buffer if exists
        self._load_buffer()

    # ------------------------------------------------------------------
    # Trace ingestion
    # ------------------------------------------------------------------

    def ingest_trajectory(self, trajectory) -> Dict[str, Any]:
        """Ingest a trajectory and segment it into training spans.

        Called automatically by DheePlugin.end_trajectory() or manually.

        Returns:
            {"spans": int, "sft_added": int, "dpo_ready": bool}
        """
        spans = self._segmenter.segment(trajectory)
        if not spans:
            return {"spans": 0, "sft_added": 0, "dpo_ready": False}

        # Add successful spans to SFT buffer
        sft_examples = self._segmenter.format_for_sft(spans)
        self._buffer.sft_samples.extend(sft_examples)
        self._buffer.trajectories_ingested += 1

        # Store spans for DPO pairing later
        self._save_spans(spans)
        self._save_buffer()

        return {
            "spans": len(spans),
            "sft_added": len(sft_examples),
            "dpo_ready": len(self._buffer.dpo_pairs) >= _MIN_DPO_PAIRS,
        }

    def ingest_contrastive_pair(
        self,
        task_description: str,
        success_approach: str,
        failure_approach: str,
        task_type: str = "general",
    ) -> None:
        """Ingest a contrastive pair for DPO training.

        Called when checkpoint() receives both what_worked and what_failed.
        """
        self._buffer.dpo_pairs.append({
            "prompt": f"[TASK] {task_description}\n[TYPE] {task_type}",
            "chosen": success_approach,
            "rejected": failure_approach,
            "span_type": "reflect",
        })
        self._buffer.contrastive_pairs_ingested += 1
        self._save_buffer()

    # ------------------------------------------------------------------
    # Training control
    # ------------------------------------------------------------------

    def should_train(self) -> tuple:
        """Check if enough data has accumulated for a training cycle.

        Returns:
            (should_train: bool, reason: str)
        """
        now = time.time()
        if now - self._buffer.last_train_time < _ACCUMULATION_WINDOW:
            return False, "Too soon since last training cycle"

        sft_ready = len(self._buffer.sft_samples) >= _MIN_SFT_SAMPLES
        dpo_ready = len(self._buffer.dpo_pairs) >= _MIN_DPO_PAIRS

        if sft_ready and dpo_ready:
            return True, f"Ready: {len(self._buffer.sft_samples)} SFT + {len(self._buffer.dpo_pairs)} DPO"
        if sft_ready:
            return True, f"SFT ready: {len(self._buffer.sft_samples)} samples"
        if dpo_ready:
            return True, f"DPO ready: {len(self._buffer.dpo_pairs)} pairs"

        return False, (
            f"Accumulating: {len(self._buffer.sft_samples)}/{_MIN_SFT_SAMPLES} SFT, "
            f"{len(self._buffer.dpo_pairs)}/{_MIN_DPO_PAIRS} DPO"
        )

    def train_cycle(self, stage: str = "auto") -> Dict[str, Any]:
        """Run one training cycle.

        Delegates to ProgressiveTrainer or Nididhyasana depending on
        what's available. Returns training results.

        Args:
            stage: "sft", "dpo", "progressive", or "auto"
        """
        result: Dict[str, Any] = {"stage": stage, "status": "skipped"}

        try:
            # Try to use Nididhyasana (existing auto-evolution loop)
            from dheeModel.training.nididhyasana import NididhyasanaLoop
            loop = NididhyasanaLoop(data_dir=self._data_dir)

            # Export training data in Nididhyasana format
            training_data = self._export_training_data()
            if not training_data:
                result["status"] = "no_data"
                return result

            # Run cycle
            cycle_result = loop.run_cycle(
                sft_data=training_data.get("sft", []),
                dpo_data=training_data.get("dpo", []),
            )
            result["status"] = "completed"
            result["cycle"] = cycle_result
        except ImportError:
            logger.debug("Nididhyasana not available — storing data for manual training")
            self._save_training_export()
            result["status"] = "data_saved"
            result["path"] = os.path.join(self._data_dir, "training_export.jsonl")
        except Exception as e:
            logger.debug("Training cycle failed: %s", e)
            result["status"] = "error"
            result["error"] = str(e)

        # Update buffer
        self._buffer.last_train_time = time.time()
        self._buffer.sft_samples = []  # Clear used samples
        self._buffer.dpo_pairs = []
        self._save_buffer()

        return result

    # ------------------------------------------------------------------
    # Inference (edge-optimized task heads)
    # ------------------------------------------------------------------

    def classify_memory_op(self, context: str) -> str:
        """Predict optimal memory operation for current context.

        Task head: [MEMORY_OP]
        Returns: "store" | "retrieve" | "update" | "summarize" | "discard" | "none"
        """
        model = self._get_model()
        if model is None:
            return self._heuristic_classify_memory_op(context)

        try:
            response = model.generate_with_task(
                task="MEMORY_OP",
                prompt=context[:1000],
            )
            op = response.strip().lower()
            valid_ops = {"store", "retrieve", "update", "summarize", "discard", "none"}
            return op if op in valid_ops else "none"
        except Exception:
            return self._heuristic_classify_memory_op(context)

    def generate_heuristic(self, trajectory_summary: str) -> str:
        """Generate an abstract heuristic from a trajectory summary.

        Task head: [HEURISTIC]
        Returns: A transferable reasoning pattern as natural language.
        """
        model = self._get_model()
        if model is None:
            return f"From experience: {trajectory_summary[:200]}"

        try:
            return model.generate_with_task(
                task="HEURISTIC",
                prompt=trajectory_summary[:2000],
            )
        except Exception:
            return f"From experience: {trajectory_summary[:200]}"

    def predict_retrieval_quality(
        self, query: str, results: List[Dict[str, Any]],
    ) -> float:
        """Predict whether retrieval results are sufficient.

        Task head: [RETRIEVAL_JUDGE]
        Returns: 0.0 (insufficient) to 1.0 (fully sufficient)
        """
        model = self._get_model()
        if model is None:
            return self._heuristic_retrieval_quality(query, results)

        try:
            results_text = "\n".join(
                f"- {r.get('memory', '')[:100]} (score={r.get('score', 0):.2f})"
                for r in results[:5]
            )
            prompt = f"Query: {query}\nResults:\n{results_text}"
            response = model.generate_with_task(
                task="RETRIEVAL_JUDGE",
                prompt=prompt,
            )
            return max(0.0, min(1.0, float(response.strip())))
        except Exception:
            return self._heuristic_retrieval_quality(query, results)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get BuddhiMini status."""
        should, reason = self.should_train()
        return {
            "buffer": self._buffer.to_dict(),
            "should_train": should,
            "train_reason": reason,
            "model_loaded": self._model is not None,
            "data_dir": self._data_dir,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_model(self):
        """Lazy-load the DheeModel."""
        if self._model is not None:
            return self._model
        try:
            from dhee.llms.dhee import DheeLLM
            self._model = DheeLLM(config={"device": self._device} if self._device else {})
            return self._model
        except Exception:
            return None

    def _heuristic_classify_memory_op(self, context: str) -> str:
        """Rule-based fallback for memory op classification."""
        cl = context.lower()
        if any(w in cl for w in ["remember", "store", "save", "note"]):
            return "store"
        if any(w in cl for w in ["recall", "search", "find", "what did"]):
            return "retrieve"
        if any(w in cl for w in ["update", "change", "correct"]):
            return "update"
        if any(w in cl for w in ["forget", "delete", "remove"]):
            return "discard"
        if any(w in cl for w in ["summarize", "consolidate", "compress"]):
            return "summarize"
        return "none"

    def _heuristic_retrieval_quality(
        self, query: str, results: List[Dict[str, Any]],
    ) -> float:
        """Rule-based fallback for retrieval quality prediction."""
        if not results:
            return 0.0
        top_score = results[0].get("score", 0) if results else 0
        count = len(results)
        # Simple heuristic: score * coverage
        coverage = min(count / 3.0, 1.0)
        return round(min(top_score * coverage, 1.0), 3)

    def _export_training_data(self) -> Optional[Dict[str, List]]:
        """Export accumulated buffer as training data."""
        if not self._buffer.sft_samples and not self._buffer.dpo_pairs:
            return None
        return {
            "sft": list(self._buffer.sft_samples),
            "dpo": list(self._buffer.dpo_pairs),
        }

    def _save_training_export(self) -> None:
        """Save training data to JSONL for manual training."""
        path = os.path.join(self._data_dir, "training_export.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                for sample in self._buffer.sft_samples:
                    f.write(json.dumps({"type": "sft", **sample}) + "\n")
                for pair in self._buffer.dpo_pairs:
                    f.write(json.dumps({"type": "dpo", **pair}) + "\n")
        except OSError as e:
            logger.debug("Failed to save training export: %s", e)

    def _save_spans(self, spans: List[TrainingSpan]) -> None:
        """Persist spans for later DPO pairing."""
        path = os.path.join(self._data_dir, "spans.jsonl")
        try:
            with open(path, "a", encoding="utf-8") as f:
                for span in spans:
                    f.write(json.dumps(span.to_dict()) + "\n")
        except OSError as e:
            logger.debug("Failed to save spans: %s", e)

    def _save_buffer(self) -> None:
        """Persist buffer metadata."""
        path = os.path.join(self._data_dir, "buffer.json")
        try:
            data = {
                "sft_count": len(self._buffer.sft_samples),
                "dpo_count": len(self._buffer.dpo_pairs),
                "trajectories_ingested": self._buffer.trajectories_ingested,
                "contrastive_pairs_ingested": self._buffer.contrastive_pairs_ingested,
                "last_train_time": self._buffer.last_train_time,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _load_buffer(self) -> None:
        """Load persisted buffer metadata."""
        path = os.path.join(self._data_dir, "buffer.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._buffer.trajectories_ingested = data.get("trajectories_ingested", 0)
            self._buffer.contrastive_pairs_ingested = data.get("contrastive_pairs_ingested", 0)
            self._buffer.last_train_time = data.get("last_train_time", 0.0)
        except (OSError, json.JSONDecodeError):
            pass
