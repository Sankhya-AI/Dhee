"""Dhee v3 — 5-Stage Weighted Reciprocal Rank Fusion Pipeline.

Explicit ranking contract (zero LLM on hot path):

Stage 1: Per-index retrieval (parallel, 0 LLM)
Stage 2: Score normalization (min-max within each index)
Stage 3: Weighted RRF (k=60, configurable weights per index)
Stage 4: Post-fusion adjustments (recency, confidence, staleness, conflicts)
Stage 5: Final ranking + dedup

No reranker stage. If retrieval quality is insufficient, fix embeddings
or distillation, not the hot path.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FusionConfig:
    """Configuration for the 5-stage fusion pipeline."""

    # Stage 1: Per-index top-K
    raw_top_k: int = 20
    distilled_top_k: int = 15
    episodic_top_k: int = 10

    # Stage 3: RRF weights
    rrf_k: int = 60  # standard RRF constant
    weight_distilled: float = 1.0
    weight_episodic: float = 0.7
    weight_raw: float = 0.5

    # Stage 4: Adjustment parameters
    recency_boost_max: float = 0.3  # max 30% boost for fresh raw
    recency_decay_hours: float = 24.0
    confidence_floor: float = 0.5  # score *= 0.5 + 0.5 * confidence
    staleness_penalty: float = 0.3  # stale/suspect get 70% penalty
    contradiction_penalty: float = 0.5  # open conflicts get 50% penalty

    # Stage 5: Final output
    final_top_n: int = 10


@dataclass
class FusionCandidate:
    """A candidate passing through the fusion pipeline."""

    row_id: str
    source_kind: str  # raw | distilled | episodic
    source_type: str  # event | belief | policy | insight | heuristic
    source_id: str
    retrieval_text: str
    raw_score: float = 0.0  # cosine similarity from index
    normalized_score: float = 0.0  # after min-max normalization
    rrf_score: float = 0.0  # after weighted RRF
    adjusted_score: float = 0.0  # after post-fusion adjustments
    confidence: float = 1.0
    utility: float = 0.0
    status: str = "active"
    created_at: Optional[str] = None
    has_open_conflicts: bool = False
    # Lineage for dedup
    lineage_event_ids: Optional[List[str]] = None


@dataclass
class FusionBreakdown:
    """Loggable breakdown of how fusion produced its results."""

    query: str
    config: Dict[str, Any]
    per_index_counts: Dict[str, int]
    pre_adjustment_top5: List[Dict[str, Any]]
    post_adjustment_top5: List[Dict[str, Any]]
    dedup_removed: int
    final_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query[:100],
            "per_index_counts": self.per_index_counts,
            "pre_adjustment_top5": self.pre_adjustment_top5,
            "post_adjustment_top5": self.post_adjustment_top5,
            "dedup_removed": self.dedup_removed,
            "final_count": self.final_count,
        }


def _parse_iso(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


class RRFFusion:
    """5-stage Weighted Reciprocal Rank Fusion pipeline.

    Usage:
        fusion = RRFFusion(config)
        results, breakdown = fusion.fuse(
            raw_candidates=[...],
            distilled_candidates=[...],
            episodic_candidates=[...],
            conflict_checker=lambda type, id: bool,
        )
    """

    def __init__(self, config: Optional[FusionConfig] = None):
        self.config = config or FusionConfig()

    def fuse(
        self,
        raw_candidates: List[FusionCandidate],
        distilled_candidates: List[FusionCandidate],
        episodic_candidates: Optional[List[FusionCandidate]] = None,
        *,
        conflict_checker: Optional[Any] = None,
        query: str = "",
    ) -> Tuple[List[FusionCandidate], FusionBreakdown]:
        """Run the full 5-stage fusion pipeline.

        Args:
            raw_candidates: Candidates from raw_index with raw_score set
            distilled_candidates: Candidates from distilled_index
            episodic_candidates: Optional candidates from episodic_index
            conflict_checker: callable(source_type, source_id) -> bool
            query: The original query string (for logging)

        Returns:
            (ranked_results, breakdown)
        """
        cfg = self.config
        episodic = episodic_candidates or []

        # Stage 1: Trim to per-index top-K
        raw = sorted(raw_candidates, key=lambda c: -c.raw_score)[:cfg.raw_top_k]
        dist = sorted(distilled_candidates, key=lambda c: -c.raw_score)[:cfg.distilled_top_k]
        epi = sorted(episodic, key=lambda c: -c.raw_score)[:cfg.episodic_top_k]

        per_index_counts = {
            "raw": len(raw), "distilled": len(dist), "episodic": len(epi),
        }

        # Stage 2: Min-max normalization within each index
        self._normalize(raw)
        self._normalize(dist)
        self._normalize(epi)

        # Stage 3: Weighted RRF
        # Build a combined dict: row_id → candidate, accumulating RRF score
        combined: Dict[str, FusionCandidate] = {}

        for rank, c in enumerate(raw):
            rrf = cfg.weight_raw / (cfg.rrf_k + rank + 1)
            if c.row_id in combined:
                combined[c.row_id].rrf_score += rrf
            else:
                c.rrf_score = rrf
                combined[c.row_id] = c

        for rank, c in enumerate(dist):
            rrf = cfg.weight_distilled / (cfg.rrf_k + rank + 1)
            if c.row_id in combined:
                combined[c.row_id].rrf_score += rrf
            else:
                c.rrf_score = rrf
                combined[c.row_id] = c

        for rank, c in enumerate(epi):
            rrf = cfg.weight_episodic / (cfg.rrf_k + rank + 1)
            if c.row_id in combined:
                combined[c.row_id].rrf_score += rrf
            else:
                c.rrf_score = rrf
                combined[c.row_id] = c

        # Pre-adjustment snapshot
        pre_sorted = sorted(combined.values(), key=lambda c: -c.rrf_score)
        pre_top5 = [
            {"row_id": c.row_id, "kind": c.source_kind, "rrf": round(c.rrf_score, 6)}
            for c in pre_sorted[:5]
        ]

        # Stage 4: Post-fusion adjustments
        now = datetime.now(timezone.utc)
        for c in combined.values():
            score = c.rrf_score

            # Recency boost (raw only)
            if c.source_kind == "raw" and c.created_at:
                created = _parse_iso(c.created_at)
                if created:
                    age_hours = max(0, (now - created).total_seconds() / 3600)
                    boost = 1.0 + cfg.recency_boost_max * math.exp(
                        -age_hours / cfg.recency_decay_hours
                    )
                    score *= boost

            # Confidence normalization
            score *= cfg.confidence_floor + (1.0 - cfg.confidence_floor) * c.confidence

            # Staleness penalty
            if c.status in ("stale", "suspect"):
                score *= cfg.staleness_penalty

            # Hard invalidation exclusion
            if c.status == "invalidated":
                score = 0.0

            # Contradiction penalty
            if conflict_checker and c.source_type and c.source_id:
                try:
                    if conflict_checker(c.source_type, c.source_id):
                        c.has_open_conflicts = True
                        score *= cfg.contradiction_penalty
                except Exception:
                    pass

            c.adjusted_score = score

        # Stage 5: Final ranking + dedup
        ranked = sorted(combined.values(), key=lambda c: -c.adjusted_score)

        # Dedup: if raw and distilled of same content via lineage, keep distilled
        seen_source_ids: Dict[str, FusionCandidate] = {}
        deduped: List[FusionCandidate] = []
        dedup_removed = 0

        for c in ranked:
            sid = c.source_id
            if sid in seen_source_ids:
                existing = seen_source_ids[sid]
                # Keep the distilled version
                if c.source_kind == "distilled" and existing.source_kind == "raw":
                    deduped = [x for x in deduped if x.source_id != sid]
                    deduped.append(c)
                    seen_source_ids[sid] = c
                    dedup_removed += 1
                else:
                    dedup_removed += 1
            else:
                seen_source_ids[sid] = c
                deduped.append(c)

        final = deduped[:cfg.final_top_n]

        # Post-adjustment snapshot
        post_top5 = [
            {
                "row_id": c.row_id, "kind": c.source_kind,
                "adjusted": round(c.adjusted_score, 6),
                "conflicts": c.has_open_conflicts,
            }
            for c in final[:5]
        ]

        breakdown = FusionBreakdown(
            query=query,
            config={
                "rrf_k": cfg.rrf_k,
                "weights": {
                    "raw": cfg.weight_raw,
                    "distilled": cfg.weight_distilled,
                    "episodic": cfg.weight_episodic,
                },
            },
            per_index_counts=per_index_counts,
            pre_adjustment_top5=pre_top5,
            post_adjustment_top5=post_top5,
            dedup_removed=dedup_removed,
            final_count=len(final),
        )

        return final, breakdown

    @staticmethod
    def _normalize(candidates: List[FusionCandidate]) -> None:
        """Min-max normalize raw_score within a candidate list."""
        if not candidates:
            return

        scores = [c.raw_score for c in candidates]
        min_s = min(scores)
        max_s = max(scores)
        spread = max_s - min_s

        if spread < 1e-9:
            for c in candidates:
                c.normalized_score = 1.0 if max_s > 0 else 0.0
        else:
            for c in candidates:
                c.normalized_score = (c.raw_score - min_s) / spread
