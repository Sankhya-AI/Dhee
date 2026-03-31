"""Advanced forgetting mechanisms for CLS Distillation Memory.

Three biologically-inspired forgetting mechanisms beyond simple exponential decay:
1. InterferencePruner — contradictory memories demote each other
2. RedundancyCollapser — near-duplicate memories auto-fuse
3. HomeostaticNormalizer — memory budget enforcement with pressure-based decay
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from dhee.configs.base import DistillationConfig, FadeMemConfig
    from dhee.db.sqlite import SQLiteManager

logger = logging.getLogger(__name__)


class InterferencePruner:
    """Demote contradictory memories discovered during decay cycles.

    For memories above a minimum strength, finds nearest neighbors and
    checks for contradiction. If contradictory, the weaker memory gets demoted.
    """

    def __init__(
        self,
        db: "SQLiteManager",
        config: "DistillationConfig",
        fade_config: "FadeMemConfig",
        resolve_conflict_fn=None,
        search_fn=None,
        llm=None,
    ):
        self.db = db
        self.config = config
        self.fade_config = fade_config
        self.resolve_conflict_fn = resolve_conflict_fn
        self.search_fn = search_fn
        self.llm = llm

    def run(
        self,
        memories: List[Dict[str, Any]],
        user_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Check memories for interference and demote contradictions.

        Returns {"checked": N, "demoted": N}.
        """
        if not self.config.enable_interference_pruning:
            return {"checked": 0, "demoted": 0}

        if not self.resolve_conflict_fn or not self.search_fn:
            return {"checked": 0, "demoted": 0}

        checked = 0
        demoted = 0
        min_strength = 0.2

        for memory in memories:
            if memory.get("immutable"):
                continue
            strength = float(memory.get("strength", 0.0))
            if strength < min_strength:
                continue

            embedding = memory.get("embedding")
            if not embedding:
                continue

            checked += 1

            # Find nearest neighbor
            try:
                filters = {"user_id": user_id} if user_id else {}
                neighbors = self.search_fn(
                    query="",
                    vectors=embedding,
                    limit=2,
                    filters=filters,
                )
                # Skip self
                neighbors = [n for n in neighbors if n.id != memory["id"]]
                if not neighbors:
                    continue

                nearest = neighbors[0]
                similarity = float(nearest.score)

                if similarity < self.fade_config.conflict_similarity_threshold:
                    continue

                # Fetch the neighbor memory from DB
                neighbor_mem = self.db.get_memory(nearest.id)
                if not neighbor_mem:
                    continue

                # Check for contradiction
                resolution = self.resolve_conflict_fn(
                    neighbor_mem, memory.get("memory", ""), self.llm
                )

                if resolution and resolution.classification == "CONTRADICTORY":
                    # Demote the weaker one
                    mem_strength = float(memory.get("strength", 0.0))
                    neighbor_strength = float(neighbor_mem.get("strength", 0.0))

                    if mem_strength <= neighbor_strength:
                        target_id = memory["id"]
                        old_strength = mem_strength
                    else:
                        target_id = neighbor_mem["id"]
                        old_strength = neighbor_strength

                    new_strength = old_strength * 0.3
                    self.db.update_memory(target_id, {"strength": new_strength})
                    self.db.log_event(
                        target_id,
                        "INTERFERENCE_DEMOTE",
                        old_strength=old_strength,
                        new_strength=new_strength,
                    )
                    demoted += 1

            except Exception as e:
                logger.debug("Interference check failed for %s: %s", memory.get("id"), e)

        return {"checked": checked, "demoted": demoted}


class RedundancyCollapser:
    """Temporal-semantic clustering fusion (FadeMem-inspired).

    Instead of pairwise cosine cutoff, builds a similarity graph over all
    memories, then uses Union-Find clustering to discover transitive groups.
    Memories A~B and B~C get grouped even if A is not directly similar to C.

    Temporal proximity is factored in: memories closer in time are more
    likely to be fused (FadeMem's temporal-semantic proximity).
    """

    def __init__(
        self,
        db: "SQLiteManager",
        config: "DistillationConfig",
        fuse_fn=None,
        search_fn=None,
    ):
        self.db = db
        self.config = config
        self.fuse_fn = fuse_fn
        self.search_fn = search_fn

    def run(
        self,
        memories: List[Dict[str, Any]],
        user_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Find and fuse redundant memory clusters.

        Uses Union-Find over a semantic similarity graph to discover
        transitive clusters, then fuses each cluster.

        Returns {"groups_fused": N, "memories_fused": N}.
        """
        if not self.config.enable_redundancy_collapse:
            return {"groups_fused": 0, "memories_fused": 0}

        if not self.fuse_fn or not self.search_fn:
            return {"groups_fused": 0, "memories_fused": 0}

        threshold = self.config.redundancy_collapse_threshold

        # Filter to fuseable memories with embeddings
        fuseable = []
        for m in memories:
            if m.get("immutable"):
                continue
            if not m.get("embedding"):
                continue
            fuseable.append(m)

        if len(fuseable) < 2:
            return {"groups_fused": 0, "memories_fused": 0}

        # Build Union-Find structure for transitive clustering
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])  # path compression
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Initialize each memory as its own cluster
        for m in fuseable:
            parent[m["id"]] = m["id"]

        # Build similarity graph: for each memory, find neighbors and union
        for m in fuseable:
            mid = m["id"]
            try:
                filters = {"user_id": user_id} if user_id else {}
                neighbors = self.search_fn(
                    query="",
                    vectors=m["embedding"],
                    limit=8,  # check more neighbors for transitive discovery
                    filters=filters,
                )

                for n in neighbors:
                    if n.id == mid:
                        continue
                    sim = float(n.score)
                    if sim < threshold:
                        continue

                    # Temporal proximity bonus: memories within 24h get a similarity boost
                    # This implements FadeMem's temporal-semantic clustering
                    n_mem = self.db.get_memory(n.id)
                    if not n_mem or n_mem.get("immutable"):
                        continue

                    effective_sim = self._temporal_adjusted_similarity(
                        sim, m.get("created_at", ""), n_mem.get("created_at", "")
                    )

                    if effective_sim >= threshold:
                        # Ensure neighbor is in our fuseable set
                        if n.id in parent:
                            union(mid, n.id)

            except Exception as e:
                logger.debug("Clustering failed for %s: %s", mid, e)

        # Collect clusters
        clusters: Dict[str, List[str]] = {}
        for m in fuseable:
            root = find(m["id"])
            clusters.setdefault(root, []).append(m["id"])

        # Fuse clusters with 2+ members
        groups_fused = 0
        memories_fused = 0

        for root, member_ids in clusters.items():
            if len(member_ids) < 2:
                continue
            # Cap cluster size to avoid mega-fusions
            if len(member_ids) > 10:
                member_ids = member_ids[:10]

            try:
                result = self.fuse_fn(member_ids, user_id=user_id)
                if result and not result.get("error"):
                    groups_fused += 1
                    memories_fused += len(member_ids)
            except Exception as e:
                logger.debug("Fusion failed for cluster %s: %s", root, e)

        return {"groups_fused": groups_fused, "memories_fused": memories_fused}

    @staticmethod
    def _temporal_adjusted_similarity(
        semantic_sim: float, ts_a: str, ts_b: str
    ) -> float:
        """Boost similarity for temporally close memories (FadeMem approach).

        Memories within 1 hour get +0.05 boost, within 24h get +0.02.
        This means thematically related memories from the same conversation
        are more likely to fuse, even if their cosine similarity is slightly
        below threshold.
        """
        if not ts_a or not ts_b:
            return semantic_sim

        try:
            from datetime import datetime

            def _parse(ts: str) -> datetime:
                # Handle ISO formats with/without timezone
                for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                    try:
                        return datetime.strptime(ts[:26], fmt)
                    except ValueError:
                        continue
                return datetime.now()

            dt_a = _parse(ts_a)
            dt_b = _parse(ts_b)
            hours_apart = abs((dt_a - dt_b).total_seconds()) / 3600.0

            if hours_apart < 1.0:
                return semantic_sim + 0.05  # same conversation likely
            elif hours_apart < 24.0:
                return semantic_sim + 0.02  # same day
            return semantic_sim

        except Exception:
            return semantic_sim


class HomeostaticNormalizer:
    """Enforce memory budgets per namespace with pressure-based decay.

    When a namespace exceeds its budget, applies extra decay pressure
    to the weakest memories proportional to the excess ratio.
    """

    def __init__(
        self,
        db: "SQLiteManager",
        config: "DistillationConfig",
        fade_config: "FadeMemConfig",
        delete_fn=None,
    ):
        self.db = db
        self.config = config
        self.fade_config = fade_config
        self.delete_fn = delete_fn

    def run(
        self,
        user_id: str,
    ) -> Dict[str, Any]:
        """Apply homeostatic pressure to namespaces over budget.

        Returns {"namespaces_over_budget": N, "pressured": N, "forgotten": N}.
        """
        if not self.config.enable_homeostasis:
            return {"namespaces_over_budget": 0, "pressured": 0, "forgotten": 0}

        counts = self.db.get_memory_count_by_namespace(user_id)
        budget = self.config.homeostasis_budget_per_namespace
        pressure_factor = self.config.homeostasis_pressure_factor

        namespaces_over = 0
        total_pressured = 0
        total_forgotten = 0

        for namespace, count in counts.items():
            if count <= budget:
                continue

            namespaces_over += 1
            excess_ratio = (count - budget) / budget

            # Fetch weakest memories in this namespace
            weak_memories = self.db.get_all_memories(
                user_id=user_id,
                namespace=namespace,
                min_strength=0.0,
                limit=count,
            )

            # Sort by strength ascending (weakest first)
            weak_memories.sort(key=lambda m: float(m.get("strength", 0.0)))

            for memory in weak_memories:
                if memory.get("immutable"):
                    continue

                strength = float(memory.get("strength", 0.0))
                # Apply extra decay proportional to excess
                pressure = strength * pressure_factor * excess_ratio
                new_strength = max(0.0, strength - pressure)

                if new_strength < self.fade_config.forgetting_threshold:
                    if self.delete_fn:
                        try:
                            self.delete_fn(memory["id"])
                            total_forgotten += 1
                        except Exception as e:
                            logger.debug("Homeostasis delete failed for %s: %s", memory["id"], e)
                else:
                    self.db.update_memory(memory["id"], {"strength": new_strength})
                    total_pressured += 1

        return {
            "namespaces_over_budget": namespaces_over,
            "pressured": total_pressured,
            "forgotten": total_forgotten,
        }
