"""Episodic event indexing and search.

Extracted from memory/main.py — handles structured event extraction,
episodic search with intent-aware scoring, and entity aggregate lookups.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dhee.memory.retrieval_helpers import parse_bitemporal_datetime

logger = logging.getLogger(__name__)


def index_episodic_events_for_memory(
    *,
    db,
    config,
    memory_id: str,
    user_id: Optional[str],
    content: str,
    metadata: Optional[Dict[str, Any]],
) -> int:
    """Extract and index episodic events from a memory.

    Returns the number of events indexed.
    """
    orch_cfg = getattr(config, "orchestration", None)
    if not orch_cfg or not orch_cfg.enable_episodic_index:
        return 0
    if not user_id:
        return 0
    if not content:
        return 0
    try:
        from dhee.core.episodic_index import extract_episodic_events, extract_entity_aggregates

        events = extract_episodic_events(
            memory_id=memory_id,
            user_id=user_id,
            content=content,
            metadata=metadata or {},
        )
        # Re-index memory deterministically on updates/duplicate writes.
        db.delete_episodic_events_for_memory(memory_id)
        count = db.add_episodic_events(events)

        # Accumulate entity aggregates from extracted events.
        if events and hasattr(db, "upsert_entity_aggregate"):
            session_id = (metadata or {}).get("session_id", "")
            aggregates = extract_entity_aggregates(events, session_id, memory_id)
            for agg in aggregates:
                try:
                    if agg["agg_type"] == "item_set":
                        db.upsert_entity_set_member(
                            user_id=user_id,
                            entity_key=agg["entity_key"],
                            item_value=agg.get("item_value", ""),
                            session_id=agg.get("session_id"),
                            memory_id=agg.get("memory_id"),
                        )
                    else:
                        db.upsert_entity_aggregate(
                            user_id=user_id,
                            entity_key=agg["entity_key"],
                            agg_type=agg["agg_type"],
                            value_delta=agg.get("value_delta", 0),
                            value_unit=agg.get("value_unit"),
                            session_id=agg.get("session_id"),
                            memory_id=agg.get("memory_id"),
                        )
                except Exception as agg_exc:
                    logger.debug("Entity aggregate upsert failed: %s", agg_exc)

        return count
    except Exception as e:
        logger.debug("Episodic indexing failed for %s: %s", memory_id, e)
        return 0


def search_episodes(
    *,
    db,
    config,
    query: str,
    user_id: str,
    intent=None,
    actor_id: Optional[str] = None,
    time_anchor: Optional[str] = None,
    entity_hints: Optional[List[str]] = None,
    min_coverage: Optional[float] = None,
    limit: int = 80,
    intent_coverage_threshold_fn=None,
) -> Dict[str, Any]:
    """Search episodic events with intent-aware scoring.

    Returns {results, coverage}.
    """
    from dhee.core.episodic_index import (
        tokenize_query_terms,
        score_event_match,
        intent_event_types,
    )
    try:
        from dhee.core.answer_orchestration import AnswerIntent
    except ImportError:
        AnswerIntent = None

    orch_cfg = getattr(config, "orchestration", None)
    if not orch_cfg or not orch_cfg.enable_episodic_index:
        return {
            "results": [],
            "coverage": {
                "event_hit_count": 0,
                "unique_canonical_keys": 0,
                "sufficient": False,
            },
        }

    intent_value = ""
    if intent is not None:
        intent_value = (intent.value if hasattr(intent, "value") else str(intent)).strip().lower()

    event_types = intent_event_types(intent_value)
    if event_types is not None:
        event_types = list(event_types)

    normalized_hints = [str(h).strip().lower() for h in (entity_hints or []) if str(h).strip()]
    anchor_dt = parse_bitemporal_datetime(time_anchor) if time_anchor else None

    events = db.get_episodic_events(
        user_id=user_id,
        actor_id=actor_id,
        event_types=event_types,
        time_anchor=time_anchor,
        entity_hints=normalized_hints,
        limit=max(50, int(limit) * 6),
    )
    query_terms = tokenize_query_terms(query)
    if normalized_hints:
        query_terms = list(dict.fromkeys(query_terms + normalized_hints))

    scored_events: List[Dict[str, Any]] = []
    for event in events:
        score = score_event_match(event, query_terms)
        if normalized_hints:
            event_entity = str(
                event.get("entity_key") or event.get("actor_id") or event.get("actor_role") or ""
            ).lower()
            if any(h in event_entity for h in normalized_hints):
                score += 1.0
        if query_terms and score <= 0 and event_types is None:
            continue
        if score <= 0 and event_types is not None:
            score = 0.25
        # Prefer recency for latest-style questions.
        if intent_value == "latest":
            dt = parse_bitemporal_datetime(event.get("event_time"))
            if dt is not None:
                age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
                score += max(0.0, 2.0 - (age_days / 30.0))
        # Anchor-aware boost
        if anchor_dt is not None:
            ev_dt = parse_bitemporal_datetime(
                event.get("normalized_time_start") or event.get("event_time")
            )
            if ev_dt is not None:
                dist_days = abs((anchor_dt - ev_dt).total_seconds()) / 86400.0
                score += max(0.0, 0.75 - (dist_days / 45.0))
        event_copy = dict(event)
        event_copy["match_score"] = float(score)
        scored_events.append(event_copy)

    scored_events.sort(
        key=lambda row: (
            float(row.get("match_score", 0.0)),
            str(row.get("event_time") or ""),
            int(row.get("turn_id", 0) or 0),
        ),
        reverse=True,
    )
    top_events = scored_events[: max(1, int(limit))]
    unique_keys = {str(row.get("canonical_key") or "") for row in top_events if row.get("canonical_key")}
    unique_entities = {
        str(row.get("entity_key") or row.get("actor_id") or "").strip().lower()
        for row in top_events
        if str(row.get("entity_key") or row.get("actor_id") or "").strip()
    }
    numeric_fact_count = sum(1 for row in top_events if row.get("value_num") is not None)
    dated_fact_count = sum(
        1
        for row in top_events
        if str(row.get("normalized_time_start") or row.get("event_time") or "").strip()
    )

    context_cap = max(1, int(getattr(orch_cfg, "context_cap", 20)))
    coverage_ratio = min(1.0, len(unique_keys) / float(context_cap)) if unique_keys else 0.0
    intent_coverage = coverage_ratio
    if intent_value in {"count", "set_members"}:
        intent_coverage = min(1.0, len(unique_entities) / float(max(1, min(context_cap, 8))))
    elif intent_value in {"money_sum", "duration"}:
        intent_coverage = min(1.0, numeric_fact_count / float(max(1, min(context_cap, 8))))
    elif intent_value == "latest":
        intent_coverage = min(1.0, dated_fact_count / float(max(1, min(context_cap, 6))))

    default_threshold = float(getattr(orch_cfg, "map_reduce_coverage_threshold", 0.6))
    if intent_coverage_threshold_fn:
        threshold = intent_coverage_threshold_fn(intent_value, default_threshold)
    else:
        threshold = default_threshold

    if min_coverage is not None:
        try:
            threshold = max(0.0, min(1.0, float(min_coverage)))
        except (TypeError, ValueError):
            pass

    coverage = {
        "event_hit_count": len(top_events),
        "unique_canonical_keys": len(unique_keys),
        "unique_entities": len(unique_entities),
        "numeric_fact_count": int(numeric_fact_count),
        "dated_fact_count": int(dated_fact_count),
        "coverage_ratio": round(coverage_ratio, 4),
        "intent_coverage": round(float(intent_coverage), 4),
        "threshold": round(float(threshold), 4),
        "sufficient": bool(intent_coverage >= threshold and len(top_events) > 0),
    }
    return {"results": top_events, "coverage": coverage}


def lookup_entity_aggregates(
    *,
    db,
    query: str,
    user_id: str,
    intent: Optional[str] = None,
) -> Optional[str]:
    """Look up pre-computed entity aggregates that match a query.

    Returns a formatted answer string (e.g. "8 days", "$140", "3") or None.
    """
    from dhee.core.episodic_index import tokenize_query_terms

    if not hasattr(db, "get_entity_aggregates"):
        return None

    keywords = tokenize_query_terms(query)
    if not keywords:
        return None

    q_lower = query.lower()
    agg_types: List[str] = []
    if intent:
        intent_lower = intent.lower()
        if intent_lower in ("duration", "duration_sum"):
            agg_types = ["duration_sum"]
        elif intent_lower in ("money", "money_sum"):
            agg_types = ["money_sum"]
        elif intent_lower in ("count", "set_members"):
            agg_types = ["count", "item_set"]

    if not agg_types:
        if any(w in q_lower for w in ("how long", "how many days", "how many hours",
                                       "how many weeks", "how many months", "duration")):
            agg_types = ["duration_sum"]
        elif any(w in q_lower for w in ("how much", "cost", "spent", "price", "money")):
            agg_types = ["money_sum"]
        else:
            agg_types = ["count", "item_set", "duration_sum"]

    best_match = None
    best_score = 0.0

    for agg_type in agg_types:
        rows = db.get_entity_aggregates(
            user_id=user_id,
            agg_type=agg_type,
            entity_hints=keywords,
        )
        for row in rows:
            entity_key = str(row.get("entity_key") or "").lower()
            score = sum(1.0 for kw in keywords if kw in entity_key)
            sessions = row.get("contributing_sessions")
            if sessions:
                try:
                    n_sessions = len(json.loads(sessions)) if isinstance(sessions, str) else len(sessions)
                except Exception:
                    n_sessions = 0
                if n_sessions > 1:
                    score += 0.5

            if score > best_score:
                best_score = score
                best_match = row

    if not best_match or best_score < 1.0:
        return None

    agg_type = best_match.get("agg_type", "")
    value_num = best_match.get("value_num")
    value_unit = best_match.get("value_unit")
    item_set = best_match.get("item_set")

    if agg_type == "item_set" and item_set:
        try:
            items = json.loads(item_set) if isinstance(item_set, str) else item_set
            return str(len(items))
        except Exception:
            pass

    if value_num is not None:
        try:
            num = float(value_num)
            if abs(num - round(num)) < 1e-9:
                formatted = str(int(round(num)))
            else:
                formatted = f"{num:g}"
            if value_unit:
                if agg_type == "money_sum":
                    return f"${formatted}" if value_unit == "USD" else f"{formatted} {value_unit}"
                return f"{formatted} {value_unit}{'s' if num != 1 else ''}"
            return formatted
        except (TypeError, ValueError):
            pass

    return None
