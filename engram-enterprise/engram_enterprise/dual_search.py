"""Dual retrieval engine: semantic + episodic with intersection promotion."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Set

from engram_enterprise.policy import enforce_scope_on_results
from engram.observability import metrics
from engram_enterprise.context_packer import pack_context
from engram_enterprise.reranker import intersection_promote


class DualSearchEngine:
    def __init__(self, *, memory, episodic_store, ref_manager):
        self.memory = memory
        self.episodic_store = episodic_store
        self.ref_manager = ref_manager

    @staticmethod
    def _parse_float_env(name: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
        try:
            value = float(os.environ.get(name, default))
        except Exception:
            value = float(default)
        return min(maximum, max(minimum, value))

    def search(
        self,
        *,
        query: str,
        user_id: str,
        agent_id: Optional[str],
        limit: int = 10,
        categories: Optional[List[str]] = None,
        allowed_confidentiality_scopes: Optional[Iterable[str]] = None,
        allowed_namespaces: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        # Materialize to avoid consuming a generator/iterator twice
        if allowed_namespaces is not None and not isinstance(allowed_namespaces, (list, tuple, set, frozenset)):
            allowed_namespaces = list(allowed_namespaces)
        semantic_payload = self.memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            limit=max(limit * 2, 10),
            categories=categories,
        )
        semantic_results = semantic_payload.get("results", semantic_payload)

        episodic_scenes = self.episodic_store.search_scenes(
            user_id=user_id,
            query=query,
            limit=max(limit, 5),
        )
        visible_scenes = self._filter_scenes_by_namespace(episodic_scenes, allowed_namespaces)
        boost_weight = self._parse_float_env("ENGRAM_V2_DUAL_INTERSECTION_BOOST_WEIGHT", 0.22)
        boost_cap = self._parse_float_env("ENGRAM_V2_DUAL_INTERSECTION_BOOST_CAP", 0.35)
        promoted = intersection_promote(
            semantic_results,
            visible_scenes,
            boost_weight=boost_weight,
            max_boost=boost_cap,
        )
        for item in promoted:
            if "confidentiality_scope" not in item:
                row = self.memory.db.get_memory(item.get("id"))
                if row:
                    item["confidentiality_scope"] = row.get("confidentiality_scope", "work")
                    item["importance"] = row.get("importance", 0.5)

        masked = enforce_scope_on_results(promoted, allowed_confidentiality_scopes)
        namespaced = self._enforce_namespace_on_results(masked, allowed_namespaces)
        final_results = namespaced[:limit]
        masked_count = sum(1 for item in final_results if item.get("masked"))
        if masked_count:
            metrics.record_masked_hits(masked_count)

        context_packet = pack_context(
            query=query,
            results=final_results,
            episodic_scenes=visible_scenes,
            max_tokens=800,
            max_items=min(8, limit),
        )

        if agent_id:
            visible_ids = [r.get("id") for r in final_results if r.get("id") and not r.get("masked")]
            self.ref_manager.record_retrieval_refs(visible_ids, agent_id=agent_id, strong=False)

        promoted_intersections = sum(1 for item in promoted if item.get("episodic_match"))
        boosted_items = sum(1 for item in promoted if float(item.get("intersection_boost", 0.0)) > 0.0)

        return {
            "results": final_results,
            "count": len(final_results),
            "context_packet": context_packet,
            "retrieval_trace": {
                "ranking_version": "dual_intersection_v2",
                "strategy": "semantic_plus_episodic_intersection",
                "semantic_candidates": len(semantic_results),
                "scene_candidates": len(visible_scenes),
                "intersection_candidates": int(promoted_intersections),
                "boosted_candidates": int(boosted_items),
                "boost_weight": float(boost_weight),
                "boost_cap": float(boost_cap),
                "masked_count": int(masked_count),
            },
            "scene_hits": [
                {
                    "scene_id": s.get("id"),
                    "summary": s.get("summary"),
                    "memory_ids": s.get("memory_ids", []),
                    "search_score": s.get("search_score"),
                }
                for s in visible_scenes[:limit]
            ],
        }

    @staticmethod
    def _namespace_mask(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("id"),
            "type": "private_event",
            "time": item.get("created_at") or item.get("timestamp"),
            "importance": item.get("importance", 0.5),
            "details": "[REDACTED]",
            "masked": True,
        }

    def _enforce_namespace_on_results(
        self,
        results: List[Dict[str, Any]],
        allowed_namespaces: Optional[Iterable[str]],
    ) -> List[Dict[str, Any]]:
        allowed: Set[str] = {str(ns).strip() for ns in (allowed_namespaces or []) if str(ns).strip()}
        if not allowed or "*" in allowed:
            normalized: List[Dict[str, Any]] = []
            for item in results:
                value = dict(item)
                value["masked"] = bool(value.get("masked", False))
                normalized.append(value)
            return normalized

        filtered: List[Dict[str, Any]] = []
        for item in results:
            namespace = str(item.get("namespace") or "default").strip() or "default"
            if namespace in allowed:
                value = dict(item)
                value["masked"] = bool(value.get("masked", False))
                filtered.append(value)
            else:
                filtered.append(self._namespace_mask(item))
        return filtered

    def _filter_scenes_by_namespace(
        self,
        scenes: List[Dict[str, Any]],
        allowed_namespaces: Optional[Iterable[str]],
    ) -> List[Dict[str, Any]]:
        allowed: Set[str] = {str(ns).strip() for ns in (allowed_namespaces or []) if str(ns).strip()}
        if not allowed or "*" in allowed:
            return scenes
        return [
            scene
            for scene in scenes
            if str(scene.get("namespace") or "default").strip() in allowed
        ]
