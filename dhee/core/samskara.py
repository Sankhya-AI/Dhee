"""संस्कार (Samskara) — Impression collector for self-evolving memory.

Every memory operation leaves a samskara — a structural impression on the
system itself. Samskaras are NOT memories. They are signals about the
quality and effectiveness of the system's own operations.

Accumulated samskaras form vasanas (tendencies). When vasanas reach
critical mass (prakrity-apurat), they trigger nididhyasana —
deep integration that changes the model's weights.

Brihadaranyaka Upanishad 4.4.5:
  "sa yathakari yathachari tatha bhavati"
  "as one acts, as one behaves, so one becomes"

The system BECOMES what it repeatedly does.

Three types of samskaras:
  1. Dhi (acquisition) — how well did we extract/understand?
  2. Dhriti (retention) — how well is the store organized?
  3. Smriti (recall) — how well did we retrieve and answer?
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SamskaraType(str, Enum):
    """The three phases of memory, each generating its own impressions."""

    # Dhi — acquisition impressions
    EXTRACTION = "extraction"         # fact extraction quality signal
    ENRICHMENT = "enrichment"         # echo/entity/context enrichment quality
    CONFLICT = "conflict"             # contradiction detected with existing memory

    # Dhriti — retention impressions
    STORAGE = "storage"               # memory stored successfully
    DEDUP = "dedup"                   # duplicate detected and merged
    DECAY = "decay"                   # memory strength decayed

    # Smriti — recall impressions
    RETRIEVAL_HIT = "retrieval_hit"   # memory retrieved and used in answer
    RETRIEVAL_MISS = "retrieval_miss" # relevant query but memory not retrieved
    ANSWER_ACCEPTED = "answer_accepted"   # user accepted the answer
    ANSWER_CORRECTED = "answer_corrected" # user corrected the answer
    GROUNDING_SUCCESS = "grounding_success"   # cognition engine grounded a sub-question
    GROUNDING_FAILURE = "grounding_failure"   # cognition engine failed to ground


class SamskaraValence(str, Enum):
    """Klishta vs Aklishta — Yoga Sutra 1.5.

    Every impression is either afflicted (degrading) or non-afflicted (supporting).
    """

    KLISHTA = "klishta"       # afflicted — signals a problem
    AKLISHTA = "aklishta"     # non-afflicted — signals correct operation


@dataclass
class Samskara:
    """A single impression left by a memory operation.

    Not a memory. A signal about the system's own quality.
    """

    type: SamskaraType
    valence: SamskaraValence
    timestamp: float = field(default_factory=time.time)

    # What operation produced this impression
    memory_id: str = ""           # memory involved (if any)
    query: str = ""               # query involved (if any)
    user_id: str = "default"

    # Quality signal
    confidence: float = 1.0       # how confident is this signal (0-1)
    detail: str = ""              # human-readable detail

    # For training data generation
    input_text: str = ""          # the input that was processed
    output_text: str = ""         # what the system produced
    corrected_text: str = ""      # what it should have produced (for corrections)

    def is_positive(self) -> bool:
        return self.valence == SamskaraValence.AKLISHTA

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["valence"] = self.valence.value
        return d


@dataclass
class Vasana:
    """वासना — Accumulated tendency from repeated samskaras.

    Like perfume lingering on a garment. Aggregated signal
    about a specific dimension of system quality.
    """

    dimension: str                # what aspect (e.g., "fact_extraction", "temporal_reasoning")
    strength: float = 0.0        # accumulated signal (-1 to 1). Negative = degrading.
    count: int = 0               # how many samskaras contributed
    last_updated: float = field(default_factory=time.time)

    def absorb(self, samskara: Samskara, learning_rate: float = 0.1) -> None:
        """Absorb a new samskara into this vasana using EMA."""
        signal = samskara.confidence if samskara.is_positive() else -samskara.confidence
        self.strength = (1 - learning_rate) * self.strength + learning_rate * signal
        self.count += 1
        self.last_updated = time.time()

    @property
    def is_degrading(self) -> bool:
        return self.strength < -0.3 and self.count >= 10

    @property
    def is_thriving(self) -> bool:
        return self.strength > 0.3 and self.count >= 10


class SamskaraCollector:
    """Collects samskaras from all memory operations.

    Accumulates them into vasanas. When vasanas cross thresholds,
    signals the need for nididhyasana (model weight update).

    The collector is the Chitragupta of Dhee — the divine accountant
    who records every action for judgment.
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        nididhyasana_threshold: int = 100,      # corrections before auto-retrain
        prakrti_apurat_threshold: float = -0.3,  # vasana floor before alarm
    ):
        self.log_dir = log_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "samskaras"
        )
        os.makedirs(self.log_dir, exist_ok=True)

        self.nididhyasana_threshold = nididhyasana_threshold
        self.prakrti_apurat_threshold = prakrti_apurat_threshold

        # Vasana accumulators — one per quality dimension
        self.vasanas: Dict[str, Vasana] = {
            "fact_extraction": Vasana(dimension="fact_extraction"),
            "context_anchoring": Vasana(dimension="context_anchoring"),
            "temporal_reasoning": Vasana(dimension="temporal_reasoning"),
            "entity_linking": Vasana(dimension="entity_linking"),
            "retrieval_precision": Vasana(dimension="retrieval_precision"),
            "retrieval_recall": Vasana(dimension="retrieval_recall"),
            "answer_quality": Vasana(dimension="answer_quality"),
            "dedup_quality": Vasana(dimension="dedup_quality"),
        }

        # Counters
        self._total_samskaras = 0
        self._correction_count = 0
        self._session_samskaras: List[Samskara] = []

        # DPO training pairs (from corrections)
        self._dpo_pairs: List[Dict[str, str]] = []

        # Load existing vasana state
        self._load_state()

    # ------------------------------------------------------------------
    # Dhi (acquisition) samskaras
    # ------------------------------------------------------------------

    def on_extraction(
        self,
        memory_id: str,
        input_text: str,
        extracted_output: str,
        fact_count: int = 0,
        user_id: str = "default",
    ) -> None:
        """Record impression from a memory extraction operation."""
        # Positive signal: extracted facts successfully
        valence = (
            SamskaraValence.AKLISHTA if fact_count > 0
            else SamskaraValence.KLISHTA
        )
        samskara = Samskara(
            type=SamskaraType.EXTRACTION,
            valence=valence,
            memory_id=memory_id,
            user_id=user_id,
            confidence=min(1.0, fact_count / 3.0),  # more facts = higher confidence
            detail=f"extracted {fact_count} facts",
            input_text=input_text[:500],
            output_text=extracted_output[:500],
        )
        self._record(samskara, "fact_extraction")

    def on_conflict_detected(
        self,
        memory_id: str,
        old_value: str,
        new_value: str,
        resolved_to: str,
        user_id: str = "default",
    ) -> None:
        """Record impression when a contradiction is found and resolved.

        Conflicts are POSITIVE signals for self-evolution:
        the system detected its own error and corrected it.
        """
        samskara = Samskara(
            type=SamskaraType.CONFLICT,
            valence=SamskaraValence.AKLISHTA,  # detection is good
            memory_id=memory_id,
            user_id=user_id,
            confidence=0.9,
            detail=f"conflict: '{old_value}' vs '{new_value}' → '{resolved_to}'",
            output_text=old_value,
            corrected_text=resolved_to,
        )
        self._record(samskara, "fact_extraction")

        # Store as DPO pair for training
        if old_value != resolved_to:
            self._dpo_pairs.append({
                "input": samskara.input_text,
                "preferred": resolved_to,
                "rejected": old_value,
                "signal": "conflict_resolution",
            })

    # ------------------------------------------------------------------
    # Smriti (recall) samskaras
    # ------------------------------------------------------------------

    def on_retrieval(
        self,
        query: str,
        retrieved_ids: List[str],
        was_useful: bool = True,
        user_id: str = "default",
    ) -> None:
        """Record impression from a search/retrieval operation."""
        if was_useful and retrieved_ids:
            samskara = Samskara(
                type=SamskaraType.RETRIEVAL_HIT,
                valence=SamskaraValence.AKLISHTA,
                query=query[:200],
                user_id=user_id,
                confidence=0.8,
                detail=f"retrieved {len(retrieved_ids)} useful results",
            )
            self._record(samskara, "retrieval_precision")
        elif not was_useful or not retrieved_ids:
            samskara = Samskara(
                type=SamskaraType.RETRIEVAL_MISS,
                valence=SamskaraValence.KLISHTA,
                query=query[:200],
                user_id=user_id,
                confidence=0.6,
                detail="retrieval miss or irrelevant results",
            )
            self._record(samskara, "retrieval_recall")

    def on_answer_accepted(
        self,
        query: str,
        answer: str,
        memory_ids: List[str],
        user_id: str = "default",
    ) -> None:
        """Record when user accepts an answer (implicit positive signal)."""
        samskara = Samskara(
            type=SamskaraType.ANSWER_ACCEPTED,
            valence=SamskaraValence.AKLISHTA,
            query=query[:200],
            user_id=user_id,
            confidence=0.7,
            detail=f"answer accepted, grounded in {len(memory_ids)} memories",
            input_text=query[:500],
            output_text=answer[:500],
        )
        self._record(samskara, "answer_quality")

    def on_answer_corrected(
        self,
        query: str,
        wrong_answer: str,
        correct_answer: str,
        memory_ids: List[str],
        user_id: str = "default",
    ) -> None:
        """Record when user corrects an answer (explicit negative signal).

        This is the most valuable samskara — direct supervision.
        Creates a DPO training pair immediately.
        """
        samskara = Samskara(
            type=SamskaraType.ANSWER_CORRECTED,
            valence=SamskaraValence.KLISHTA,
            query=query[:200],
            user_id=user_id,
            confidence=1.0,  # user corrections are highest confidence
            detail=f"corrected: '{wrong_answer[:100]}' → '{correct_answer[:100]}'",
            input_text=query[:500],
            output_text=wrong_answer[:500],
            corrected_text=correct_answer[:500],
        )
        self._record(samskara, "answer_quality")
        self._correction_count += 1

        # DPO pair: direct preference signal
        self._dpo_pairs.append({
            "input": query,
            "preferred": correct_answer,
            "rejected": wrong_answer,
            "signal": "user_correction",
        })

        # Check if nididhyasana threshold reached
        if self._correction_count >= self.nididhyasana_threshold:
            logger.warning(
                "Nididhyasana threshold reached: %d corrections accumulated. "
                "Model retraining recommended.",
                self._correction_count,
            )

    def on_grounding(
        self,
        sub_question: str,
        grounded: bool,
        source: str = "",
        user_id: str = "default",
    ) -> None:
        """Record CognitionEngine grounding result."""
        samskara = Samskara(
            type=(
                SamskaraType.GROUNDING_SUCCESS if grounded
                else SamskaraType.GROUNDING_FAILURE
            ),
            valence=(
                SamskaraValence.AKLISHTA if grounded
                else SamskaraValence.KLISHTA
            ),
            query=sub_question[:200],
            user_id=user_id,
            confidence=0.8,
            detail=f"grounding {'succeeded' if grounded else 'failed'} via {source}",
        )
        dimension = (
            "retrieval_precision" if grounded
            else "retrieval_recall"
        )
        self._record(samskara, dimension)

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _record(self, samskara: Samskara, vasana_dimension: str) -> None:
        """Record a samskara and update the corresponding vasana."""
        self._session_samskaras.append(samskara)
        self._total_samskaras += 1

        # Update vasana
        if vasana_dimension in self.vasanas:
            self.vasanas[vasana_dimension].absorb(samskara)

        # Persist to log
        self._append_log(samskara)

        # Check for degrading vasanas
        vasana = self.vasanas.get(vasana_dimension)
        if vasana and vasana.is_degrading:
            logger.warning(
                "Vasana '%s' is degrading: strength=%.3f (count=%d). "
                "System quality declining in this dimension.",
                vasana_dimension,
                vasana.strength,
                vasana.count,
            )

    def _append_log(self, samskara: Samskara) -> None:
        """Append samskara to JSONL log for training data generation."""
        log_path = os.path.join(self.log_dir, "samskaras.jsonl")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(samskara.to_dict(), ensure_ascii=False) + "\n")
        except OSError:
            pass  # non-critical

    def needs_nididhyasana(self) -> bool:
        """Check if accumulated signals warrant a training cycle.

        Yoga Sutra 4.2: "jaty-antara-parinamah prakrity-apurat"
        Transformation happens when natural potential overflows.
        """
        # Trigger 1: enough corrections accumulated
        if self._correction_count >= self.nididhyasana_threshold:
            return True

        # Trigger 2: any vasana severely degrading
        for vasana in self.vasanas.values():
            if (
                vasana.strength < self.prakrti_apurat_threshold
                and vasana.count >= 20
            ):
                return True

        return False

    def get_training_signals(self) -> Dict[str, Any]:
        """Export accumulated signals for training pipeline.

        Returns:
            - dpo_pairs: Direct preference pairs from corrections
            - vasana_report: Current vasana strengths
            - degrading_dimensions: Which dimensions need attention
            - total_samskaras: Total impressions recorded
        """
        return {
            "dpo_pairs": list(self._dpo_pairs),
            "vasana_report": {
                name: {
                    "strength": v.strength,
                    "count": v.count,
                    "status": (
                        "degrading" if v.is_degrading
                        else "thriving" if v.is_thriving
                        else "neutral"
                    ),
                }
                for name, v in self.vasanas.items()
            },
            "degrading_dimensions": [
                name for name, v in self.vasanas.items()
                if v.is_degrading
            ],
            "total_samskaras": self._total_samskaras,
            "correction_count": self._correction_count,
            "needs_nididhyasana": self.needs_nididhyasana(),
        }

    def _save_state(self) -> None:
        """Persist vasana state to disk."""
        state_path = os.path.join(self.log_dir, "vasana_state.json")
        state = {
            "vasanas": {
                name: {
                    "dimension": v.dimension,
                    "strength": v.strength,
                    "count": v.count,
                    "last_updated": v.last_updated,
                }
                for name, v in self.vasanas.items()
            },
            "total_samskaras": self._total_samskaras,
            "correction_count": self._correction_count,
        }
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass

    def _load_state(self) -> None:
        """Restore vasana state from disk."""
        state_path = os.path.join(self.log_dir, "vasana_state.json")
        if not os.path.exists(state_path):
            return
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
            for name, vdata in state.get("vasanas", {}).items():
                if name in self.vasanas:
                    self.vasanas[name].strength = vdata.get("strength", 0.0)
                    self.vasanas[name].count = vdata.get("count", 0)
                    self.vasanas[name].last_updated = vdata.get(
                        "last_updated", time.time()
                    )
            self._total_samskaras = state.get("total_samskaras", 0)
            self._correction_count = state.get("correction_count", 0)
        except (OSError, json.JSONDecodeError):
            pass

    def get_training_data(self) -> Dict[str, Any]:
        """Export accumulated data formatted for BuddhiMini training pipeline.

        Returns SFT examples from session samskaras and DPO pairs from corrections.
        Called by BuddhiMini.train_cycle() to feed the progressive trainer.
        """
        sft_samples = []
        for s in self._session_samskaras:
            if s.input_text and s.output_text:
                sample = {
                    "input": f"[{s.type.value.upper()}] {s.input_text}",
                    "output": s.output_text,
                    "type": s.type.value,
                    "valence": s.valence.value,
                }
                if s.corrected_text:
                    sample["corrected"] = s.corrected_text
                sft_samples.append(sample)

        return {
            "sft_samples": sft_samples,
            "dpo_pairs": list(self._dpo_pairs),
            "vasana_report": {
                name: {"strength": v.strength, "count": v.count}
                for name, v in self.vasanas.items()
            },
            "degrading_dimensions": [
                name for name, v in self.vasanas.items()
                if v.is_degrading
            ],
            "total_samskaras": self._total_samskaras,
        }

    def export_replay_corpus(
        self,
        output_dir: str,
        *,
        shard_name: Optional[str] = None,
        include_accepted: bool = True,
        include_corrected: bool = True,
        max_records: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Derive a replay-gate corpus from the durable samskara log.

        Reads ``<log_dir>/samskaras.jsonl`` and emits one JSONL line per
        usable record into ``<output_dir>/<shard_name>``. Only
        ANSWER_ACCEPTED (prompt+output is known-good) and
        ANSWER_CORRECTED (prompt → corrected_text is ground truth)
        records produce corpus entries.

        Output shape matches what ``dhee.mini.replay_gate.ReplayGate``
        consumes::

            {"prompt": str, "expected": str, "metadata": {...}}

        Returns a structured summary; never raises on missing log or
        empty corpus (callers get ``record_count=0`` and can act on it).
        """
        summary: Dict[str, Any] = {
            "path": None,
            "record_count": 0,
            "accepted_count": 0,
            "corrected_count": 0,
            "skipped_count": 0,
            "source_log": os.path.join(self.log_dir, "samskaras.jsonl"),
        }

        os.makedirs(output_dir, exist_ok=True)
        shard = shard_name or f"replay-{int(time.time())}.jsonl"
        out_path = os.path.join(output_dir, shard)

        log_path = summary["source_log"]
        if not os.path.exists(log_path):
            return summary

        records: List[Dict[str, Any]] = []
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        summary["skipped_count"] += 1
                        continue

                    rtype = rec.get("type")
                    prompt = str(rec.get("input_text") or rec.get("query") or "")
                    if not prompt:
                        summary["skipped_count"] += 1
                        continue

                    expected: Optional[str] = None
                    source_kind: Optional[str] = None
                    if rtype == SamskaraType.ANSWER_CORRECTED.value \
                            and include_corrected:
                        expected = str(rec.get("corrected_text") or "")
                        source_kind = "corrected"
                    elif rtype == SamskaraType.ANSWER_ACCEPTED.value \
                            and include_accepted:
                        expected = str(rec.get("output_text") or "")
                        source_kind = "accepted"

                    if not expected or not source_kind:
                        continue

                    records.append({
                        "prompt": prompt,
                        "expected": expected,
                        "metadata": {
                            "source": source_kind,
                            "timestamp": rec.get("timestamp"),
                            "user_id": rec.get("user_id", "default"),
                            "valence": rec.get("valence"),
                        },
                    })

                    if source_kind == "corrected":
                        summary["corrected_count"] += 1
                    else:
                        summary["accepted_count"] += 1
        except OSError as exc:
            logger.debug("Failed to read samskara log: %s", exc)
            return summary

        if max_records is not None and len(records) > max_records:
            # Keep the most recent N records — replay should reflect
            # current behaviour, not ancient history.
            records = records[-int(max_records):]

        try:
            with open(out_path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("Failed to write replay corpus: %s", exc)
            return summary

        summary["path"] = out_path
        summary["record_count"] = len(records)
        return summary

    def flush(self) -> None:
        """Persist current state. Call periodically or on shutdown."""
        self._save_state()

        # Also flush DPO pairs if any
        if self._dpo_pairs:
            dpo_path = os.path.join(self.log_dir, "dpo_pairs.jsonl")
            try:
                with open(dpo_path, "a", encoding="utf-8") as f:
                    for pair in self._dpo_pairs:
                        f.write(json.dumps(pair, ensure_ascii=False) + "\n")
                self._dpo_pairs.clear()
            except OSError:
                pass
