"""Teacher Logging LLM — wraps any LLM to capture (prompt, response) pairs.

Used for distillation and self-improvement: capture teacher model outputs to
train DheeModel. Logs are annotated with quality signals from Viveka and
benchmark judge verdicts, enabling the Nididhyasana loop to curate training
data automatically.

Self-improvement data flow:
  1. Teacher logs captured during benchmark/live operation
  2. Judge verdicts or Viveka scores annotated retroactively
  3. Nididhyasana reads logs → filters by quality → produces training JSONL
  4. DheeModel fine-tuned on curated data → hot-swapped
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from dhee.llms.base import BaseLLM

logger = logging.getLogger(__name__)

_DEFAULT_LOG_DIR = os.path.join(os.path.expanduser("~"), ".dhee", "teacher_logs")


class TeacherLoggingLLM(BaseLLM):
    """Wraps any LLM to capture (prompt, response) pairs for distillation.

    Each entry gets a unique ID so it can be retroactively annotated with:
    - benchmark judge verdict (correct/incorrect)
    - Viveka quality score
    - gold answer (for SFT training data)
    """

    def __init__(
        self,
        inner: BaseLLM,
        log_dir: Optional[str] = None,
        config: Optional[dict] = None,
        run_id: Optional[str] = None,
    ):
        super().__init__(config)
        self.inner = inner
        self.log_dir = log_dir or _DEFAULT_LOG_DIR
        self.run_id = run_id or datetime_tag()
        os.makedirs(self.log_dir, exist_ok=True)
        self._log_file = os.path.join(self.log_dir, "teacher_log.jsonl")
        self._annotation_file = os.path.join(self.log_dir, "annotations.jsonl")
        self._count = 0
        self._entry_index: Dict[str, int] = {}  # entry_id → line number

    def generate(self, prompt: str) -> str:
        """Generate via inner LLM and log the pair."""
        t0 = time.monotonic()
        response = self.inner.generate(prompt)
        elapsed_ms = (time.monotonic() - t0) * 1000

        task_type = self._classify_task(prompt)
        entry_id = self._log(prompt, response, task_type, elapsed_ms)
        self._last_entry_id = entry_id

        return response

    @property
    def last_entry_id(self) -> Optional[str]:
        return getattr(self, "_last_entry_id", None)

    def annotate(
        self,
        entry_id: str,
        *,
        verdict: Optional[str] = None,
        gold_answer: Optional[str] = None,
        viveka_score: Optional[float] = None,
        question_type: Optional[str] = None,
        question_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Retroactively annotate a logged entry with quality signals.

        Called by the benchmark judge after evaluating system vs gold answer.
        """
        annotation = {
            "entry_id": entry_id,
            "timestamp": time.time(),
            "run_id": self.run_id,
        }
        if verdict is not None:
            annotation["verdict"] = verdict  # "correct" | "incorrect" | "partial"
        if gold_answer is not None:
            annotation["gold_answer"] = gold_answer
        if viveka_score is not None:
            annotation["viveka_score"] = viveka_score
        if question_type is not None:
            annotation["question_type"] = question_type
        if question_id is not None:
            annotation["question_id"] = question_id
        if metadata:
            annotation["metadata"] = metadata

        try:
            with open(self._annotation_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(annotation, default=str) + "\n")
        except Exception as e:
            logger.warning("Failed to write annotation: %s", e)

    def get_training_candidates(
        self,
        min_viveka_score: float = 0.0,
        verdict_filter: Optional[str] = "correct",
    ) -> List[Dict[str, Any]]:
        """Load logged entries that pass quality filters for training.

        Returns entries where:
        - verdict matches filter (default: only correct answers)
        - viveka_score >= min threshold
        """
        # Load annotations index
        annotations: Dict[str, Dict] = {}
        if os.path.exists(self._annotation_file):
            with open(self._annotation_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        ann = json.loads(line)
                        annotations[ann["entry_id"]] = ann

        # Load entries and filter
        candidates = []
        if not os.path.exists(self._log_file):
            return candidates

        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                entry = json.loads(line)
                eid = entry.get("entry_id", "")
                ann = annotations.get(eid, {})

                # Apply filters
                if verdict_filter and ann.get("verdict") != verdict_filter:
                    continue
                if ann.get("viveka_score", 1.0) < min_viveka_score:
                    continue

                entry["annotation"] = ann
                candidates.append(entry)

        return candidates

    def export_sft_data(self, output_path: str, task_types: Optional[List[str]] = None) -> int:
        """Export quality-filtered entries as SFT training JSONL.

        Format: {"instruction": prompt, "output": response_or_gold}
        Uses gold_answer when available (from benchmark), falls back to response.
        """
        candidates = self.get_training_candidates(verdict_filter="correct")
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for entry in candidates:
                if task_types and entry.get("task_type") not in task_types:
                    continue
                ann = entry.get("annotation", {})
                output = ann.get("gold_answer") or entry["response"]
                row = {
                    "instruction": entry["prompt"],
                    "output": output,
                    "task_type": entry.get("task_type", "other"),
                    "source": "teacher_log",
                    "run_id": entry.get("run_id", self.run_id),
                }
                f.write(json.dumps(row, default=str) + "\n")
                count += 1
        logger.info("Exported %d SFT examples to %s", count, output_path)
        return count

    def _classify_task(self, prompt: str) -> str:
        lower = prompt.lower()
        if "extract" in lower and ("fact" in lower or "engram" in lower):
            return "engram"
        if "classify" in lower and ("intent" in lower or "query" in lower):
            return "query"
        if "answer" in lower and "question" in lower:
            return "answer"
        if "decompose" in lower or "sub-question" in lower:
            return "decompose"
        if "context" in lower and ("era" in lower or "place" in lower or "time" in lower):
            return "context"
        if "scene" in lower and ("setting" in lower or "people" in lower):
            return "scene"
        if "echo" in lower or "paraphrase" in lower:
            return "echo"
        if "category" in lower or "categorize" in lower:
            return "category"
        if "entity" in lower or "extract" in lower:
            return "entity"
        return "other"

    def _log(self, prompt: str, response: str, task_type: str, elapsed_ms: float) -> str:
        entry_id = f"tl_{uuid.uuid4().hex[:12]}"
        entry = {
            "entry_id": entry_id,
            "run_id": self.run_id,
            "timestamp": time.time(),
            "task_type": task_type,
            "prompt": prompt,
            "response": response,
            "elapsed_ms": round(elapsed_ms, 1),
            "prompt_tokens": len(prompt.split()),
            "response_tokens": len(response.split()),
        }
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            self._count += 1
            if self._count % 100 == 0:
                logger.info(
                    "Teacher logger: %d pairs captured in %s",
                    self._count, self._log_file,
                )
        except Exception as e:
            logger.warning("Failed to log teacher pair: %s", e)
        return entry_id

    @property
    def log_count(self) -> int:
        return self._count

    @property
    def log_path(self) -> str:
        return self._log_file

    @property
    def annotation_path(self) -> str:
        return self._annotation_file


def datetime_tag() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")
