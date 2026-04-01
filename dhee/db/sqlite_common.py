from datetime import datetime, timezone


VALID_MEMORY_COLUMNS = frozenset({
    "memory", "metadata", "categories", "embedding", "strength",
    "layer", "tombstone", "updated_at", "related_memories", "source_memories",
    "confidentiality_scope", "source_type", "source_app", "source_event_id",
    "decay_lambda", "status", "importance", "sensitivity", "namespace",
    "access_count", "last_accessed", "immutable", "expiration_date",
    "scene_id", "user_id", "agent_id", "run_id", "app_id",
    "memory_type", "s_fast", "s_mid", "s_slow", "content_hash",
    "conversation_context", "enrichment_status",
})

VALID_SCENE_COLUMNS = frozenset({
    "title", "summary", "topic", "location", "participants", "memory_ids",
    "start_time", "end_time", "embedding", "strength", "access_count",
    "tombstone", "layer", "scene_strength", "topic_embedding_ref", "namespace",
})

VALID_PROFILE_COLUMNS = frozenset({
    "name", "profile_type", "narrative", "facts", "preferences",
    "relationships", "sentiment", "theory_of_mind", "aliases",
    "embedding", "strength", "updated_at", "role_bias", "profile_summary",
})


def _utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    """Return current UTC time as ISO string."""
    return _utcnow().isoformat()
