from __future__ import annotations

import pytest

from dhee import CoreMemory, Engram
from dhee.configs.base import MemoryConfig
from dhee.core.conflict import ConflictResolution
from dhee.core.samskara import SamskaraCollector
from dhee.core.viveka import Viveka
from dhee.fs import ContextWorkspace
from dhee.memory.write_pipeline import MemoryWritePipeline


def test_explicit_chotu_goal_becomes_canonical_personal_memory(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = memory.add(
        "My goal is for Chotu to become a proactive personal assistant that remembers preferences, decisions, and product philosophy.",
        user_id="default",
        metadata={"explicit_remember": True},
        infer=False,
    )

    stored = result["results"][0]
    loaded = memory.get(stored["id"])

    assert loaded["namespace"] == "canonical_personal"
    assert loaded["memory_type"] == "semantic"
    assert loaded["layer"] == "lml"
    assert loaded["strength"] >= 0.92
    assert loaded["metadata"]["retention_policy"] == "durable"
    assert loaded["metadata"]["policy_explicit"] is True
    assert loaded["metadata"]["canonical_kind"] == "goal"
    memory.close()


def test_explicit_canonical_kind_wins_over_text_reinference(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = memory.add(
        "Dhee must never supersede or demote a good Chotu profile, style, goal, preference, or decision memory until replacement is verified.",
        user_id="default",
        metadata={"explicit_remember": True, "canonical_kind": "decision"},
        infer=False,
    )["results"][0]

    loaded = memory.get(result["id"])
    assert loaded["metadata"]["canonical_kind"] == "decision"
    memory.close()


def test_passive_and_test_noise_are_isolated_from_personal_recall(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    canonical = memory.add(
        "My goal is for Chotu to preserve my assistant preferences and major decisions.",
        user_id="default",
        metadata={"explicit_remember": True},
        infer=False,
    )["results"][0]
    passive = memory.add(
        "Chotu observed visible screen activity. App: Chrome. Visible text: personal assistant videos and UI widgets.",
        user_id="default",
        agent_id="chotu",
        source_app="chotu",
        metadata={
            "source": "chotu_screen_memory",
            "type": "screen_activity",
            "retention_policy": "durable",
            "confidence": 0.95,
            "evidence": {"kind": "screen_context", "dwell_seconds": 120},
        },
        infer=False,
    )["results"][0]
    fixture = memory.add("Memory 9", user_id="default", infer=False)["results"][0]

    loaded_passive = memory.get(passive["id"])
    loaded_fixture = memory.get(fixture["id"])
    assert loaded_passive["namespace"] == "passive_screen"
    assert loaded_passive["metadata"]["dhee_memory_class"] == "passive_screen"
    assert loaded_fixture["namespace"] == "test"
    assert loaded_fixture["memory_type"] == "test_fixture"
    assert loaded_fixture["strength"] <= 0.05

    results = memory.memory.search(
        "What do you remember about Chotu personal assistant goals preferences decisions?",
        user_id="default",
        limit=5,
    )["results"]
    result_ids = [row["id"] for row in results]
    assert canonical["id"] in result_ids
    assert passive["id"] not in result_ids
    assert fixture["id"] not in result_ids
    assert results[0]["memory_class"] == "canonical_personal"
    assert results[0]["recall_explanation"]["matched_memory_id"] == canonical["id"]
    memory.close()


def test_operational_file_touch_is_evidence_not_personal_recall(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    canonical = memory.add(
        "My goal is for Chotu to preserve assistant preferences and major decisions.",
        user_id="default",
        metadata={"explicit_remember": True},
        infer=False,
    )["results"][0]
    operational = memory.add(
        "edited /Users/example/project/src/app.py",
        user_id="default",
        metadata={"kind": "file_touched", "tool": "Edit", "success": True},
        infer=False,
    )["results"][0]

    loaded = memory.get(operational["id"])
    assert loaded["namespace"] == "operational"
    assert loaded["memory_type"] == "operational_event"
    assert loaded["strength"] <= 0.05
    assert loaded["metadata"]["suppress_from_default_recall"] is True

    personal_results = memory.memory.search(
        "What do you remember about Chotu assistant goals preferences decisions?",
        user_id="default",
        limit=5,
    )["results"]
    assert [row["id"] for row in personal_results] == [canonical["id"]]

    operational_results = memory.memory.search(
        "recent edits file touched operational",
        user_id="default",
        limit=5,
        min_strength=0.0,
    )["results"]
    assert operational["id"] in [row["id"] for row in operational_results]
    memory.close()


def test_placeholder_memory_signatures_are_test_fixtures(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    placeholders = [
        "Test content 29422d88",
        "Test memory about programming",
        "Agent memory",
        "Important fact",
        "Memory one",
        "Memory two",
        "Memory item 0",
        "Persistent memory",
        "Default user memory",
        "I like Python",
        "I like Python 14cdcdeb",
        "Unique content xyz123",
        "Some data to search",
        "Data for eviction test",
        "preserve_7763eefd",
        "second_c3277279",
        "History test 14cdcdeb",
        "Caching is good",
        "To be deleted",
        "Original content",
        "Updated content",
        "Hello World",
    ]
    ids = [
        memory.add(content, user_id="default", infer=False)["results"][0]["id"]
        for content in placeholders
    ]

    for memory_id in ids:
        loaded = memory.get(memory_id)
        assert loaded["namespace"] == "test"
        assert loaded["memory_type"] == "test_fixture"
        assert loaded["strength"] <= 0.05
    memory.close()


def test_core_memory_admission_applies_quality_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path))
    memory = CoreMemory(preset="minimal")
    stored = memory.add("Data for eviction test", user_id="default")["results"][0]

    loaded = memory.get(stored["id"])
    assert loaded["namespace"] == "test"
    assert loaded["memory_type"] == "test_fixture"
    assert loaded["layer"] == "sml"
    assert loaded["strength"] <= 0.05
    assert loaded["metadata"]["suppress_from_default_recall"] is True
    memory.close()


def test_ordinary_memory_does_not_promote_after_repeated_access(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    stored = memory.add(
        "Plain note about coffee machine calibration and bench readings.",
        user_id="default",
        infer=False,
    )["results"][0]

    for _ in range(5):
        results = memory.memory.search(
            "coffee machine calibration",
            user_id="default",
            limit=1,
        )["results"]
        assert results and results[0]["id"] == stored["id"]

    loaded = memory.get(stored["id"])
    assert loaded["access_count"] >= 5
    assert loaded["layer"] == "sml"
    assert loaded["metadata"]["dhee_memory_class"] == "ordinary"
    memory.close()


def test_canonical_recall_falls_back_to_db_when_vector_is_missing(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.memory.db.add_memory(
        {
            "id": "db-only-chotu-goal",
            "memory": "My goal is for Chotu to preserve assistant preferences and major decisions.",
            "user_id": "default",
            "metadata": {
                "dhee_memory_class": "canonical_personal",
                "canonical_personal": True,
                "canonical_kind": "goal",
                "policy_explicit": True,
                "retention_policy": "durable",
            },
            "namespace": "canonical_personal",
            "memory_type": "semantic",
            "layer": "lml",
            "strength": 0.95,
        }
    )

    results = memory.memory.search(
        "What do you remember about Chotu assistant goals preferences decisions?",
        user_id="default",
        limit=3,
    )["results"]

    assert results
    assert results[0]["id"] == "db-only-chotu-goal"
    assert results[0]["memory_class"] == "canonical_personal"
    assert results[0]["recall_explanation"]["matched_memory_id"] == "db-only-chotu-goal"
    memory.close()


def test_repair_memory_quality_can_reindex_db_only_vectors(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory_id = "legacy-vectorless-chotu-goal"
    memory.memory.db.add_memory(
        {
            "id": memory_id,
            "memory": "My goal is for Chotu to preserve assistant preferences and major decisions.",
            "user_id": "default",
            "metadata": {
                "dhee_memory_class": "canonical_personal",
                "canonical_personal": True,
                "canonical_kind": "goal",
                "policy_explicit": True,
                "retention_policy": "durable",
            },
            "namespace": "canonical_personal",
            "memory_type": "semantic",
            "layer": "lml",
            "strength": 0.95,
            "embedding": [0.01] * 384,
        }
    )

    assert memory.memory.vector_store.list(filters={"memory_id": memory_id}) == []

    dry_run = memory.repair_memory_quality(
        user_id="default",
        dry_run=True,
        reindex_vectors=True,
    )
    assert dry_run["vector_repair"]["missing_vector"] == 1
    assert memory.memory.vector_store.list(filters={"memory_id": memory_id}) == []

    repair = memory.repair_memory_quality(
        user_id="default",
        dry_run=False,
        reindex_vectors=True,
    )
    assert repair["vector_repair"]["repair_count"] == 1
    vectors = memory.memory.vector_store.list(filters={"memory_id": memory_id})
    assert len(vectors) == 1
    assert vectors[0].id == memory_id
    assert vectors[0].payload["namespace"] == "canonical_personal"
    assert vectors[0].payload["canonical_kind"] == "goal"
    memory.close()


def test_suppressed_noise_is_not_strength_boosted_on_explicit_debug_recall(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    stored = memory.add("Memory 0", user_id="default", infer=False)["results"][0]

    results = memory.memory.search(
        "debug test fixture memory 0",
        user_id="default",
        limit=1,
        min_strength=0.0,
    )["results"]
    loaded = memory.get(stored["id"])

    assert results and results[0]["id"] == stored["id"]
    assert loaded["strength"] <= 0.05
    memory.close()


def test_successful_retrieval_records_precision_not_recall_miss(tmp_path):
    collector = SamskaraCollector(log_dir=str(tmp_path / "samskaras"))
    viveka = Viveka(samskara_collector=collector)

    assessment = viveka.assess_retrieval(
        "What are Chotu goals?",
        [{"id": "m1", "memory": "Chotu goal is reliable personal assistant recall.", "score": 0.9}],
        user_id="default",
    )
    signals = collector.get_training_signals()["vasana_report"]

    assert assessment.is_aklishta
    assert assessment.memory_ids == ["m1"]
    assert signals["retrieval_precision"]["count"] == 1
    assert signals["retrieval_precision"]["strength"] > 0
    assert signals["retrieval_recall"]["count"] == 0


class _FakeScope:
    def normalize_agent_category(self, value):
        return value

    def normalize_connector_id(self, value):
        return value

    def infer_scope(self, **kwargs):
        return "user" if kwargs.get("policy_explicit") else "agent"


class _FakeEmbedder:
    def embed(self, *_args, **_kwargs):
        return [1.0, 0.0, 0.0]

    def embed_batch(self, texts, **_kwargs):
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeDB:
    def __init__(self):
        self.rows = {}
        self.deleted = []
        self.updates = []

    def add_memory(self, row):
        self.rows[row["id"]] = dict(row)
        return row["id"]

    def delete_memory(self, memory_id, use_tombstone=True):
        self.deleted.append((memory_id, use_tombstone))
        self.rows.pop(memory_id, None)

    def update_memory(self, memory_id, updates):
        self.updates.append((memory_id, dict(updates)))
        self.rows.setdefault(memory_id, {}).update(updates)
        return True

    def increment_access(self, _memory_id):
        return None

    def get_memory_by_content_hash(self, *_args, **_kwargs):
        return None

    def log_event(self, *_args, **_kwargs):
        return None


class _VectorStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.inserted = []

    def insert(self, *, vectors, payloads, ids):
        if self.fail:
            raise RuntimeError("vector write timed out")
        self.inserted.append((vectors, payloads, ids))


def _pipeline(vector_store, db, demote_calls):
    return MemoryWritePipeline(
        db=db,
        embedder=_FakeEmbedder(),
        llm=object(),
        config=MemoryConfig(),
        vector_store=vector_store,
        scope_resolver=_FakeScope(),
        record_cost_fn=lambda **_kwargs: None,
        forget_by_query_fn=lambda *_args, **_kwargs: {"deleted_count": 0, "deleted_ids": []},
        demote_existing_fn=lambda memory, **kwargs: demote_calls.append((memory, kwargs)),
        nearest_memory_fn=lambda *_args, **_kwargs: (
            {
                "id": "old",
                "memory": "User prefers concise technical writing.",
                "strength": 0.8,
                "layer": "lml",
                "metadata": {"dhee_memory_class": "project_context"},
            },
            0.99,
        ),
    )


def test_supersede_is_deferred_until_replacement_vector_write_succeeds(monkeypatch):
    db = _FakeDB()
    demote_calls = []
    pipeline = _pipeline(_VectorStore(fail=True), db, demote_calls)
    monkeypatch.setattr(
        "dhee.memory.write_pipeline.resolve_conflict",
        lambda *_args, **_kwargs: ConflictResolution("CONTRADICTORY", 1.0),
    )

    with pytest.raises(RuntimeError):
        pipeline.process_single_memory(
            mem={"content": "User prefers verbose technical writing.", "metadata": {}},
            processed_metadata={},
            effective_filters={"user_id": "default"},
            categories=None,
            user_id="default",
            agent_id="chotu",
            run_id=None,
            app_id=None,
            agent_category=None,
            connector_id=None,
            scope=None,
            source_app="chotu",
            immutable=False,
            expiration_date=None,
            initial_layer="auto",
            initial_strength=1.0,
            echo_depth=None,
        )

    assert demote_calls == []
    assert db.deleted and db.deleted[0][1] is False


def test_supersede_records_replacement_only_after_success(monkeypatch):
    db = _FakeDB()
    demote_calls = []
    pipeline = _pipeline(_VectorStore(fail=False), db, demote_calls)
    monkeypatch.setattr(
        "dhee.memory.write_pipeline.resolve_conflict",
        lambda *_args, **_kwargs: ConflictResolution("CONTRADICTORY", 1.0),
    )

    result = pipeline.process_single_memory(
        mem={"content": "User prefers verbose technical writing.", "metadata": {}},
        processed_metadata={},
        effective_filters={"user_id": "default"},
        categories=None,
        user_id="default",
        agent_id="chotu",
        run_id=None,
        app_id=None,
        agent_category=None,
        connector_id=None,
        scope=None,
        source_app="chotu",
        immutable=False,
        expiration_date=None,
        initial_layer="auto",
        initial_strength=1.0,
        echo_depth=None,
    )

    assert result["event"] == "UPDATE"
    assert demote_calls
    assert demote_calls[0][1]["superseded_by"] == result["id"]


def test_stats_repair_state_and_sources_surface_canonical_memory(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.add(
        "My goal is for Chotu to be a dependable personal assistant brain.",
        user_id="default",
        metadata={"explicit_remember": True},
        infer=False,
    )
    memory.add("Cache test", user_id="default", infer=False)

    stats = memory.stats(user_id="default")
    assert stats["quality"]["canonical_personal_count"] == 1
    assert stats["quality"]["test_fixture_count"] == 1

    repair = memory.repair_memory_quality(user_id="default", dry_run=True)
    assert repair["scanned_count"] >= 2

    workspace = ContextWorkspace(
        repo=str(tmp_path),
        user_id="default",
        agent_id="pytest",
        db=memory.memory.db,
    )
    state_md = workspace.read("/state/current.md")
    sources = workspace.read("/sources")
    canonical_sources = workspace.read("/sources/memory/canonical.md")

    assert "- goal: unset" not in state_md
    assert "Chotu" in state_md
    assert "memory/" in sources
    assert "Canonical Personal Memory Sources" in canonical_sources
    assert "dependable personal assistant brain" in canonical_sources
    memory.close()


def test_agent_health_includes_shared_canonical_personal_model(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.add(
        "My goal is for Chotu to be a dependable personal assistant brain.",
        user_id="default",
        metadata={"explicit_remember": True},
        infer=False,
    )
    memory.add(
        "Chotu observed visible screen activity. App: Chrome. Visible text: personal assistant videos and UI widgets.",
        user_id="default",
        agent_id="chotu",
        source_app="chotu",
        metadata={
            "source": "chotu_screen_memory",
            "type": "screen_activity",
            "retention_policy": "durable",
            "confidence": 0.95,
            "evidence": {"kind": "screen_context", "dwell_seconds": 120},
        },
        infer=False,
    )

    stats = memory.memory.get_stats(user_id="default", agent_id="chotu")
    assert stats["total"] == 2
    assert stats["lml_count"] == 1
    assert stats["quality"]["canonical_personal_count"] == 1
    assert stats["quality"]["passive_screen_count"] == 1
    assert stats["quality"]["shared_personal_count"] == 1
    assert "canonical_personal_count is zero" not in stats["quality"]["warnings"]

    audit = memory.audit_memory_quality(
        user_id="default",
        agent_id="chotu",
        profile_keyword="chotu",
    )
    assert audit["ready"] is True
    assert audit["status"] == "ready"
    assert audit["counts"]["canonical_personal"] == 1
    assert audit["counts"]["passive_screen"] == 1
    assert audit["counts"]["canonical_profile_matches"] == 1
    memory.close()


def test_raw_evidence_gets_structured_distillation_metadata(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = memory.add(
        "# Chotu Product Philosophy\n\nDecision: Chotu should remember explicit goals, preferences, and constraints before acting proactively.",
        user_id="default",
        metadata={
            "source_type": "markdown",
            "artifact_id": "artifact-doc-1",
            "source_path": "README.md",
            "content_hash": "abc123",
        },
        infer=False,
    )["results"][0]

    loaded = memory.get(result["id"])
    distillation = loaded["metadata"]["evidence_distillation"]

    assert loaded["namespace"] == "evidence"
    assert loaded["memory_type"] == "episodic"
    assert loaded["metadata"]["raw_evidence"] is True
    assert loaded["metadata"]["evidence_kind"] == "markdown"
    assert distillation["decision_relevance"] == "high"
    assert distillation["actionability"] in {"medium", "high"}
    assert distillation["source_quality"] == "high"
    assert "Chotu" in distillation["entities"]
    assert "goals" in distillation["topics"]
    memory.close()


def test_audit_and_apply_repair_clear_legacy_chotu_memory_quality_failures(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    db = memory.memory.db
    db.add_memory(
        {
            "id": "legacy-goal",
            "memory": "My goal is for Chotu to be a dependable personal assistant brain.",
            "user_id": "default",
            "metadata": {"policy_explicit": True},
            "namespace": "default",
            "memory_type": "episodic",
            "layer": "sml",
            "strength": 0.09,
        }
    )
    db.add_memory(
        {
            "id": "legacy-test",
            "memory": "Unique content xyz123",
            "user_id": "default",
            "metadata": {},
            "namespace": "default",
            "memory_type": "semantic",
            "layer": "sml",
            "strength": 1.0,
        }
    )
    db.add_memory(
        {
            "id": "legacy-malformed",
            "memory": "ordinary legacy row with malformed metadata",
            "user_id": "default",
            "metadata": {},
            "namespace": "default",
            "memory_type": "semantic",
            "layer": "sml",
            "strength": 0.8,
        }
    )
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE memories SET metadata = ? WHERE id = ?",
            ('["legacy", "metadata"]', "legacy-malformed"),
        )

    before = memory.audit_memory_quality(user_id="default", profile_keyword="chotu")
    assert before["ready"] is False
    assert before["counts"]["unpromoted_canonical"] == 1
    assert before["counts"]["unresolved_test_noise"] == 1
    assert before["counts"]["damaged_canonical"] == 1

    repair = memory.repair_memory_quality(user_id="default", dry_run=False)
    assert repair["canonical_promoted"] == 1
    assert repair["test_isolated"] == 1
    assert repair["malformed_metadata_normalized"] == 1

    after = memory.audit_memory_quality(user_id="default", profile_keyword="chotu")
    assert after["ready"] is True
    assert after["status"] == "ready"
    assert after["counts"]["canonical_personal"] == 1
    assert after["counts"]["canonical_profile_matches"] == 1
    assert after["counts"]["unresolved_test_noise"] == 0
    assert after["counts"]["damaged_canonical"] == 0

    repaired_goal = memory.get("legacy-goal")
    repaired_test = memory.get("legacy-test")
    repaired_malformed = memory.get("legacy-malformed")
    assert repaired_goal["namespace"] == "canonical_personal"
    assert repaired_goal["layer"] == "lml"
    assert repaired_goal["strength"] >= 0.92
    assert repaired_test["namespace"] == "test"
    assert repaired_test["memory_type"] == "test_fixture"
    assert repaired_test["strength"] <= 0.05
    assert repaired_malformed["metadata"]["legacy_metadata_raw"] == ["legacy", "metadata"]
    assert repaired_malformed["metadata"]["legacy_metadata_type"] == "list"
    memory.close()


def test_failed_tool_rows_are_operational_noise_not_personal_truth(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.memory.db.add_memory(
        {
            "id": "legacy-bash-failure",
            "memory": "bash failed: pytest tests/test_login.py — FAILED: missing exp claim",
            "user_id": "default",
            "metadata": {
                "kind": "failure",
                "source": "claude_code_hook",
                "tool": "Bash",
                "success": False,
            },
            "namespace": "default",
            "memory_type": "semantic",
            "layer": "lml",
            "strength": 0.98,
        }
    )

    before = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert before["ready"] is False
    assert before["counts"]["unresolved_operational_noise"] == 1
    assert before["counts"]["unapproved_lml"] == 1

    repair = memory.repair_memory_quality(user_id="default", dry_run=False)
    assert repair["operational_isolated"] == 1

    loaded = memory.get("legacy-bash-failure")
    assert loaded["namespace"] == "operational"
    assert loaded["memory_type"] == "operational_event"
    assert loaded["layer"] == "sml"
    assert loaded["strength"] <= 0.05
    assert loaded["metadata"]["suppress_from_default_recall"] is True

    after = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert after["ready"] is True
    assert after["counts"]["unresolved_operational_noise"] == 0
    assert after["counts"]["unapproved_lml"] == 0
    memory.close()


def test_audit_and_repair_clean_contaminated_profile_anchors(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.memory.db.add_profile(
        {
            "id": "profile-bash-failed",
            "user_id": "default",
            "name": "bash failed",
            "profile_type": "contact",
            "facts": ["pytest tests/test_login.py — FAILED: missing exp claim"],
            "preferences": [],
            "strength": 1.0,
        }
    )

    before = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert before["ready"] is False
    assert before["counts"]["profile_contamination"] == 1

    repair = memory.repair_memory_quality(user_id="default", dry_run=False)
    assert repair["profiles_cleaned"] == 1

    cleaned = memory.memory.db.get_profile("profile-bash-failed")
    assert cleaned["facts"] == []
    assert cleaned["preferences"] == []
    assert cleaned["strength"] == 0.0
    assert memory.memory.profile_processor.search_profiles(
        query="pytest login failure",
        user_id="default",
        limit=5,
    ) == []

    after = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert after["ready"] is True
    assert after["counts"]["profile_contamination"] == 0
    memory.close()


def test_repair_makes_command_only_profile_anchor_inert(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.memory.db.add_profile(
        {
            "id": "profile-ran",
            "user_id": "default",
            "name": "ran",
            "profile_type": "contact",
            "facts": [
                "git -C /Users/example/project show abc123 --stat | head -60",
                "sqlite3 ~/.dhee/handoff.db \"SELECT name FROM sqlite_master\"",
                "curl -H \"Authorization: [REDACTED]\" https://api.example.com",
                "OUT=$(echo '{\"prompt\":\"fix login\"}' | python -m dhee.hooks.claude_code UserPromptSubmit)",
            ],
            "preferences": [],
            "strength": 1.0,
        }
    )

    before = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert before["counts"]["profile_contamination"] == 1

    repair = memory.repair_memory_quality(user_id="default", dry_run=False)
    assert repair["profiles_cleaned"] == 1

    cleaned = memory.memory.db.get_profile("profile-ran")
    assert cleaned["facts"] == []
    assert cleaned["strength"] == 0.0
    after = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert after["ready"] is True
    assert after["counts"]["profile_contamination"] == 0
    memory.close()


def test_repair_renames_doc_heading_profile_when_clean_alias_exists(tmp_path):
    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.memory.db.add_profile(
        {
            "id": "profile-dhee-heading",
            "user_id": "default",
            "name": "Dhee Native Integration\n\nDhee",
            "profile_type": "contact",
            "facts": ["Dhee serves as memory/context router"],
            "preferences": ["memory", "context"],
            "aliases": ["Dhee"],
            "strength": 1.0,
        }
    )

    before = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert before["counts"]["profile_contamination"] == 1

    repair = memory.repair_memory_quality(user_id="default", dry_run=False)
    assert repair["profiles_cleaned"] == 1

    cleaned = memory.memory.db.get_profile("profile-dhee-heading")
    assert cleaned["name"] == "Dhee"
    assert cleaned["facts"] == ["Dhee serves as memory/context router"]
    assert cleaned["strength"] == 1.0
    after = memory.audit_memory_quality(user_id="default", require_personal_model=False)
    assert after["ready"] is True
    assert after["counts"]["profile_contamination"] == 0
    memory.close()


def test_orchestrated_search_uses_repo_handoff_before_semantic_policy_collision(tmp_path, monkeypatch):
    from dhee.memory import orchestration as orchestration_module

    repo = str(tmp_path / "repo")
    session = {
        "id": "handoff-hero",
        "agent_id": "codex",
        "repo": repo,
        "status": "completed",
        "task_summary": "Replaced the Dhee README hero with a Sankhya homepage-style PNG and pushed to main.",
        "decisions": [
            "Replaced README hero reference from docs/dhee-hero.svg to docs/dhee-hero.png.",
            "Removed docs/dhee-flow.svg and docs/dhee-impact.svg.",
        ],
        "files_touched": ["README.md", "docs/dhee-hero.png"],
        "todos": ["PyPI 7.0.0 upload remains blocked until credentials are available."],
        "updated": "2026-05-20T17:06:03+00:00",
        "source": "bus_session",
    }
    monkeypatch.setattr(
        orchestration_module,
        "_load_repo_handoff",
        lambda repo, user_id, agent_id: session,
    )

    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.add(
        "Dhee must never supersede or demote a good Chotu profile, style, goal, preference, or decision memory until the replacement memory has been successfully stored and verified.",
        user_id="default",
        metadata={"explicit_remember": True, "canonical_kind": "decision"},
        infer=False,
    )

    result = memory.memory.search_orchestrated(
        query="What happened in the latest Dhee README hero replacement and what PyPI blocker remains?",
        user_id="default",
        repo=repo,
        orchestration_mode="hybrid",
        question_type="repo_continuity",
        limit=3,
    )

    top = result["results"][0]
    assert top["memory_class"] == "repo_continuity"
    assert "docs/dhee-hero.png" in top["memory"]
    assert "PyPI 7.0.0 upload remains blocked" in top["memory"]
    assert "repo_handoff_included" in result["reason_codes"]
    memory.close()


def test_repo_handoff_retrieval_prefers_query_relevant_session_over_latest(tmp_path, monkeypatch):
    from dhee.memory import orchestration as orchestration_module

    repo = str(tmp_path / "repo")
    latest_unrelated = {
        "id": "handoff-chotu",
        "agent_id": "codex",
        "repo": repo,
        "status": "completed",
        "task_summary": "Analyzed whether the Chotu native Dhee integration is architecturally right.",
        "decisions": ["No code changes were made."],
        "files_touched": [],
        "todos": [],
    }
    older_relevant = {
        "id": "handoff-hero",
        "agent_id": "codex",
        "repo": repo,
        "status": "completed",
        "task_summary": "Replaced the Dhee README hero with a Sankhya homepage-style PNG and pushed to main.",
        "decisions": ["Replaced README hero reference from docs/dhee-hero.svg to docs/dhee-hero.png."],
        "files_touched": ["README.md", "docs/dhee-hero.png"],
        "todos": ["PyPI 7.0.0 upload remains blocked until credentials are available."],
    }
    monkeypatch.setattr(
        orchestration_module,
        "_load_repo_handoff_candidates",
        lambda repo, user_id, agent_id, limit=50: [latest_unrelated, older_relevant],
    )
    monkeypatch.setattr(
        orchestration_module,
        "_load_repo_handoff",
        lambda repo, user_id, agent_id: latest_unrelated,
    )

    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    result = memory.memory.search_orchestrated(
        query="What happened in the latest Dhee README hero replacement and what PyPI blocker remains?",
        user_id="default",
        repo=repo,
        orchestration_mode="hybrid",
        question_type="repo_continuity",
        limit=3,
    )

    top = result["results"][0]
    assert top["id"] == "session:handoff-hero"
    assert "docs/dhee-hero.png" in top["memory"]
    assert "PyPI 7.0.0 upload remains blocked" in top["memory"]
    memory.close()


def test_think_rejects_wrong_canonical_policy_when_repo_handoff_answers(tmp_path, monkeypatch):
    from dhee.memory import orchestration as orchestration_module

    repo = str(tmp_path / "repo")
    monkeypatch.setattr(
        orchestration_module,
        "_load_repo_handoff",
        lambda repo, user_id, agent_id: {
            "id": "handoff-hero",
            "agent_id": "codex",
            "repo": repo,
            "status": "completed",
            "task_summary": "Replaced the Dhee README hero with docs/dhee-hero.png.",
            "decisions": ["Used the Sankhya homepage visual style for the hero image."],
            "files_touched": ["README.md", "docs/dhee-hero.png"],
            "todos": ["PyPI 7.0.0 upload remains blocked until credentials are available."],
        },
    )

    memory = Engram(provider="mock", in_memory=True, data_dir=str(tmp_path))
    memory.add(
        "Dhee must never supersede or demote a good Chotu profile, style, goal, preference, or decision memory until the replacement memory has been successfully stored and verified.",
        user_id="default",
        metadata={"explicit_remember": True, "canonical_kind": "decision"},
        infer=False,
    )

    result = memory.memory.think(
        "What happened in the latest Dhee README hero replacement and what blocker remains?",
        user_id="default",
        repo=repo,
    )

    assert "docs/dhee-hero.png" in result.answer
    assert "PyPI 7.0.0 upload remains blocked" in result.answer
    assert result.grounded_facts[0].memory_ids == ["session:handoff-hero"]
    memory.close()
