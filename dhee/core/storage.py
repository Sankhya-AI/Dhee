"""Dhee v3 — Schema DDL for the event-sourced cognition substrate.

All tables live in a single SQLite database (v3.db). Schema is organized as:

Layer 1 — Raw truth:
    raw_memory_events   Immutable source-of-truth memory events

Layer 2 — Derived cognition (type-specific tables):
    beliefs             Confidence-tracked claims with Bayesian updates
    policies            Condition→action rules with utility tracking
    anchors             Hierarchical context (era/place/time/activity)
    insights            Synthesized causal hypotheses
    heuristics          Transferable reasoning patterns

Layer 3 — Infrastructure:
    derived_lineage     Links derived objects → source raw events
    maintenance_jobs    Cold-path job registry
    locks               SQLite lease manager for job concurrency
    cognitive_conflicts Contradiction/disagreement queue
    anchor_candidates   Per-field extraction candidates (Phase 2)
    distillation_candidates  Consolidation promotion candidates (Phase 4)

All tables use TEXT PRIMARY KEY (UUIDs), ISO timestamps, and JSON for
nested structures. Follows existing Dhee conventions from dhee/db/sqlite.py.
"""

# ---------------------------------------------------------------------------
# Layer 1: Raw truth
# ---------------------------------------------------------------------------

RAW_MEMORY_EVENTS = """
CREATE TABLE IF NOT EXISTS raw_memory_events (
    event_id            TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    session_id          TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    content             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    source              TEXT DEFAULT 'user',
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'corrected', 'deleted')),
    supersedes_event_id TEXT REFERENCES raw_memory_events(event_id),
    metadata_json       TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_rme_user_status
    ON raw_memory_events(user_id, status);
CREATE INDEX IF NOT EXISTS idx_rme_content_hash
    ON raw_memory_events(content_hash, user_id);
CREATE INDEX IF NOT EXISTS idx_rme_created
    ON raw_memory_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rme_supersedes
    ON raw_memory_events(supersedes_event_id)
    WHERE supersedes_event_id IS NOT NULL;
"""

# ---------------------------------------------------------------------------
# Layer 2: Derived cognition — type-specific tables
# ---------------------------------------------------------------------------

BELIEFS = """
CREATE TABLE IF NOT EXISTS beliefs (
    belief_id           TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    claim               TEXT NOT NULL,
    domain              TEXT DEFAULT 'general',
    status              TEXT NOT NULL DEFAULT 'proposed'
                            CHECK (status IN (
                                'proposed', 'held', 'challenged',
                                'revised', 'retracted',
                                'stale', 'suspect', 'invalidated'
                            )),
    confidence          REAL NOT NULL DEFAULT 0.5,
    evidence_json       TEXT DEFAULT '[]',
    revisions_json      TEXT DEFAULT '[]',
    contradicts_ids     TEXT DEFAULT '[]',
    source_memory_ids   TEXT DEFAULT '[]',
    source_episode_ids  TEXT DEFAULT '[]',
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    lineage_fingerprint TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    tags_json           TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_beliefs_user_domain_status
    ON beliefs(user_id, domain, status);
CREATE INDEX IF NOT EXISTS idx_beliefs_user_confidence
    ON beliefs(user_id, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_beliefs_status
    ON beliefs(status)
    WHERE status IN ('stale', 'suspect', 'invalidated');
"""

POLICIES = """
CREATE TABLE IF NOT EXISTS policies (
    policy_id           TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    name                TEXT NOT NULL,
    granularity         TEXT NOT NULL DEFAULT 'task'
                            CHECK (granularity IN ('task', 'step')),
    status              TEXT NOT NULL DEFAULT 'proposed'
                            CHECK (status IN (
                                'proposed', 'active', 'validated', 'deprecated',
                                'stale', 'suspect', 'invalidated'
                            )),
    condition_json      TEXT NOT NULL DEFAULT '{}',
    action_json         TEXT NOT NULL DEFAULT '{}',
    apply_count         INTEGER NOT NULL DEFAULT 0,
    success_count       INTEGER NOT NULL DEFAULT 0,
    failure_count       INTEGER NOT NULL DEFAULT 0,
    utility             REAL NOT NULL DEFAULT 0.0,
    last_delta          REAL NOT NULL DEFAULT 0.0,
    cumulative_delta    REAL NOT NULL DEFAULT 0.0,
    source_task_ids     TEXT DEFAULT '[]',
    source_episode_ids  TEXT DEFAULT '[]',
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    lineage_fingerprint TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    tags_json           TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_policies_user_gran_status
    ON policies(user_id, granularity, status);
CREATE INDEX IF NOT EXISTS idx_policies_user_utility
    ON policies(user_id, utility DESC);
CREATE INDEX IF NOT EXISTS idx_policies_status
    ON policies(status)
    WHERE status IN ('stale', 'suspect', 'invalidated');
"""

ANCHORS = """
CREATE TABLE IF NOT EXISTS anchors (
    anchor_id           TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    memory_event_id     TEXT REFERENCES raw_memory_events(event_id),
    era                 TEXT,
    place               TEXT,
    place_type          TEXT,
    place_detail        TEXT,
    time_absolute       TEXT,
    time_markers_json   TEXT DEFAULT '[]',
    time_range_start    TEXT,
    time_range_end      TEXT,
    time_derivation     TEXT,
    activity            TEXT,
    session_id          TEXT,
    session_position    INTEGER DEFAULT 0,
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    lineage_fingerprint TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_anchors_user_era_place
    ON anchors(user_id, era, place, activity);
CREATE INDEX IF NOT EXISTS idx_anchors_user_time
    ON anchors(user_id, time_range_start, time_range_end);
CREATE INDEX IF NOT EXISTS idx_anchors_event
    ON anchors(memory_event_id)
    WHERE memory_event_id IS NOT NULL;
"""

INSIGHTS = """
CREATE TABLE IF NOT EXISTS insights (
    insight_id          TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    content             TEXT NOT NULL,
    insight_type        TEXT NOT NULL DEFAULT 'pattern'
                            CHECK (insight_type IN (
                                'causal', 'warning', 'strategy', 'pattern'
                            )),
    source_task_types_json TEXT DEFAULT '[]',
    confidence          REAL NOT NULL DEFAULT 0.5,
    validation_count    INTEGER NOT NULL DEFAULT 0,
    invalidation_count  INTEGER NOT NULL DEFAULT 0,
    utility             REAL NOT NULL DEFAULT 0.0,
    apply_count         INTEGER NOT NULL DEFAULT 0,
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    lineage_fingerprint TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    last_validated      TEXT,
    tags_json           TEXT DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN (
                                'active', 'stale', 'suspect', 'invalidated'
                            ))
);

CREATE INDEX IF NOT EXISTS idx_insights_user_type_conf
    ON insights(user_id, insight_type, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_insights_user_utility
    ON insights(user_id, utility DESC);
CREATE INDEX IF NOT EXISTS idx_insights_status
    ON insights(status)
    WHERE status IN ('stale', 'suspect', 'invalidated');
"""

HEURISTICS = """
CREATE TABLE IF NOT EXISTS heuristics (
    heuristic_id        TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    content             TEXT NOT NULL,
    abstraction_level   TEXT NOT NULL DEFAULT 'specific'
                            CHECK (abstraction_level IN (
                                'specific', 'domain', 'universal'
                            )),
    source_task_types_json TEXT DEFAULT '[]',
    confidence          REAL NOT NULL DEFAULT 0.5,
    validation_count    INTEGER NOT NULL DEFAULT 0,
    invalidation_count  INTEGER NOT NULL DEFAULT 0,
    utility             REAL NOT NULL DEFAULT 0.0,
    last_delta          REAL NOT NULL DEFAULT 0.0,
    apply_count         INTEGER NOT NULL DEFAULT 0,
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    lineage_fingerprint TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    tags_json           TEXT DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN (
                                'active', 'stale', 'suspect', 'invalidated'
                            ))
);

CREATE INDEX IF NOT EXISTS idx_heuristics_user_level_conf
    ON heuristics(user_id, abstraction_level, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_heuristics_user_utility
    ON heuristics(user_id, utility DESC);
CREATE INDEX IF NOT EXISTS idx_heuristics_status
    ON heuristics(status)
    WHERE status IN ('stale', 'suspect', 'invalidated');
"""

# ---------------------------------------------------------------------------
# Layer 3: Infrastructure
# ---------------------------------------------------------------------------

DERIVED_LINEAGE = """
CREATE TABLE IF NOT EXISTS derived_lineage (
    lineage_id          TEXT PRIMARY KEY,
    derived_type        TEXT NOT NULL
                            CHECK (derived_type IN (
                                'belief', 'policy', 'anchor',
                                'insight', 'heuristic'
                            )),
    derived_id          TEXT NOT NULL,
    source_event_id     TEXT NOT NULL REFERENCES raw_memory_events(event_id),
    contribution_weight REAL NOT NULL DEFAULT 1.0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_lineage_derived
    ON derived_lineage(derived_type, derived_id);
CREATE INDEX IF NOT EXISTS idx_lineage_source
    ON derived_lineage(source_event_id);
"""

MAINTENANCE_JOBS = """
CREATE TABLE IF NOT EXISTS maintenance_jobs (
    job_id              TEXT PRIMARY KEY,
    job_name            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                                'pending', 'running', 'completed',
                                'failed', 'cancelled'
                            )),
    payload_json        TEXT DEFAULT '{}',
    result_json         TEXT,
    error_message       TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    started_at          TEXT,
    completed_at        TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    max_retries         INTEGER NOT NULL DEFAULT 3,
    idempotency_key     TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_name
    ON maintenance_jobs(status, job_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
    ON maintenance_jobs(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""

LOCKS = """
CREATE TABLE IF NOT EXISTS locks (
    lock_id             TEXT PRIMARY KEY,
    owner_id            TEXT NOT NULL,
    lease_expires_at    TEXT NOT NULL,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);
"""

COGNITIVE_CONFLICTS = """
CREATE TABLE IF NOT EXISTS cognitive_conflicts (
    conflict_id             TEXT PRIMARY KEY,
    conflict_type           TEXT NOT NULL
                                CHECK (conflict_type IN (
                                    'belief_contradiction',
                                    'anchor_disagreement',
                                    'distillation_conflict',
                                    'invalidation_dispute'
                                )),
    side_a_type             TEXT NOT NULL,
    side_a_id               TEXT NOT NULL,
    side_b_type             TEXT NOT NULL,
    side_b_id               TEXT NOT NULL,
    detected_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    resolution_status       TEXT NOT NULL DEFAULT 'open'
                                CHECK (resolution_status IN (
                                    'open', 'auto_resolved',
                                    'user_resolved', 'deferred'
                                )),
    resolution_json         TEXT,
    auto_resolution_confidence REAL
);

CREATE INDEX IF NOT EXISTS idx_conflicts_status
    ON cognitive_conflicts(resolution_status)
    WHERE resolution_status = 'open';
CREATE INDEX IF NOT EXISTS idx_conflicts_sides
    ON cognitive_conflicts(side_a_type, side_a_id);
"""

ANCHOR_CANDIDATES = """
CREATE TABLE IF NOT EXISTS anchor_candidates (
    candidate_id        TEXT PRIMARY KEY,
    anchor_id           TEXT NOT NULL REFERENCES anchors(anchor_id),
    field_name          TEXT NOT NULL,
    field_value         TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 0.5,
    extractor_source    TEXT NOT NULL DEFAULT 'default',
    source_event_ids    TEXT DEFAULT '[]',
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN (
                                'pending', 'accepted', 'rejected', 'superseded'
                            )),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_anchor_cand_anchor_field
    ON anchor_candidates(anchor_id, field_name, status);
"""

DISTILLATION_CANDIDATES = """
CREATE TABLE IF NOT EXISTS distillation_candidates (
    candidate_id        TEXT PRIMARY KEY,
    source_event_ids    TEXT NOT NULL DEFAULT '[]',
    derivation_version  INTEGER NOT NULL DEFAULT 1,
    confidence          REAL NOT NULL DEFAULT 0.5,
    canonical_key       TEXT,
    idempotency_key     TEXT,
    target_type         TEXT NOT NULL
                            CHECK (target_type IN (
                                'belief', 'policy', 'insight', 'heuristic'
                            )),
    payload_json        TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending_validation'
                            CHECK (status IN (
                                'pending_validation', 'promoted',
                                'rejected', 'quarantined'
                            )),
    promoted_id         TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_distill_idempotency
    ON distillation_candidates(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_distill_status
    ON distillation_candidates(status, target_type);
"""

SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS v3_schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f', 'now')),
    description TEXT
);
"""

# ---------------------------------------------------------------------------
# Ordered list of all DDL statements for initialization
# ---------------------------------------------------------------------------

ALL_SCHEMAS = [
    # Version tracking
    SCHEMA_VERSION,
    # Layer 1
    RAW_MEMORY_EVENTS,
    # Layer 2
    BELIEFS,
    POLICIES,
    ANCHORS,
    INSIGHTS,
    HEURISTICS,
    # Layer 3
    DERIVED_LINEAGE,
    MAINTENANCE_JOBS,
    LOCKS,
    COGNITIVE_CONFLICTS,
    ANCHOR_CANDIDATES,
    DISTILLATION_CANDIDATES,
]

CURRENT_VERSION = 1


def initialize_schema(conn: "sqlite3.Connection") -> None:
    """Create all v3 tables if they don't exist.

    Idempotent — safe to call on every startup.
    """
    for ddl in ALL_SCHEMAS:
        conn.executescript(ddl)

    # Record schema version (idempotent)
    existing = conn.execute(
        "SELECT 1 FROM v3_schema_version WHERE version = ?",
        (CURRENT_VERSION,),
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO v3_schema_version (version, description) VALUES (?, ?)",
            (CURRENT_VERSION, "Initial v3 event-sourced substrate"),
        )
    conn.commit()
