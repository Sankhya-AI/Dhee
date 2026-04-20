"""Background consolidator for the propositional substrate.

Movement 3 caretaker. A single entry point, ``run_consolidation``, orchestrates
three passes:

  1. **Promotion** — delegates to ``engram_tiering.run_promotion_pass``.
  2. **Dedup fusion** — collapses near-duplicates by ``canonical_key``,
     keeping the row with the highest tier / reaffirmed_count and
     merging reaffirmation counts into it.
  3. **Forgetting sweep** — ``tier='avoid'`` rows with zero reaffirmations
     and ``age > FORGET_AFTER_DAYS`` are moved to the cold archive.
     Canonical rows are **never** swept (write-once, evict-never).

The pass is idempotent and safe to run on a hot database. All mutations
happen inside transactions per table.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from dhee.core.engram_tiering import run_promotion_pass

logger = logging.getLogger(__name__)


FORGET_AFTER_DAYS = 30


def _row_to_payload(row) -> str:
    return json.dumps({k: row[k] for k in row.keys()})


def _fuse_duplicates_in(conn, table: str) -> int:
    """Collapse live-duplicate rows sharing the same ``canonical_key``.

    Keep the row with the highest tier (canonical > high > medium > low),
    falling back to highest ``reaffirmed_count``, then earliest ``created_at``.
    Superseded rows (``superseded_by_id`` not null) are out of scope — they
    already live as lineage markers, not as active duplicates.
    """
    tier_rank = (
        "CASE COALESCE(tier,'medium') "
        "WHEN 'canonical' THEN 0 "
        "WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 "
        "WHEN 'low' THEN 3 "
        "ELSE 4 END"
    )
    fused = 0
    groups = conn.execute(
        f"""SELECT canonical_key, COUNT(*) AS n FROM {table}
            WHERE superseded_by_id IS NULL
              AND COALESCE(tier,'medium') != 'avoid'
            GROUP BY canonical_key
            HAVING n > 1"""
    ).fetchall()
    for g in groups:
        rows = conn.execute(
            f"""SELECT id, COALESCE(tier,'medium') AS tier,
                       COALESCE(reaffirmed_count,0) AS rc
                FROM {table}
               WHERE canonical_key = ?
                 AND superseded_by_id IS NULL
               ORDER BY {tier_rank}, rc DESC, created_at ASC""",
            (g["canonical_key"],),
        ).fetchall()
        keeper = rows[0]
        total_reaffirmed = sum(int(r["rc"]) for r in rows)
        conn.execute(
            f"UPDATE {table} SET reaffirmed_count = ? WHERE id = ?",
            (total_reaffirmed, keeper["id"]),
        )
        for r in rows[1:]:
            # Point the discarded row at the keeper so lineage still resolves.
            conn.execute(
                f"UPDATE {table} SET superseded_by_id = ?, tier = 'avoid' "
                f"WHERE id = ?",
                (keeper["id"], r["id"]),
            )
            fused += 1
    return fused


def _sweep_forgotten(conn, table: str, archive_table: str, reason: str) -> int:
    """Move old, unreaffirmed, avoid-tier rows into the cold archive.

    Canonical rows are never swept (write-once, evict-never). The row
    payload is serialised verbatim so future schema changes don't break
    older archive entries.
    """
    candidates = conn.execute(
        f"""SELECT * FROM {table}
             WHERE COALESCE(tier,'medium') = 'avoid'
               AND COALESCE(reaffirmed_count,0) = 0
               AND (julianday('now') - julianday(created_at)) >= ?""",
        (FORGET_AFTER_DAYS,),
    ).fetchall()
    swept = 0
    for row in candidates:
        row_id = row["id"]
        payload = _row_to_payload(row)
        # Archive columns vary slightly between fact/pref tables; write
        # the common columns explicitly and stash the full payload too.
        if table == "engram_facts":
            conn.execute(
                """INSERT OR REPLACE INTO engram_fact_archive
                (id, canonical_key, memory_id, subject, predicate, value,
                 payload, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_id,
                    row["canonical_key"],
                    row["memory_id"],
                    row["subject"],
                    row["predicate"],
                    row["value"],
                    payload,
                    reason,
                ),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO engram_preference_archive
                (id, canonical_key, memory_id, user_id, subject, topic,
                 value, payload, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_id,
                    row["canonical_key"],
                    row["memory_id"],
                    row["user_id"],
                    row["subject"],
                    row["topic"],
                    row["value"],
                    payload,
                    reason,
                ),
            )
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        swept += 1
    return swept


def run_consolidation(db) -> Dict[str, Any]:
    """Run all three passes. Always returns a dict; never raises."""
    report: Dict[str, Any] = {
        "promotion": {},
        "fusion": {},
        "forgotten": {},
    }

    report["promotion"] = run_promotion_pass(db)

    try:
        with db._get_connection() as conn:
            for table in ("engram_facts", "engram_preferences"):
                try:
                    report["fusion"][table] = _fuse_duplicates_in(conn, table)
                except Exception as exc:
                    logger.debug("fusion skipped for %s: %s", table, exc)
                    report["fusion"][table] = 0
    except Exception as exc:
        logger.debug("fusion pass failed: %s", exc)

    try:
        with db._get_connection() as conn:
            report["forgotten"]["engram_facts"] = _sweep_forgotten(
                conn, "engram_facts", "engram_fact_archive",
                reason="avoid-tier TTL expired",
            )
            report["forgotten"]["engram_preferences"] = _sweep_forgotten(
                conn, "engram_preferences", "engram_preference_archive",
                reason="avoid-tier TTL expired",
            )
    except Exception as exc:
        logger.debug("forgetting sweep failed: %s", exc)

    return report
