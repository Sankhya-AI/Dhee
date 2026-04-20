"""Epistemic Control Loop primitives (M3).

``last_verified_at`` is the "world did not push back" signal. It is bumped
when a fact is cited by a context bundle and a downstream task completes
without user correction. Substrate-level helpers live here so the caller
(buddhi, MetaBuddhi, MCP handlers) never hand-rolls SQL against the verify
column.

Staleness thresholds are intentionally generous — Dhee prefers "check again
before acting" to "silently forget". A fact whose ``last_verified_at`` is
older than the TTL is not wrong; it is *unconfirmed*. The agent adds an
``epistemic_check`` step to the HyperContext rather than refusing to use it.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


DEFAULT_VERIFY_TTL_DAYS = 14
LOAD_BEARING_VERIFY_TTL_DAYS = 7


def mark_verified(
    db,
    *,
    fact_id: str,
    table: str = "engram_facts",
    now_epoch: Optional[float] = None,
) -> bool:
    """Stamp ``last_verified_at`` on a fact or preference row.

    Call from the cognition layer when a policy/task successfully grounds
    on this fact. No-op if the row is superseded or the table is wrong.
    """
    if table not in ("engram_facts", "engram_preferences"):
        return False
    now_epoch = now_epoch if now_epoch is not None else time.time()
    try:
        with db._get_connection() as conn:
            cur = conn.execute(
                f"UPDATE {table} SET last_verified_at = ? "
                f"WHERE id = ? AND superseded_by_id IS NULL",
                (now_epoch, fact_id),
            )
            return (cur.rowcount or 0) > 0
    except Exception as exc:
        logger.debug("mark_verified failed: %s", exc)
        return False


def staleness_days(row: Dict[str, Any], *, now_epoch: Optional[float] = None) -> Optional[float]:
    """Days since last verification, or None if never verified."""
    now_epoch = now_epoch if now_epoch is not None else time.time()
    last = row.get("last_verified_at")
    if last is None:
        return None
    try:
        return max(0.0, (now_epoch - float(last)) / 86400.0)
    except (TypeError, ValueError):
        return None


def needs_epistemic_check(
    row: Dict[str, Any],
    *,
    load_bearing: bool = False,
    ttl_days: Optional[float] = None,
    now_epoch: Optional[float] = None,
) -> bool:
    """Decide whether this row should trigger an ``epistemic_check`` step.

    Canonical-tier rows are treated as trusted unless explicitly load-bearing
    and also past the shorter TTL. A never-verified row on a load-bearing
    path always returns True (caller should verify before acting).
    """
    tier = (row.get("tier") or "medium").lower()
    if ttl_days is None:
        ttl_days = LOAD_BEARING_VERIFY_TTL_DAYS if load_bearing else DEFAULT_VERIFY_TTL_DAYS

    if tier == "canonical" and not load_bearing:
        return False

    days = staleness_days(row, now_epoch=now_epoch)
    if days is None:
        return load_bearing  # never-verified + load-bearing → always check
    return days >= float(ttl_days)


def pending_epistemic_checks(
    db,
    *,
    user_id: Optional[str] = None,
    limit: int = 5,
    load_bearing: bool = False,
    now_epoch: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return active engram_facts whose ``last_verified_at`` is past the TTL.

    Canonical rows on non-load-bearing paths are excluded to keep the
    HyperContext bundle focused on facts that actually need reverification.
    Never-verified rows are included only when ``load_bearing=True`` so
    ordinary context bundles don't nag about brand-new medium-tier facts.

    Rows come back sorted by staleness (oldest verification first), with
    ``None``-verified rows sorted last — caller truncates to ``limit``.
    """
    ttl = LOAD_BEARING_VERIFY_TTL_DAYS if load_bearing else DEFAULT_VERIFY_TTL_DAYS
    now_epoch = now_epoch if now_epoch is not None else time.time()
    rows: List[Dict[str, Any]] = []
    try:
        sql = (
            "SELECT ef.id, ef.subject, ef.predicate, ef.value, ef.tier, "
            "       ef.last_verified_at, ef.memory_id "
            "  FROM engram_facts ef "
        )
        params: List[Any] = []
        if user_id is not None:
            sql += "  JOIN memories m ON m.id = ef.memory_id "
        sql += "WHERE ef.superseded_by_id IS NULL "
        sql += "  AND COALESCE(ef.tier, 'medium') != 'avoid' "
        if user_id is not None:
            sql += "  AND m.user_id = ? "
            params.append(user_id)
        with db._get_connection() as conn:
            for raw in conn.execute(sql, tuple(params)).fetchall():
                row = dict(raw)
                if not needs_epistemic_check(
                    row, load_bearing=load_bearing, now_epoch=now_epoch
                ):
                    continue
                row["staleness_days"] = staleness_days(row, now_epoch=now_epoch)
                rows.append(row)
    except Exception as exc:
        logger.debug("pending_epistemic_checks failed: %s", exc)
        return []

    def _key(r: Dict[str, Any]):
        # Oldest last_verified first; never-verified (None) sorts last.
        d = r.get("staleness_days")
        return (0, -d) if d is not None else (1, 0)

    rows.sort(key=_key)
    return rows[: max(0, int(limit))]
