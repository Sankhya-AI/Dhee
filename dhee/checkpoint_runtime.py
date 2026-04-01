"""Shared checkpoint runtime helpers for supported Dhee entrypoints."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional


def _serialize_optional(value: Any) -> Any:
    """Convert common model objects to plain dictionaries when available."""
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


def run_checkpoint_common(
    *,
    logger: logging.Logger,
    log_prefix: str,
    user_id: str,
    summary: str,
    status: str,
    agent_id: str,
    repo: Optional[str],
    decisions: Optional[List[str]],
    files_touched: Optional[List[str]],
    todos: Optional[List[str]],
    task_type: Optional[str],
    outcome_score: Optional[float],
    what_worked: Optional[str],
    what_failed: Optional[str],
    key_decision: Optional[str],
    remember_to: Optional[str],
    trigger_keywords: Optional[List[str]],
    enrich_pending_fn: Callable[..., Dict[str, Any]],
    record_outcome_fn: Callable[..., Any],
    reflect_fn: Callable[..., List[Any]],
    store_intention_fn: Callable[..., Any],
) -> Dict[str, Any]:
    """Execute the shared checkpoint side-effects for Dhee entrypoints."""
    result: Dict[str, Any] = {}
    warnings: List[str] = []

    clamped_score = None
    if outcome_score is not None:
        clamped_score = max(0.0, min(1.0, float(outcome_score)))

    try:
        from dhee.core.kernel import save_session_digest

        digest = save_session_digest(
            task_summary=summary,
            agent_id=agent_id,
            repo=repo,
            status=status,
            decisions_made=decisions,
            files_touched=files_touched,
            todos_remaining=todos,
        )
        result["session_saved"] = True
        if isinstance(digest, dict):
            result["session_id"] = digest.get("session_id")
    except Exception as exc:
        logger.warning("%s session digest save failed: %s", log_prefix, exc, exc_info=True)
        result["session_saved"] = False
        result["session_save_error"] = str(exc)
        warnings.append(f"session_save_failed: {exc}")

    try:
        enrich_result = enrich_pending_fn(
            user_id=user_id,
            batch_size=10,
            max_batches=5,
        )
        enriched = enrich_result.get("enriched_count", 0)
        if enriched > 0:
            result["memories_enriched"] = enriched
    except Exception as exc:
        logger.warning("%s deferred enrichment failed: %s", log_prefix, exc, exc_info=True)
        result["enrichment_error"] = str(exc)
        warnings.append(f"deferred_enrichment_failed: {exc}")

    if task_type and clamped_score is not None:
        insight = record_outcome_fn(
            user_id=user_id,
            task_type=task_type,
            score=clamped_score,
        )
        result["outcome_recorded"] = True
        insight_payload = _serialize_optional(insight)
        if insight_payload:
            result["auto_insight"] = insight_payload

    if any([what_worked, what_failed, key_decision]):
        insights = reflect_fn(
            user_id=user_id,
            task_type=task_type or "general",
            what_worked=what_worked,
            what_failed=what_failed,
            key_decision=key_decision,
            outcome_score=clamped_score,
        )
        result["insights_created"] = len(insights)

    if remember_to:
        intention = store_intention_fn(
            user_id=user_id,
            description=remember_to,
            trigger_keywords=trigger_keywords,
        )
        intention_payload = _serialize_optional(intention)
        if intention_payload:
            result["intention_stored"] = intention_payload

    if warnings:
        result["warnings"] = warnings

    return result
