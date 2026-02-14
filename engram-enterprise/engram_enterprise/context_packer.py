"""Context packet builder for token-bounded retrieval output."""

from __future__ import annotations

from typing import Dict, List, Set


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Conservative heuristic for GPT-style tokenization.
    return max(1, len(text) // 4)


def pack_context(
    *,
    query: str,
    results: List[Dict],
    episodic_scenes: List[Dict],
    max_tokens: int = 800,
    max_items: int = 8,
) -> Dict:
    scene_ids: Set[str] = {str(scene.get("id")) for scene in episodic_scenes if scene.get("id")}
    snippets: List[Dict] = []
    token_used = _estimate_tokens(query)
    masked_count = 0

    for item in results[: max_items * 3]:
        is_masked = bool(item.get("masked"))
        if is_masked:
            masked_count += 1
        text = item.get("memory") or item.get("details") or ""
        candidate_tokens = _estimate_tokens(text)
        if token_used + candidate_tokens > max_tokens and snippets:
            break

        snippet = {
            "memory_id": item.get("id"),
            "text": text,
            "masked": is_masked,
            "score": item.get("composite_score", item.get("score")),
            "citations": {
                "scene_ids": list(scene_ids),
            },
        }
        snippets.append(snippet)
        token_used += candidate_tokens
        if len(snippets) >= max_items:
            break

    return {
        "query": query,
        "snippets": snippets,
        "token_usage": {
            "estimated_tokens": token_used,
            "budget": max_tokens,
        },
        "masking": {
            "masked_count": masked_count,
            "total_candidates": len(results),
        },
    }
