"""Tier promotion + demotion rules for the M2 propositional substrate.

Rules (Movement 3, encapsulated-rolling-bengio.md):

  * ``medium → high``:   ``reaffirmed_count >= 3`` AND age >= ``HIGH_MIN_AGE_DAYS``
                          AND never-superseded AND not 'avoid'.
  * ``high → canonical``: ``reaffirmed_count >= 6`` AND age >= ``CANONICAL_MIN_AGE_DAYS``
                          AND never-superseded AND not 'avoid'.
  * ``canonical`` is write-once, evict-never — no rule here touches it.
  * Demotion on contradiction lands at write time (see resolvers.py).

The thresholds are deliberately conservative. They are the substrate's
promotion criteria, not the only signal: downstream success (a canonical
fact cited in a successful task) can bump a fact to canonical faster,
and that hook lives in the cognition layer (Movement 4).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


HIGH_MIN_REAFFIRMED = 3
HIGH_MIN_AGE_DAYS = 14

CANONICAL_MIN_REAFFIRMED = 6
CANONICAL_MIN_AGE_DAYS = 60


_PROMOTION_SQL_TEMPLATE = """
UPDATE {table}
   SET tier = ?
 WHERE tier = ?
   AND (superseded_by_id IS NULL)
   AND COALESCE(reaffirmed_count, 0) >= ?
   AND (julianday('now') - julianday(created_at)) >= ?
"""


def _run_promotion(
    conn,
    *,
    table: str,
    from_tier: str,
    to_tier: str,
    min_reaffirmed: int,
    min_age_days: int,
) -> int:
    sql = _PROMOTION_SQL_TEMPLATE.format(table=table)
    cur = conn.execute(sql, (to_tier, from_tier, min_reaffirmed, min_age_days))
    return cur.rowcount if cur.rowcount is not None else 0


def run_promotion_pass(db) -> Dict[str, Dict[str, int]]:
    """Apply tier promotion rules to facts and preferences.

    Safe to run repeatedly; rows already at the target tier are skipped
    by the ``WHERE tier = from_tier`` clause. Returns a per-table count
    of rows advanced at each step so callers (CLI / consolidator / tests)
    can report meaningful deltas.
    """
    report: Dict[str, Dict[str, int]] = {}
    try:
        with db._get_connection() as conn:
            for table in ("engram_facts", "engram_preferences"):
                counts = {"medium_to_high": 0, "high_to_canonical": 0}
                try:
                    counts["medium_to_high"] = _run_promotion(
                        conn,
                        table=table,
                        from_tier="medium",
                        to_tier="high",
                        min_reaffirmed=HIGH_MIN_REAFFIRMED,
                        min_age_days=HIGH_MIN_AGE_DAYS,
                    )
                    counts["high_to_canonical"] = _run_promotion(
                        conn,
                        table=table,
                        from_tier="high",
                        to_tier="canonical",
                        min_reaffirmed=CANONICAL_MIN_REAFFIRMED,
                        min_age_days=CANONICAL_MIN_AGE_DAYS,
                    )
                except Exception as exc:
                    logger.debug("tier promotion on %s skipped: %s", table, exc)
                report[table] = counts
    except Exception as exc:
        logger.debug("tier promotion pass failed: %s", exc)
    return report


def promote_on_downstream_success(
    db,
    *,
    fact_id: str,
    table: str = "engram_facts",
) -> bool:
    """Bump a fact's tier one step on a confirmed downstream success.

    Called from the cognition layer when a policy application that cited
    this fact completes a task successfully (see Movement 4 hook). This
    is the only place a canonical-tier row is produced ahead of age —
    the substrate trusts closed-loop success as a stronger signal than
    raw reaffirmation count.
    """
    if table not in ("engram_facts", "engram_preferences"):
        return False
    try:
        with db._get_connection() as conn:
            row = conn.execute(
                f"SELECT tier, superseded_by_id FROM {table} WHERE id = ?",
                (fact_id,),
            ).fetchone()
            if row is None or row["superseded_by_id"] is not None:
                return False
            current = row["tier"] or "medium"
            next_tier = {
                "medium": "high",
                "high": "canonical",
            }.get(current)
            if next_tier is None:
                return False
            conn.execute(
                f"UPDATE {table} SET tier = ? WHERE id = ?",
                (next_tier, fact_id),
            )
            return True
    except Exception as exc:
        logger.debug("downstream promotion skipped: %s", exc)
        return False
