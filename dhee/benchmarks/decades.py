"""Decades longevity eval — substrate holds up after years of writes.

Movement 3 claims three load-bearing properties of the propositional
substrate:

  1. **Canonical-tier rows persist forever.** Once a fact reaches the
     ``canonical`` tier (via age+reaffirmation or downstream-success
     bump), no sweep ever touches it. Write-once, evict-never.
  2. **Supersede chains stay explorable.** When a fact is superseded,
     the old row is not deleted — it's demoted to ``avoid`` with
     ``superseded_by_id`` pointing forward. Even after the forgetting
     sweep lands the old row in ``engram_fact_archive``, the chain
     remains resolvable.
  3. **Recall latency degrades gracefully.** A 10K-row substrate should
     not be dramatically slower to query by ``canonical_key`` than a
     1K-row substrate; the active-row index is the whole point.

This eval measures all three on a synthetic corpus generated in one
pass so the numbers are reproducible. It's a *load test*, not a
quality benchmark — unlike the replay corpus (which must come from
real user activity), a "does the substrate survive 3 years of writes"
question has no honest answer from live data yet.

Pass thresholds match the Movement 3 verification line:

  * ``canonical_retention >= 1.0``
  * ``supersede_chain_integrity >= 1.0``
  * ``latency_degradation <= 2.0`` (p50 at 10K ÷ p50 at 1K)

CLI: ``dhee decades-eval [--events N] [--json]``.
"""

from __future__ import annotations

import logging
import os
import random
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


CANONICAL_RETENTION_REQUIRED = 1.0
SUPERSEDE_INTEGRITY_REQUIRED = 1.0
MAX_LATENCY_DEGRADATION = 2.0


@dataclass
class DecadesConfig:
    """Knobs for the synthetic corpus."""

    total_events: int = 10000
    supersede_fraction: float = 0.20
    canonical_fraction: float = 0.10
    span_days: int = 365 * 3
    latency_samples: int = 200
    seed: int = 42


@dataclass
class DecadesScorecard:
    """Result surface — every number is measured, none are assumed."""

    config: DecadesConfig
    total_facts_written: int = 0
    canonical_rows: int = 0
    canonical_retained_after_sweep: int = 0
    canonical_retention: float = 0.0
    supersede_chains: int = 0
    supersede_chain_integrity: float = 0.0
    archived_old_rows: int = 0
    latency_1k_p50_ms: float = 0.0
    latency_10k_p50_ms: float = 0.0
    latency_degradation: float = 0.0
    passed: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["config"] = asdict(self.config)
        return d


# ---------------------------------------------------------------------------
# Corpus generation
# ---------------------------------------------------------------------------


def _new_db(data_dir: str):
    from dhee.db.sqlite import SQLiteManager

    path = os.path.join(data_dir, "decades.db")
    return SQLiteManager(db_path=path)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _seed_memory_row(conn, memory_id: str, user_id: str, created_at: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO memories (id, memory, user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (memory_id, f"decades-eval fact {memory_id}", user_id, created_at, created_at),
    )


def _insert_fact(
    conn,
    *,
    fact_id: str,
    memory_id: str,
    subject: str,
    predicate: str,
    value: str,
    canonical_key: str,
    tier: str,
    created_at: str,
    reaffirmed_count: int = 0,
    superseded_by_id: Optional[str] = None,
    valid_until: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO engram_facts
        (id, memory_id, subject, predicate, value, canonical_key, confidence,
         is_derived, tier, reaffirmed_count, superseded_by_id, valid_until,
         created_at, schema_v)
        VALUES (?, ?, ?, ?, ?, ?, 1.0, 0, ?, ?, ?, ?, ?, 1)""",
        (
            fact_id, memory_id, subject, predicate, value, canonical_key,
            tier, reaffirmed_count, superseded_by_id, valid_until,
            created_at,
        ),
    )


def _generate_corpus(db, cfg: DecadesConfig) -> Dict[str, Any]:
    """Populate a fresh DB with a time-skewed fact population.

    Structure:
      * ``n_supersede_chains`` subjects get two rows — earlier at
        ``tier='avoid'`` (old, superseded) + later at ``tier='medium'``
        (current). The earlier row is backdated far enough that the
        forgetting sweep will move it into the archive, exercising the
        "chain survives sweep" property.
      * ``n_canonical`` rows are pre-stamped ``tier='canonical'`` with
        ``reaffirmed_count`` well past threshold and backdated beyond
        ``FORGET_AFTER_DAYS``. The consolidator must not touch them.
      * Remaining rows are plain ``tier='medium'`` with varied ages.
    """
    rng = random.Random(cfg.seed)
    user_id = "decades-user"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    n_chains = int(cfg.total_events * cfg.supersede_fraction) // 2
    n_canonical = int(cfg.total_events * cfg.canonical_fraction)
    n_plain = cfg.total_events - (n_chains * 2) - n_canonical
    if n_plain < 0:
        n_plain = 0

    chain_ids: List[Dict[str, str]] = []

    with db._get_connection() as conn:
        # Supersede chains
        for i in range(n_chains):
            subject = f"user"
            predicate = f"worked_on_{i}"
            old_value = f"project_alpha_{i}"
            new_value = f"project_beta_{i}"
            canonical = f"user|{predicate}|"
            # Old row: far enough back to be swept-eligible
            old_age_days = rng.randint(60, cfg.span_days)
            old_created = now - timedelta(days=old_age_days)
            new_age_days = max(1, old_age_days - rng.randint(30, 90))
            new_created = now - timedelta(days=new_age_days)
            old_id = f"decades-fact-old-{i}"
            new_id = f"decades-fact-new-{i}"
            old_mem = f"decades-mem-old-{i}"
            new_mem = f"decades-mem-new-{i}"
            _seed_memory_row(conn, old_mem, user_id, _iso(old_created))
            _seed_memory_row(conn, new_mem, user_id, _iso(new_created))
            _insert_fact(
                conn, fact_id=old_id, memory_id=old_mem,
                subject=subject, predicate=predicate, value=old_value,
                canonical_key=canonical + old_value,
                tier="avoid", reaffirmed_count=0,
                superseded_by_id=new_id,
                valid_until=_iso(new_created),
                created_at=_iso(old_created),
            )
            _insert_fact(
                conn, fact_id=new_id, memory_id=new_mem,
                subject=subject, predicate=predicate, value=new_value,
                canonical_key=canonical + new_value,
                tier="medium", reaffirmed_count=0,
                created_at=_iso(new_created),
            )
            chain_ids.append({
                "old": old_id, "new": new_id,
                "old_canonical": canonical + old_value,
                "new_canonical": canonical + new_value,
            })

        # Canonical rows — pre-promoted, must never be evicted
        canonical_ids: List[str] = []
        for i in range(n_canonical):
            age_days = rng.randint(120, cfg.span_days)
            created = now - timedelta(days=age_days)
            fid = f"decades-fact-canonical-{i}"
            mid = f"decades-mem-canonical-{i}"
            _seed_memory_row(conn, mid, user_id, _iso(created))
            _insert_fact(
                conn, fact_id=fid, memory_id=mid,
                subject="user",
                predicate=f"core_belief_{i}",
                value=f"stance_{i}",
                canonical_key=f"user|core_belief_{i}|stance_{i}",
                tier="canonical", reaffirmed_count=12,
                created_at=_iso(created),
            )
            canonical_ids.append(fid)

        # Plain medium-tier rows — varied ages, uncontended canonical keys
        for i in range(n_plain):
            age_days = rng.randint(0, cfg.span_days)
            created = now - timedelta(days=age_days)
            fid = f"decades-fact-plain-{i}"
            mid = f"decades-mem-plain-{i}"
            _seed_memory_row(conn, mid, user_id, _iso(created))
            _insert_fact(
                conn, fact_id=fid, memory_id=mid,
                subject=f"user_{i % 97}",
                predicate=f"pref_{i % 53}",
                value=f"val_{i}",
                canonical_key=f"user_{i % 97}|pref_{i % 53}|val_{i}",
                tier="medium", reaffirmed_count=rng.randint(0, 2),
                created_at=_iso(created),
            )

    return {
        "chain_ids": chain_ids,
        "canonical_ids": canonical_ids,
        "n_plain": n_plain,
    }


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _chain_still_explorable(
    conn, *, old_id: str, new_id: str,
) -> bool:
    """After consolidator sweep, the chain must still resolve.

    The successor (new_id) MUST be active in engram_facts with
    ``superseded_by_id IS NULL``. The predecessor (old_id) may either:
      * still be in engram_facts with ``tier='avoid'`` and
        ``superseded_by_id = new_id``, OR
      * have moved to ``engram_fact_archive`` (sweep fired) — in
        which case the archived payload must still carry a pointer
        to the successor.
    """
    new_row = conn.execute(
        "SELECT superseded_by_id FROM engram_facts WHERE id = ?",
        (new_id,),
    ).fetchone()
    if new_row is None or new_row["superseded_by_id"] is not None:
        return False

    live_old = conn.execute(
        "SELECT superseded_by_id, tier FROM engram_facts WHERE id = ?",
        (old_id,),
    ).fetchone()
    if live_old is not None:
        return (live_old["superseded_by_id"] == new_id
                and (live_old["tier"] or "") == "avoid")

    archived = conn.execute(
        "SELECT payload FROM engram_fact_archive WHERE id = ?",
        (old_id,),
    ).fetchone()
    if archived is None:
        return False
    import json

    try:
        payload = json.loads(archived["payload"])
    except Exception:
        return False
    return payload.get("superseded_by_id") == new_id


def _measure_lookup_latency(
    db, canonical_keys: List[str], samples: int,
) -> float:
    """p50 of SELECT-by-canonical_key query time (ms) over the active index."""
    if not canonical_keys:
        return 0.0
    timings: List[float] = []
    with db._get_connection() as conn:
        for key in canonical_keys[:samples]:
            start = time.perf_counter()
            conn.execute(
                "SELECT id FROM engram_facts "
                "WHERE canonical_key = ? AND superseded_by_id IS NULL "
                "LIMIT 1",
                (key,),
            ).fetchone()
            timings.append((time.perf_counter() - start) * 1000.0)
    if not timings:
        return 0.0
    return statistics.median(timings)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_decades_eval(
    *,
    data_dir: Optional[str] = None,
    config: Optional[DecadesConfig] = None,
) -> DecadesScorecard:
    """Drive a full Movement-3 longevity scorecard end-to-end.

    Args:
      data_dir: where to place the temporary SQLite DBs. Uses a
        tempdir when omitted.
      config: override any of the corpus knobs.
    """
    from dhee.core.engram_consolidator import run_consolidation

    cfg = config or DecadesConfig()
    work_dir = data_dir or tempfile.mkdtemp(prefix="dhee-decades-")
    main_dir = os.path.join(work_dir, "main")
    os.makedirs(main_dir, exist_ok=True)

    scorecard = DecadesScorecard(config=cfg)

    # ------------------------------------------------------------------
    # 1) Populate the main DB.
    # ------------------------------------------------------------------
    db = _new_db(main_dir)
    corpus = _generate_corpus(db, cfg)
    scorecard.supersede_chains = len(corpus["chain_ids"])
    scorecard.canonical_rows = len(corpus["canonical_ids"])

    with db._get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM engram_facts"
        ).fetchone()["n"]
    scorecard.total_facts_written = int(total)

    # ------------------------------------------------------------------
    # 2) Run the consolidator (promotion + fusion + forgetting sweep).
    # ------------------------------------------------------------------
    run_consolidation(db)

    # ------------------------------------------------------------------
    # 3) Canonical retention — every pre-stamped canonical row must
    #    still be in engram_facts with tier='canonical'.
    # ------------------------------------------------------------------
    retained = 0
    with db._get_connection() as conn:
        for cid in corpus["canonical_ids"]:
            row = conn.execute(
                "SELECT tier FROM engram_facts WHERE id = ?",
                (cid,),
            ).fetchone()
            if row is not None and (row["tier"] or "") == "canonical":
                retained += 1
    scorecard.canonical_retained_after_sweep = retained
    scorecard.canonical_retention = (
        retained / scorecard.canonical_rows if scorecard.canonical_rows else 1.0
    )

    # ------------------------------------------------------------------
    # 4) Supersede-chain integrity — every chain stays resolvable.
    # ------------------------------------------------------------------
    intact = 0
    archived_old = 0
    with db._get_connection() as conn:
        for chain in corpus["chain_ids"]:
            if _chain_still_explorable(
                conn, old_id=chain["old"], new_id=chain["new"],
            ):
                intact += 1
            archived = conn.execute(
                "SELECT 1 FROM engram_fact_archive WHERE id = ?",
                (chain["old"],),
            ).fetchone()
            if archived is not None:
                archived_old += 1
    scorecard.supersede_chain_integrity = (
        intact / scorecard.supersede_chains
        if scorecard.supersede_chains else 1.0
    )
    scorecard.archived_old_rows = archived_old

    # ------------------------------------------------------------------
    # 5) Latency degradation — p50 at 10K vs p50 on a fresh 1K-row DB.
    # ------------------------------------------------------------------
    ref_dir = os.path.join(work_dir, "ref1k")
    os.makedirs(ref_dir, exist_ok=True)
    ref_cfg = DecadesConfig(
        total_events=min(1000, cfg.total_events),
        supersede_fraction=cfg.supersede_fraction,
        canonical_fraction=cfg.canonical_fraction,
        span_days=cfg.span_days,
        latency_samples=cfg.latency_samples,
        seed=cfg.seed + 1,
    )
    ref_db = _new_db(ref_dir)
    ref_corpus = _generate_corpus(ref_db, ref_cfg)

    def _probe_keys(db_handle) -> List[str]:
        with db_handle._get_connection() as c:
            rows = c.execute(
                "SELECT canonical_key FROM engram_facts "
                "WHERE superseded_by_id IS NULL LIMIT ?",
                (cfg.latency_samples,),
            ).fetchall()
        return [r["canonical_key"] for r in rows]

    scorecard.latency_1k_p50_ms = _measure_lookup_latency(
        ref_db, _probe_keys(ref_db), cfg.latency_samples,
    )
    scorecard.latency_10k_p50_ms = _measure_lookup_latency(
        db, _probe_keys(db), cfg.latency_samples,
    )
    if scorecard.latency_1k_p50_ms > 0:
        scorecard.latency_degradation = (
            scorecard.latency_10k_p50_ms / scorecard.latency_1k_p50_ms
        )
    else:
        # Sub-microsecond 1k baseline — below timer resolution. Report
        # the 10k number against a 1µs floor so we don't divide by zero
        # or claim an infinite slowdown.
        scorecard.latency_degradation = scorecard.latency_10k_p50_ms / 0.001
        scorecard.notes.append(
            "1k latency floored at 1µs (below perf_counter resolution)"
        )

    # Silence reference corpus var so linters don't complain.
    _ = ref_corpus

    # ------------------------------------------------------------------
    # 6) Decide pass/fail honestly.
    # ------------------------------------------------------------------
    failed: List[str] = []
    if scorecard.canonical_retention < CANONICAL_RETENTION_REQUIRED:
        failed.append(
            f"canonical_retention={scorecard.canonical_retention:.3f} "
            f"< {CANONICAL_RETENTION_REQUIRED}"
        )
    if scorecard.supersede_chain_integrity < SUPERSEDE_INTEGRITY_REQUIRED:
        failed.append(
            f"supersede_chain_integrity="
            f"{scorecard.supersede_chain_integrity:.3f} "
            f"< {SUPERSEDE_INTEGRITY_REQUIRED}"
        )
    if scorecard.latency_degradation > MAX_LATENCY_DEGRADATION:
        failed.append(
            f"latency_degradation={scorecard.latency_degradation:.2f}x "
            f"> {MAX_LATENCY_DEGRADATION}x"
        )
    scorecard.passed = not failed
    if failed:
        scorecard.notes.extend(failed)

    return scorecard
