"""Cost guardrail logic for memory operations.

Extracted from memory/main.py — centralizes cost tracking, estimation,
and write-path guardrails that auto-disable expensive features when
cost budgets are exceeded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure estimation helpers (no state)
# ---------------------------------------------------------------------------

def estimate_token_count(value: Any) -> float:
    """Lightweight token estimate for guardrail telemetry."""
    if value is None:
        return 0.0
    if not isinstance(value, str):
        try:
            value = json.dumps(value, default=str)
        except Exception:
            value = str(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    # Rough English token estimate; good enough for trend guardrails.
    return float(max(1, math.ceil(len(text) / 4.0)))


def estimate_output_tokens(input_tokens: float) -> float:
    base = max(0.0, float(input_tokens or 0.0))
    return float(max(8, math.ceil(base * 0.3)))


def stable_hash_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CostTracker — stateful cost guardrail manager
# ---------------------------------------------------------------------------

class CostTracker:
    """Tracks cost counters and enforces write-path guardrails.

    Takes db, config references at init. Manages the reducer cache and
    cost counter recording.
    """

    def __init__(self, db, config):
        self.db = db
        self.config = config
        self._guardrail_auto_disabled = False
        self._reducer_cache: Dict[str, Dict[str, Any]] = {}

    def record_cost_counter(
        self,
        *,
        phase: str,
        user_id: Optional[str],
        llm_calls: float = 0.0,
        input_tokens: float = 0.0,
        output_tokens: float = 0.0,
        embed_calls: float = 0.0,
    ) -> None:
        cost_cfg = getattr(self.config, "cost_guardrail", None)
        if not cost_cfg or not cost_cfg.enable_cost_counters:
            return
        try:
            self.db.record_cost_counter(
                phase=phase,
                user_id=user_id,
                llm_calls=llm_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                embed_calls=embed_calls,
            )
            if str(phase) == "write":
                self.enforce_write_cost_guardrail(user_id=user_id)
        except Exception as e:
            logger.debug("Cost counter record failed: %s", e)

    def enforce_write_cost_guardrail(self, *, user_id: Optional[str]) -> None:
        cost_cfg = getattr(self.config, "cost_guardrail", None)
        orch_cfg = getattr(self.config, "orchestration", None)
        if not cost_cfg or not cost_cfg.strict_write_path_cap or not orch_cfg:
            return

        base_calls = float(getattr(cost_cfg, "baseline_write_llm_calls_per_memory", 0.0) or 0.0)
        base_tokens = float(getattr(cost_cfg, "baseline_write_tokens_per_memory", 0.0) or 0.0)
        if base_calls <= 0.0 and base_tokens <= 0.0:
            return

        summary = self.db.aggregate_cost_counters(phase="write", user_id=user_id)
        samples = max(1, int(summary.get("samples", 0) or 0))
        avg_calls = float(summary.get("llm_calls", 0.0) or 0.0) / float(samples)
        avg_tokens = (
            float(summary.get("input_tokens", 0.0) or 0.0)
            + float(summary.get("output_tokens", 0.0) or 0.0)
        ) / float(samples)

        violates_calls = base_calls > 0.0 and avg_calls > base_calls
        violates_tokens = base_tokens > 0.0 and avg_tokens > base_tokens
        if not (violates_calls or violates_tokens):
            return

        if getattr(cost_cfg, "auto_disable_on_violation", False):
            if not self._guardrail_auto_disabled:
                orch_cfg.enable_episodic_index = False
                orch_cfg.enable_hierarchical_retrieval = False
                orch_cfg.enable_orchestrated_search = False
                self._guardrail_auto_disabled = True
                logger.warning(
                    "Write-cost guardrail violated (avg_calls=%.4f avg_tokens=%.2f). "
                    "Auto-disabled orchestration features.",
                    avg_calls,
                    avg_tokens,
                )
        else:
            logger.warning(
                "Write-cost guardrail violated (avg_calls=%.4f avg_tokens=%.2f), "
                "strict mode active and auto-disable disabled.",
                avg_calls,
                avg_tokens,
            )

    def intent_coverage_threshold(self, intent_value: str, fallback: float) -> float:
        orch_cfg = getattr(self.config, "orchestration", None)
        thresholds = getattr(orch_cfg, "intent_coverage_thresholds", {}) or {}
        key = str(intent_value or "freeform").strip().lower()
        value = thresholds.get(key, fallback)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return max(0.0, min(1.0, float(fallback)))

    # -----------------------------------------------------------------------
    # Reducer cache (LRU-bounded, TTL-based)
    # -----------------------------------------------------------------------

    def build_reducer_cache_key(
        self,
        *,
        user_id: str,
        intent_value: str,
        query: str,
        results: List[Dict[str, Any]],
    ) -> str:
        evidence_fingerprint_parts: List[str] = []
        for row in results[:30]:
            mem_id = str(row.get("id") or "").strip()
            score = float(row.get("composite_score", row.get("score", 0.0)) or 0.0)
            evidence_fingerprint_parts.append(f"{mem_id}:{score:.4f}")
        evidence_fingerprint = "|".join(evidence_fingerprint_parts)
        base = "|".join(
            [
                str(user_id or ""),
                str(intent_value or ""),
                stable_hash_text(query),
                stable_hash_text(evidence_fingerprint),
            ]
        )
        return stable_hash_text(base)

    def get_reducer_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        orch_cfg = getattr(self.config, "orchestration", None)
        ttl_seconds = int(getattr(orch_cfg, "reducer_cache_ttl_seconds", 900) or 900)
        record = self._reducer_cache.get(cache_key)
        if not record:
            return None
        ts = float(record.get("ts", 0.0) or 0.0)
        if ts <= 0.0:
            return None
        if (time.time() - ts) > max(1, ttl_seconds):
            self._reducer_cache.pop(cache_key, None)
            return None
        return record

    def put_reducer_cache(
        self,
        *,
        cache_key: str,
        reduced_answer: Optional[str],
        facts: List[Dict[str, Any]],
    ) -> None:
        orch_cfg = getattr(self.config, "orchestration", None)
        max_entries = int(getattr(orch_cfg, "reducer_cache_max_entries", 2048) or 2048)
        self._reducer_cache[cache_key] = {
            "ts": time.time(),
            "reduced_answer": reduced_answer,
            "facts": list(facts or []),
        }
        while len(self._reducer_cache) > max(1, max_entries):
            oldest_key = next(iter(self._reducer_cache))
            self._reducer_cache.pop(oldest_key, None)
