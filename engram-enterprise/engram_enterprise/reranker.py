"""Re-ranking helpers for dual retrieval."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _build_episodic_signal(
    episodic_scene_results: List[Dict[str, Any]],
) -> Tuple[Dict[str, float], Dict[str, int]]:
    signal_by_memory: Dict[str, float] = {}
    scene_count_by_memory: Dict[str, int] = {}
    for rank, scene in enumerate(episodic_scene_results):
        memory_ids = [str(mid) for mid in (scene.get("memory_ids") or []) if str(mid).strip()]
        if not memory_ids:
            continue
        rank_weight = 1.0 / (1.0 + float(rank))
        scene_score = _coerce_float(scene.get("search_score"), 0.0)
        scene_weight = max(0.15, min(1.0, scene_score))
        contribution = rank_weight * scene_weight
        for memory_id in memory_ids:
            signal_by_memory[memory_id] = signal_by_memory.get(memory_id, 0.0) + contribution
            scene_count_by_memory[memory_id] = scene_count_by_memory.get(memory_id, 0) + 1

    for memory_id, signal in list(signal_by_memory.items()):
        signal_by_memory[memory_id] = min(1.0, signal)
    return signal_by_memory, scene_count_by_memory


def intersection_promote(
    semantic_results: List[Dict[str, Any]],
    episodic_scene_results: List[Dict[str, Any]],
    *,
    boost_weight: float = 0.22,
    max_boost: float = 0.35,
) -> List[Dict[str, Any]]:
    """Promote semantic results that also appear in episodic scenes.

    Uses deterministic boost calibration:
    - Episodic signal is derived from scene rank + scene score.
    - Final score = base_score * (1 + intersection_boost).
    - Stable tie-breakers preserve semantic order.
    """
    weight = min(1.0, max(0.0, _coerce_float(boost_weight, 0.22)))
    cap = min(1.0, max(0.0, _coerce_float(max_boost, 0.35)))
    signal_by_memory, scene_count_by_memory = _build_episodic_signal(episodic_scene_results)
    episodic_memory_ids: Set[str] = set(signal_by_memory.keys())

    ranked: List[Tuple[float, float, int, Dict[str, Any]]] = []
    for semantic_rank, item in enumerate(semantic_results):
        enriched = dict(item)
        memory_id = str(item.get("id"))
        base_score = _coerce_float(item.get("composite_score"), _coerce_float(item.get("score"), 0.0))
        episodic_signal = signal_by_memory.get(memory_id, 0.0)
        intersection_boost = min(cap, episodic_signal * weight)
        final_score = base_score * (1.0 + intersection_boost)

        enriched["episodic_match"] = memory_id in episodic_memory_ids
        enriched["episodic_scene_count"] = int(scene_count_by_memory.get(memory_id, 0))
        enriched["episodic_signal"] = round(float(episodic_signal), 6)
        enriched["intersection_boost"] = round(float(intersection_boost), 6)
        enriched["base_composite_score"] = float(base_score)
        enriched["composite_score"] = float(final_score)

        # Tie-breaking preserves semantic ranking deterministically.
        ranked.append((float(final_score), float(base_score), -semantic_rank, enriched))

    ranked.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [row[3] for row in ranked]
