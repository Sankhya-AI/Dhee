"""Dhee v3 — Observability: expanded cognition_health with v3 metrics.

Adds v3-specific metrics to the existing cognition_health() output:
- Stale/suspect/invalidated derived counts per type
- Pending conflict backlog
- Lease contention (active locks)
- Candidate promotion stats (promoted/rejected/quarantined)
- Retrieval view freshness
- Maintenance job health

All metrics are pure SQL COUNT/aggregation queries. Zero LLM calls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def v3_health(
    conn: "sqlite3.Connection",
    lock: "threading.RLock",
    *,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute v3 substrate health metrics.

    Returns a dict suitable for merging into existing cognition_health() output.
    """
    health: Dict[str, Any] = {}

    with lock:
        # --- Raw event counts ---
        if user_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM raw_memory_events WHERE user_id = ? AND status = 'active'",
                (user_id,),
            ).fetchone()
            health["raw_events_active"] = row[0] if row else 0

            row = conn.execute(
                "SELECT COUNT(*) FROM raw_memory_events WHERE user_id = ? AND status = 'corrected'",
                (user_id,),
            ).fetchone()
            health["raw_events_corrected"] = row[0] if row else 0
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM raw_memory_events WHERE status = 'active'"
            ).fetchone()
            health["raw_events_active"] = row[0] if row else 0

        # --- Derived object counts by invalidation status ---
        derived_tables = {
            "beliefs": "belief_id",
            "policies": "policy_id",
            "insights": "insight_id",
            "heuristics": "heuristic_id",
        }
        invalidation_statuses = ("stale", "suspect", "invalidated")
        derived_health: Dict[str, Dict[str, int]] = {}

        for table, _pk in derived_tables.items():
            counts: Dict[str, int] = {}
            for status in invalidation_statuses:
                try:
                    if user_id:
                        row = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE status = ? AND user_id = ?",
                            (status, user_id),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE status = ?",
                            (status,),
                        ).fetchone()
                    counts[status] = row[0] if row else 0
                except Exception:
                    counts[status] = -1  # table might not exist yet
            derived_health[table] = counts

        health["derived_invalidation"] = derived_health

        # --- Conflict backlog ---
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM cognitive_conflicts WHERE resolution_status = 'open'"
            ).fetchone()
            health["open_conflicts"] = row[0] if row else 0
        except Exception:
            health["open_conflicts"] = -1

        # --- Lease contention ---
        try:
            now_iso = _utcnow_iso()
            row = conn.execute(
                "SELECT COUNT(*) FROM locks WHERE lease_expires_at > ?",
                (now_iso,),
            ).fetchone()
            health["active_leases"] = row[0] if row else 0
        except Exception:
            health["active_leases"] = -1

        # --- Candidate promotion stats ---
        try:
            promo_stats: Dict[str, int] = {}
            for status in ("pending_validation", "promoted", "rejected", "quarantined"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM distillation_candidates WHERE status = ?",
                    (status,),
                ).fetchone()
                promo_stats[status] = row[0] if row else 0
            health["distillation_candidates"] = promo_stats
        except Exception:
            health["distillation_candidates"] = {}

        # --- Maintenance job health ---
        try:
            job_stats: Dict[str, int] = {}
            for status in ("pending", "running", "completed", "failed"):
                row = conn.execute(
                    "SELECT COUNT(*) FROM maintenance_jobs WHERE status = ?",
                    (status,),
                ).fetchone()
                job_stats[status] = row[0] if row else 0
            health["maintenance_jobs"] = job_stats
        except Exception:
            health["maintenance_jobs"] = {}

        # --- Retrieval view freshness ---
        try:
            row = conn.execute(
                "SELECT MAX(refreshed_at) FROM retrieval_view"
            ).fetchone()
            health["retrieval_view_last_refresh"] = row[0] if row and row[0] else None

            row = conn.execute(
                "SELECT COUNT(*) FROM retrieval_view"
            ).fetchone()
            health["retrieval_view_rows"] = row[0] if row else 0
        except Exception:
            health["retrieval_view_last_refresh"] = None
            health["retrieval_view_rows"] = 0

        # --- Lineage coverage ---
        try:
            row = conn.execute(
                "SELECT COUNT(DISTINCT derived_type || ':' || derived_id) FROM derived_lineage"
            ).fetchone()
            health["lineage_derived_objects"] = row[0] if row else 0

            row = conn.execute(
                "SELECT COUNT(DISTINCT source_event_id) FROM derived_lineage"
            ).fetchone()
            health["lineage_source_events"] = row[0] if row else 0
        except Exception:
            health["lineage_derived_objects"] = 0
            health["lineage_source_events"] = 0

    # --- Warnings ---
    warnings: list = []

    di = health.get("derived_invalidation", {})
    total_stale = sum(
        counts.get("stale", 0) for counts in di.values()
        if isinstance(counts, dict)
    )
    total_suspect = sum(
        counts.get("suspect", 0) for counts in di.values()
        if isinstance(counts, dict)
    )
    if total_stale > 10:
        warnings.append(f"{total_stale} stale derived objects awaiting repair")
    if total_suspect > 5:
        warnings.append(f"{total_suspect} suspect derived objects need verification")

    oc = health.get("open_conflicts", 0)
    if isinstance(oc, int) and oc > 5:
        warnings.append(f"{oc} unresolved cognitive conflicts")

    jobs = health.get("maintenance_jobs", {})
    if isinstance(jobs, dict) and jobs.get("failed", 0) > 3:
        warnings.append(f"{jobs['failed']} failed maintenance jobs")

    health["v3_warnings"] = warnings

    return health
