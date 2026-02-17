"""Metamemory — Feeling of Knowing, knowledge gaps, and calibration tracking.

All data is stored as Engram memories with `memory_type="metamemory_gap"` or
`memory_type="metamemory_calibration"`. No new DB tables.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engram_metamemory.config import MetamemoryConfig
from engram_metamemory.confidence import compute_confidence

logger = logging.getLogger(__name__)


class Metamemory:
    """Metamemory engine — the Oracle.

    Provides:
    - Feeling of Knowing (FOK): "Do I know about X?"
    - Knowledge gap registry: "What don't I know?"
    - Retrieval calibration: "How accurate am I?"
    - Confidence queries for individual memories.
    """

    def __init__(
        self,
        memory: Any,
        user_id: str = "default",
        config: Optional[MetamemoryConfig] = None,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.config = config or MetamemoryConfig()

    def feeling_of_knowing(
        self,
        query: str,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """Assess whether the system knows about a topic.

        Returns a FOK verdict: "confident", "uncertain", or "unknown",
        along with the confidence score and supporting memories.
        """
        results = self.memory.search(
            query=query,
            user_id=self.user_id,
            limit=limit,
        )
        memories = results.get("results", [])

        if not memories:
            return {
                "verdict": "unknown",
                "score": 0.0,
                "query": query,
                "supporting_count": 0,
                "top_memories": [],
            }

        # Compute aggregate confidence from top results
        confidences = []
        top_memories = []
        for mem in memories:
            metadata = mem.get("metadata", {}) or {}
            conf = metadata.get("mm_confidence")
            if conf is None:
                conf = compute_confidence(
                    metadata=metadata,
                    strength=mem.get("strength", 1.0),
                    access_count=mem.get("access_count", 0),
                    created_at=mem.get("created_at"),
                    config=self.config,
                )
            confidences.append(float(conf))
            top_memories.append({
                "id": mem.get("id"),
                "memory": mem.get("memory", "")[:200],
                "confidence": float(conf),
                "score": mem.get("composite_score", 0.0),
            })

        # Aggregate: weighted average favoring highest confidence
        if confidences:
            # Top result gets 2x weight
            weights = [2.0] + [1.0] * (len(confidences) - 1)
            agg_score = sum(c * w for c, w in zip(confidences, weights)) / sum(weights)
        else:
            agg_score = 0.0

        if agg_score >= self.config.fok_confident_threshold:
            verdict = "confident"
        elif agg_score >= self.config.fok_uncertain_threshold:
            verdict = "uncertain"
        else:
            verdict = "unknown"

        return {
            "verdict": verdict,
            "score": round(agg_score, 4),
            "query": query,
            "supporting_count": len(memories),
            "top_memories": top_memories[:3],
        }

    def log_knowledge_gap(
        self,
        query: str,
        context: Optional[str] = None,
        reason: str = "empty_search",
    ) -> Dict[str, Any]:
        """Register a knowledge gap — something the system knows it doesn't know.

        Deduplicates by searching for existing gaps with similar queries.
        """
        # Check for existing similar gap
        existing_gaps = self.memory.search(
            query=f"gap: {query}",
            user_id=self.user_id,
            filters={"memory_type": "metamemory_gap", "mm_gap_status": "open"},
            limit=3,
        )
        existing = existing_gaps.get("results", [])

        for gap in existing:
            if gap.get("composite_score", 0) >= self.config.gap_dedup_threshold:
                # Increment frequency on existing gap
                gap_meta = gap.get("metadata", {}) or {}
                freq = int(gap_meta.get("mm_gap_frequency", 1)) + 1
                self.memory.update(gap["id"], {"metadata": {"mm_gap_frequency": freq}})
                return {
                    "action": "incremented",
                    "gap_id": gap["id"],
                    "frequency": freq,
                    "query": query,
                }

        # Create new gap
        now = datetime.now(timezone.utc).isoformat()
        result = self.memory.add(
            messages=f"Knowledge gap: {query}",
            user_id=self.user_id,
            metadata={
                "memory_type": "metamemory_gap",
                "mm_gap_query": query,
                "mm_gap_context": context or "",
                "mm_gap_reason": reason,
                "mm_gap_status": "open",
                "mm_gap_frequency": 1,
                "mm_gap_created_at": now,
                "mm_gap_resolved_at": None,
            },
        )

        results_list = result.get("results", [result])
        gap_id = results_list[0].get("id") if results_list else None

        return {
            "action": "created",
            "gap_id": gap_id,
            "query": query,
            "reason": reason,
        }

    def list_knowledge_gaps(
        self,
        status: str = "open",
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """List tracked knowledge gaps."""
        results = self.memory.search(
            query="knowledge gap",
            user_id=self.user_id,
            filters={"memory_type": "metamemory_gap", "mm_gap_status": status},
            limit=limit,
        )
        gaps = []
        for mem in results.get("results", []):
            metadata = mem.get("metadata", {}) or {}
            gaps.append({
                "id": mem.get("id"),
                "query": metadata.get("mm_gap_query", ""),
                "context": metadata.get("mm_gap_context", ""),
                "reason": metadata.get("mm_gap_reason", ""),
                "status": metadata.get("mm_gap_status", "open"),
                "frequency": int(metadata.get("mm_gap_frequency", 1)),
                "created_at": metadata.get("mm_gap_created_at", ""),
            })
        return gaps

    def resolve_knowledge_gap(
        self,
        gap_id: str,
    ) -> Dict[str, Any]:
        """Mark a knowledge gap as resolved."""
        now = datetime.now(timezone.utc).isoformat()
        self.memory.update(gap_id, {
            "metadata": {
                "mm_gap_status": "resolved",
                "mm_gap_resolved_at": now,
            },
        })
        return {"gap_id": gap_id, "status": "resolved", "resolved_at": now}

    def log_retrieval_outcome(
        self,
        query: str,
        memory_ids: List[str],
        outcome: str,
        correction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record whether a retrieval was useful, wrong, or irrelevant.

        Args:
            query: The search query that was used.
            memory_ids: IDs of the retrieved memories.
            outcome: One of "useful", "wrong", "irrelevant", "partial".
            correction: Optional correction text if outcome was wrong.
        """
        valid_outcomes = {"useful", "wrong", "irrelevant", "partial"}
        if outcome not in valid_outcomes:
            return {"error": f"Invalid outcome. Must be one of: {valid_outcomes}"}

        # Compute average confidence of retrieved memories
        confidences = []
        for mid in memory_ids[:10]:
            mem = self.memory.get(mid)
            if mem:
                metadata = mem.get("metadata", {}) or {}
                conf = metadata.get("mm_confidence", 0.5)
                confidences.append(float(conf))
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.5

        now = datetime.now(timezone.utc).isoformat()
        result = self.memory.add(
            messages=f"Retrieval outcome: {outcome} for query '{query}'",
            user_id=self.user_id,
            metadata={
                "memory_type": "metamemory_calibration",
                "mm_cal_query": query,
                "mm_cal_memory_ids": memory_ids[:10],
                "mm_cal_outcome": outcome,
                "mm_cal_correction": correction,
                "mm_cal_avg_confidence": avg_conf,
                "mm_cal_created_at": now,
            },
        )

        return {
            "action": "logged",
            "outcome": outcome,
            "avg_confidence": round(avg_conf, 4),
            "query": query,
        }

    def get_calibration_stats(self) -> Dict[str, Any]:
        """Get accuracy statistics over a rolling window.

        Returns calibration metrics: how well confidence predicts usefulness.
        """
        results = self.memory.search(
            query="retrieval outcome calibration",
            user_id=self.user_id,
            filters={"memory_type": "metamemory_calibration"},
            limit=self.config.calibration_window,
        )

        entries = results.get("results", [])
        if not entries:
            return {
                "total_evaluations": 0,
                "accuracy_rate": None,
                "avg_confidence_useful": None,
                "avg_confidence_wrong": None,
                "calibration_gap": None,
            }

        useful_confs = []
        wrong_confs = []
        total = 0
        useful_count = 0

        for entry in entries:
            metadata = entry.get("metadata", {}) or {}
            outcome = metadata.get("mm_cal_outcome", "")
            conf = float(metadata.get("mm_cal_avg_confidence", 0.5))
            total += 1

            if outcome == "useful":
                useful_count += 1
                useful_confs.append(conf)
            elif outcome == "wrong":
                wrong_confs.append(conf)

        accuracy = useful_count / total if total > 0 else None
        avg_useful = sum(useful_confs) / len(useful_confs) if useful_confs else None
        avg_wrong = sum(wrong_confs) / len(wrong_confs) if wrong_confs else None

        # Calibration gap: difference between confidence and actual accuracy
        calibration_gap = None
        if accuracy is not None and avg_useful is not None:
            calibration_gap = round(avg_useful - accuracy, 4)

        return {
            "total_evaluations": total,
            "accuracy_rate": round(accuracy, 4) if accuracy is not None else None,
            "avg_confidence_useful": round(avg_useful, 4) if avg_useful is not None else None,
            "avg_confidence_wrong": round(avg_wrong, 4) if avg_wrong is not None else None,
            "calibration_gap": calibration_gap,
        }

    def get_memory_confidence(
        self,
        memory_id: str,
    ) -> Dict[str, Any]:
        """Get detailed confidence breakdown for a specific memory."""
        mem = self.memory.get(memory_id)
        if not mem:
            return {"error": f"Memory {memory_id} not found"}

        metadata = mem.get("metadata", {}) or {}
        stored_conf = metadata.get("mm_confidence")

        # Recompute live confidence
        live_conf = compute_confidence(
            metadata=metadata,
            strength=mem.get("strength", 1.0),
            access_count=mem.get("access_count", 0),
            created_at=mem.get("created_at"),
            config=self.config,
        )

        return {
            "memory_id": memory_id,
            "stored_confidence": stored_conf,
            "live_confidence": round(live_conf, 4),
            "strength": mem.get("strength", 1.0),
            "access_count": mem.get("access_count", 0),
            "echo_depth": metadata.get("echo_depth", "shallow"),
            "layer": mem.get("layer", "sml"),
            "created_at": mem.get("created_at"),
        }
