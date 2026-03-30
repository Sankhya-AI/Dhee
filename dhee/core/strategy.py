"""Versioned retrieval strategies — inspectable, evolvable, rollback-safe.

Every strategy is a JSON file on disk (per DGM-H: "make everything a file").
MetaBuddhi proposes mutations; strategies that improve metrics get promoted;
those that degrade get rolled back.

A strategy controls scoring weights used by HybridSearcher and Buddhi's
HyperContext assembly. Changing a strategy changes HOW the system retrieves
and prioritizes information — the meta-cognitive lever.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RetrievalStrategy:
    """A versioned set of scoring weights and retrieval parameters.

    Fields mirror the tunable knobs in Dhee's retrieval pipeline:
    - semantic_weight / keyword_weight: HybridSearcher alpha split
    - recency_boost: bonus for recently accessed memories
    - strength_floor: minimum memory strength to surface
    - contrastive_boost: bonus when contrastive evidence supports a result
    - heuristic_relevance_weight: how much heuristic matches influence ranking
    - insight_budget: max insights to include in HyperContext
    - memory_budget: max memories to include in HyperContext
    """

    id: str
    version: int
    name: str
    description: str

    # Retrieval scoring weights
    semantic_weight: float = 0.7
    keyword_weight: float = 0.3
    recency_boost: float = 0.05
    strength_floor: float = 0.1
    contrastive_boost: float = 0.15
    heuristic_relevance_weight: float = 0.1

    # HyperContext budgets
    insight_budget: int = 10
    memory_budget: int = 10
    warning_budget: int = 5

    # Lifecycle
    created_at: float = field(default_factory=time.time)
    parent_id: Optional[str] = None   # which strategy this mutated from
    status: str = "active"            # active | candidate | retired | rolled_back

    # Performance tracking (populated by MetaBuddhi)
    eval_scores: List[float] = field(default_factory=list)
    eval_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "semantic_weight": self.semantic_weight,
            "keyword_weight": self.keyword_weight,
            "recency_boost": self.recency_boost,
            "strength_floor": self.strength_floor,
            "contrastive_boost": self.contrastive_boost,
            "heuristic_relevance_weight": self.heuristic_relevance_weight,
            "insight_budget": self.insight_budget,
            "memory_budget": self.memory_budget,
            "warning_budget": self.warning_budget,
            "created_at": self.created_at,
            "parent_id": self.parent_id,
            "status": self.status,
            "eval_scores": self.eval_scores[-20:],
            "eval_count": self.eval_count,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> RetrievalStrategy:
        return cls(
            id=d["id"],
            version=d.get("version", 1),
            name=d.get("name", "unnamed"),
            description=d.get("description", ""),
            semantic_weight=d.get("semantic_weight", 0.7),
            keyword_weight=d.get("keyword_weight", 0.3),
            recency_boost=d.get("recency_boost", 0.05),
            strength_floor=d.get("strength_floor", 0.1),
            contrastive_boost=d.get("contrastive_boost", 0.15),
            heuristic_relevance_weight=d.get("heuristic_relevance_weight", 0.1),
            insight_budget=d.get("insight_budget", 10),
            memory_budget=d.get("memory_budget", 10),
            warning_budget=d.get("warning_budget", 5),
            created_at=d.get("created_at", time.time()),
            parent_id=d.get("parent_id"),
            status=d.get("status", "active"),
            eval_scores=d.get("eval_scores", []),
            eval_count=d.get("eval_count", 0),
        )

    @property
    def avg_score(self) -> float:
        if not self.eval_scores:
            return 0.0
        return sum(self.eval_scores) / len(self.eval_scores)


class StrategyStore:
    """Manages versioned strategies on disk.

    Each strategy is a JSON file: strategies/{id}.json
    One is marked active at a time. History is preserved for rollback.
    """

    def __init__(self, data_dir: Optional[str] = None):
        self._dir = data_dir or os.path.join(
            os.path.expanduser("~"), ".dhee", "meta_buddhi", "strategies"
        )
        os.makedirs(self._dir, exist_ok=True)
        self._active_id: Optional[str] = None
        self._strategies: Dict[str, RetrievalStrategy] = {}
        self._load()

    def get_active(self) -> RetrievalStrategy:
        """Get the active strategy. Creates default if none exists."""
        if self._active_id and self._active_id in self._strategies:
            return self._strategies[self._active_id]
        return self._ensure_default()

    def save(self, strategy: RetrievalStrategy) -> None:
        """Persist a strategy to disk."""
        self._strategies[strategy.id] = strategy
        path = os.path.join(self._dir, f"{strategy.id}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(strategy.to_dict(), f, indent=2)
        except OSError as e:
            logger.debug("Failed to save strategy %s: %s", strategy.id, e)
        self._save_index()

    def promote(self, strategy_id: str) -> bool:
        """Make a candidate strategy the active one."""
        strategy = self._strategies.get(strategy_id)
        if not strategy:
            return False
        # Retire the current active
        if self._active_id and self._active_id in self._strategies:
            old = self._strategies[self._active_id]
            old.status = "retired"
            self.save(old)
        # Activate the new one
        strategy.status = "active"
        self._active_id = strategy.id
        self.save(strategy)
        return True

    def rollback(self, strategy_id: str) -> Optional[RetrievalStrategy]:
        """Roll back a strategy to its parent."""
        strategy = self._strategies.get(strategy_id)
        if not strategy or not strategy.parent_id:
            return None
        parent = self._strategies.get(strategy.parent_id)
        if not parent:
            return None
        strategy.status = "rolled_back"
        self.save(strategy)
        self.promote(parent.id)
        return parent

    def list_all(self) -> List[RetrievalStrategy]:
        return list(self._strategies.values())

    def get(self, strategy_id: str) -> Optional[RetrievalStrategy]:
        return self._strategies.get(strategy_id)

    def _ensure_default(self) -> RetrievalStrategy:
        """Create the default strategy if none exist."""
        default = RetrievalStrategy(
            id=str(uuid.uuid4()),
            version=1,
            name="default",
            description="Balanced default retrieval strategy",
            status="active",
        )
        self._active_id = default.id
        self.save(default)
        return default

    def _save_index(self) -> None:
        path = os.path.join(self._dir, "_index.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"active_id": self._active_id}, f)
        except OSError:
            pass

    def _load(self) -> None:
        # Load index
        index_path = os.path.join(self._dir, "_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                self._active_id = idx.get("active_id")
            except (OSError, json.JSONDecodeError):
                pass

        # Load all strategy files
        try:
            for fname in os.listdir(self._dir):
                if not fname.endswith(".json") or fname.startswith("_"):
                    continue
                fpath = os.path.join(self._dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    s = RetrievalStrategy.from_dict(data)
                    self._strategies[s.id] = s
                except (OSError, json.JSONDecodeError, KeyError):
                    continue
        except OSError:
            pass
