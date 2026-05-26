import sqlite3

from dhee.db.sqlite import SQLiteManager
from dhee.memory.narrative_scene import NarrativeSceneService


class FakeSceneEmbedder:
    model = "fake-nemotron-embed"

    def _vec(self, text):
        text = (text or "").lower()
        return [
            float(text.count("proof") + text.count("bundle")),
            float(text.count("sqlite")),
            float(text.count("scene") + text.count("runtime")),
            float(text.count("calendar") + text.count("dinner")),
        ]

    def embed(self, text, memory_action=None):
        return self._vec(text)

    def embed_batch(self, texts, memory_action=None):
        return [self._vec(text) for text in texts]


class FakeSceneReranker:
    model = "fake-nemotron-rerank"

    def rerank(self, query, passages, top_n=0):
        rows = []
        for index, passage in enumerate(passages):
            text = passage.lower()
            logit = 8.0 if "proof" in text and "sqlite" in text else -2.0
            rows.append({"index": index, "logit": logit, "text": passage})
        rows.sort(key=lambda row: row["logit"], reverse=True)
        return rows[:top_n] if top_n else rows


class FakeRollupLLM:
    model = "google/gemma-4-31b-it"

    def __init__(self):
        self.prompts = []

    def set_purpose(self, purpose):
        self.purpose = purpose

    def generate(self, prompt):
        self.prompts.append(prompt)
        if '"scope_type": "series"' in prompt:
            return """
            {
              "arc_summary": "LLM series arc: Dhee is becoming Chotu's CTO-grade narrative operating layer.",
              "active_tensions": ["Convert memory from recall into reliable anticipation."],
              "latest_signal": "Series strategy upgraded by scene-card evidence.",
              "open_threads": ["Wire Chotu runtime to scene lifecycle tools."],
              "resolved_threads": [],
              "likely_next_steps": ["Use narrative priors before coding actions."],
              "contradictions": [],
              "evidence_card_ids": [],
              "confidence": 0.82
            }
            """
        if '"scope_type": "season"' in prompt:
            return """
            {
              "arc_summary": "LLM season arc: normalized SceneCards are replacing prompt sludge.",
              "active_tensions": ["Keep proof gates ahead of code mutation."],
              "latest_signal": "Season signal distilled from SceneCards.",
              "open_threads": ["Finish Chotu integration."],
              "resolved_threads": [],
              "likely_next_steps": ["Expose advisory SceneCards to Chotu."],
              "contradictions": [],
              "evidence_card_ids": [],
              "confidence": 0.8
            }
            """
        return """
        {
          "arc_summary": "LLM episode arc: SceneCard lifecycle created auditable narrative evidence.",
          "active_tensions": ["Do not let raw transcripts become prompt state."],
          "latest_signal": "Episode signal distilled from the latest SceneCard.",
          "open_threads": ["Verify LLM rollup storage."],
          "resolved_threads": [],
          "likely_next_steps": ["Keep deterministic fallback available."],
          "contradictions": [],
          "evidence_card_ids": [],
          "confidence": 0.78
        }
        """


EXPECTED_NARRATIVE_TABLES = {
    "series",
    "seasons",
    "story_characters",
    "episodes",
    "episode_characters",
    "scene_characters",
    "scene_events",
    "scene_cards",
    "scene_card_claims",
    "scene_categories",
    "scene_markers",
    "scene_edges",
}


def _table_names(db):
    with db._get_connection() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _scene_columns(db):
    with db._get_connection() as conn:
        rows = conn.execute("PRAGMA table_info(scenes)").fetchall()
    return {row["name"] for row in rows}


def test_narrative_schema_migrates_fresh_and_legacy_databases(tmp_path):
    fresh = SQLiteManager(str(tmp_path / "fresh.db"))
    assert EXPECTED_NARRATIVE_TABLES <= _table_names(fresh)
    assert {
        "episode_id",
        "agent_category",
        "hero_character_id",
        "action_lane",
        "outcome_status",
        "visibility_scope",
        "privacy_class",
        "consolidated_card_id",
    } <= _scene_columns(fresh)
    for table in ("series", "seasons", "episodes"):
        with fresh._get_connection() as conn:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        columns = {row["name"] for row in rows}
        assert {
            "deterministic_rollup_json",
            "llm_rollup_json",
            "rollup_model",
            "rollup_prompt_version",
            "rollup_source_scene_card_ids_json",
            "rollup_input_hash",
        } <= columns

    legacy_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy_path)
    conn.execute(
        """
        CREATE TABLE scenes (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            title TEXT,
            summary TEXT,
            topic TEXT,
            location TEXT,
            participants TEXT DEFAULT '[]',
            memory_ids TEXT DEFAULT '[]',
            start_time TEXT,
            end_time TEXT,
            embedding TEXT,
            strength REAL DEFAULT 1.0,
            access_count INTEGER DEFAULT 0,
            tombstone INTEGER DEFAULT 0
        )
        """
    )
    conn.close()

    legacy = SQLiteManager(str(legacy_path))
    assert EXPECTED_NARRATIVE_TABLES <= _table_names(legacy)
    assert {"episode_id", "action_lane", "consolidated_card_json"} <= _scene_columns(legacy)


def test_scene_lifecycle_creates_cto_series_episode_events_and_card(tmp_path):
    db = SQLiteManager(str(tmp_path / "lifecycle.db"))
    service = NarrativeSceneService(db)

    start = service.scene_start(
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        source_app="codex",
        namespace="repo:dhee",
        query="Design Dhee scene runtime with proof-gated SceneCards",
        intent_type="architecture_design",
        action_lane="planning",
        categories=["Dhee", "scene-runtime"],
        markers={"phase": "implementation"},
    )

    scene_id = start["scene"]["id"]
    assert start["series"]["theme"] == "Become a successful CTO"
    assert start["episode"]["primary_hero_id"] == start["hero"]["id"]
    assert start["scene"]["episode_id"] == start["episode"]["id"]
    assert db.get_scene_categories(scene_id) == ["dhee", "scene_runtime"]
    assert db.get_scene_markers(scene_id)["phase"] == ["implementation"]

    event = service.scene_event(
        scene_id=scene_id,
        event_type="tool_evidence",
        summary="Inspected normalized SQLite schema and SceneCard retrieval proof gates.",
        evidence_ref="file:dhee/db/sqlite.py",
    )
    assert event["event"]["summary"]
    assert "raw_transcript" not in event["event"]

    end = service.scene_end(
        scene_id=scene_id,
        outcome="SceneCard created for canonical retrieval.",
        outcome_status="success",
        story_progress_delta="Dhee now has a normalized retrieval object for Chotu.",
        durable_facts=["Dhee SceneCards are the canonical retrieval object for scene intelligence."],
        decisions=["Prefer SQLite SceneCards over JSONL temporal scenes when both exist."],
        procedures=["Retrieve SceneCards before raw transcript evidence."],
        success_patterns=["Prompt-safe evidence refs keep Chotu context compact."],
        promote_durable_facts=True,
    )
    assert end["card"]["scene_id"] == scene_id
    assert end["scene"]["consolidated_card_id"] == end["card"]["id"]
    assert end["card"]["evidence_refs"][0]["ref"] == "file:dhee/db/sqlite.py"
    assert end["card"]["durable_facts"] == [
        "Dhee SceneCards are the canonical retrieval object for scene intelligence."
    ]
    assert end["episode"]["scene_ids"] == [scene_id]
    assert "SceneCard created for canonical retrieval." in end["episode"]["key_decisions"]
    assert end["episode"]["category_summaries"]["scene_runtime"]
    assert "normalized retrieval object" in end["episode"]["story_progress"]
    rolled_season = db.get_season(start["season"]["id"])
    assert "scene_runtime:" in " ".join(rolled_season["open_threads"])
    assert "Latest episode" in rolled_season["arc_summary"]
    rolled_series = db.get_series(start["series"]["id"])
    assert "Active season" in rolled_series["arc_summary"]
    assert rolled_series["latest_season_signal"]
    assert rolled_series["active_tensions"]
    assert end["promoted_memory_ids"]
    promoted = db.get_memory(end["promoted_memory_ids"][0])
    assert promoted["memory_type"] == "semantic"
    assert promoted["layer"] == "lml"
    assert promoted["source_type"] == "scene_card"
    assert promoted["metadata"]["kind"] == "scene_card_durable_fact"
    assert "raw_transcript" not in str(end["card"])


def test_scene_event_rejects_unknown_scene_ids(tmp_path):
    db = SQLiteManager(str(tmp_path / "event-guard.db"))
    service = NarrativeSceneService(db)

    result = service.scene_event(
        scene_id="scene_budget_missing",
        event_type="worker_result",
        summary="This event should not be orphaned.",
    )

    assert result == {"error": "scene not found"}
    with db._get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM scene_events").fetchone()[0]
    assert count == 0


def test_scene_card_summary_prefers_story_over_evidence_labels(tmp_path):
    db = SQLiteManager(str(tmp_path / "summary-quality.db"))
    service = NarrativeSceneService(db)

    start = service.scene_start(
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        source_app="codex",
        namespace="repo:dhee",
        query="Store proactive Chotu memory philosophy",
        intent_type="memory_consolidation",
        action_lane="planning",
        categories=["chotu", "dhee", "memory_quality"],
    )

    end = service.scene_end(
        scene_id=start["scene"]["id"],
        outcome="Stored proactive memory philosophy.",
        outcome_status="success",
        story_progress_delta="Dhee now preserves Chotu's proactive-agent purpose.",
        durable_facts=["Chotu uses Dhee memory to anticipate user needs."],
        evidence=[{"type": "memory_id", "id": "mem-1"}],
    )

    assert end["card"]["summary"].startswith("Dhee now preserves Chotu's proactive-agent purpose.")
    assert "Chotu uses Dhee memory to anticipate user needs." in end["card"]["summary"]
    assert "scene evidence:" not in end["card"]["summary"][:80]


def test_scene_end_distills_episode_season_and_series_rollups_with_llm(tmp_path):
    db = SQLiteManager(str(tmp_path / "llm-rollups.db"))
    rollup_llm = FakeRollupLLM()
    service = NarrativeSceneService(db, rollup_llm=rollup_llm)

    start = service.scene_start(
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        source_app="codex",
        namespace="repo:dhee",
        query="Implement auditable LLM narrative rollups",
        intent_type="architecture_implementation",
        action_lane="planning",
        categories=["scene_runtime"],
    )
    service.scene_event(
        scene_id=start["scene"]["id"],
        summary="Added deterministic evidence for LLM rollup distillation.",
        evidence_ref="ptr:rollup-evidence",
    )
    end = service.scene_end(
        scene_id=start["scene"]["id"],
        outcome="LLM rollup path implemented.",
        outcome_status="success",
        story_progress_delta="Dhee can now distill narrative arcs from SceneCards.",
    )

    episode = db.get_episode(start["episode"]["id"])
    season = db.get_season(start["season"]["id"])
    series = db.get_series(start["series"]["id"])

    assert episode["story_progress"].startswith("LLM episode arc:")
    assert episode["deterministic_rollup"]["source"] == "deterministic_scene_card_rollup"
    assert episode["llm_rollup"]["arc_summary"].startswith("LLM episode arc:")
    assert episode["rollup_model"] == "google/gemma-4-31b-it"
    assert episode["rollup_prompt_version"] == "dhee.narrative_rollup.v1"
    assert end["card"]["id"] in episode["rollup_source_scene_card_ids"]

    assert season["arc_summary"].startswith("LLM season arc:")
    assert season["llm_rollup"]["open_threads"] == ["Finish Chotu integration."]
    assert end["card"]["id"] in season["rollup_source_scene_card_ids"]

    assert series["arc_summary"].startswith("LLM series arc:")
    assert series["latest_season_signal"] == "Series strategy upgraded by scene-card evidence."
    assert "Convert memory from recall into reliable anticipation." in series["active_tensions"]
    assert end["card"]["id"] in series["rollup_source_scene_card_ids"]

    assert len(rollup_llm.prompts) == 3
    assert all("raw_transcript" not in prompt for prompt in rollup_llm.prompts)


def test_chotu_kimi_scene_packets_become_prompt_safe_consolidation_evidence(tmp_path):
    db = SQLiteManager(str(tmp_path / "chotu-kimi.db"))
    service = NarrativeSceneService(db)

    start = service.scene_start(
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:chotu",
        query="Improve Sankhya hero image with Kimi runtime",
        action_lane="code_mutation",
        categories=["chotu", "dhee", "kimi", "scene_runtime"],
    )
    event = service.scene_event(
        scene_id=start["scene"]["id"],
        event_type="worker_result",
        payload={
            "run_id": "run-1",
            "status": "completed",
            "payload": {"summary": "Kimi updated the hero image candidate."},
        },
    )
    assert event["event"]["summary"] == "Kimi updated the hero image candidate."
    assert event["event"]["metadata"]["payload"]["status"] == "completed"

    consolidation_payload = {
        "schema_version": "chotu.dhee_scene_consolidation_input.v1",
        "id": "dhee_scene_consolidation_abc123",
        "outcome": "owner_accepted",
        "final_outcome": {
            "worker_result": {
                "id": "worker-result-1",
                "status": "completed",
                "summary": "Updated the Sankhya hero image and verified the focused checks.",
            },
            "latest_quality_gate": {"passed": True},
            "owner_feedback": [{"accepted": True, "feedback": "ship it"}],
        },
        "provisional_evidence": {
            "kimi_trace": {
                "status": "ready_for_scene_consolidation",
                "event_count": 4,
                "thinking_event_count": 1,
                "tool_names": ["dhee_scene_context"],
            }
        },
        "prompt_causality": {
            "enabled": True,
            "prompt_sha256": "prompt-hash",
            "action_packet_id": "rapkt-1",
            "section_hashes": [{"title": "Deep Dhee-aware Kimi Runtime", "sha256": "section-hash"}],
        },
        "evidence_refs": [{"kind": "kimi_provisional_trace", "path": "/tmp/kimi-events.jsonl"}],
        "truth_model": {
            "thinking_events_are_provisional": True,
            "verification_and_final_outcome_win": True,
            "raw_transcripts_included": False,
        },
        "do_not_promote": ["raw_thinking", "unverified_intermediate_claims"],
        "promote_candidates": ["prompt patterns correlated with useful reasoning"],
    }
    end = service.scene_end(
        scene_id=start["scene"]["id"],
        outcome="owner_accepted",
        outcome_status="success",
        evidence=[
            {"kind": "owner_feedback", "summary": "owner accepted the result", "id": "feedback-1"},
            {
                "kind": "chotu_scene_consolidation_input",
                "summary": "Chotu scene ended with verified worker output.",
                "payload": consolidation_payload,
            },
        ],
    )

    card = end["card"]
    assert "Kimi updated the hero image candidate." in card["summary"]
    assert any(ref["kind"] == "chotu_scene_consolidation_input" for ref in card["evidence_refs"])
    assert any(ref["kind"] == "kimi_provisional_trace" for ref in card["evidence_refs"])
    assert any("provisional evidence" in item for item in card["procedures"])
    assert any("prompt section hashes" in item for item in card["procedures"])
    assert any("Verification and final outcome won" in item for item in card["success_patterns"])
    assert any("Kimi thinking is useful trace evidence" in item for item in card["failure_patterns"])
    saved = db.get_scene_card(card["id"])
    assert any(item["kind"] == "prompt_causality" for item in saved["artifacts"])
    assert "raw_thinking" in saved["do_not_use_for"]

    advisory = service.scene_context(
        query="Improve Sankhya hero image with Kimi runtime",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:chotu",
        categories=["chotu", "dhee", "kimi"],
        action_lane="code_mutation",
        has_task_contract=False,
        has_proof_bundle=False,
    )
    assert advisory["included_cards"]
    assert advisory["retrieval_policy"]["proof_gate"] == "blocked"
    assert advisory["retrieval_policy"]["card_use"] == "advisory_prior_only"
    assert advisory["included_cards"][0]["use_policy"] == "advisory_prior_only_not_mutation_proof"


def test_default_series_and_hero_are_scoped_per_user(tmp_path):
    db = SQLiteManager(str(tmp_path / "users.db"))
    service = NarrativeSceneService(db)

    default_start = service.scene_start(user_id="default", agent_id="chotu", query="default user scene")
    other_start = service.scene_start(user_id="user-2", agent_id="chotu", query="other user scene")

    assert default_start["series"]["id"] != other_start["series"]["id"]
    assert default_start["hero"]["id"] != other_start["hero"]["id"]
    assert default_start["series"]["theme"] == other_start["series"]["theme"]
    assert other_start["hero"]["stable_identity_ref"] == "user-2"


def test_scene_context_filters_and_cross_agent_category_reuse(tmp_path):
    db = SQLiteManager(str(tmp_path / "retrieval.db"))
    service = NarrativeSceneService(db)

    start = service.scene_start(
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        namespace="repo:dhee",
        query="Build scene runtime retrieval for Chotu",
        action_lane="planning",
        categories=["scene_runtime"],
    )
    service.scene_event(
        scene_id=start["scene"]["id"],
        summary="Scene runtime retrieval should pass compact SceneCards to Chotu.",
        evidence_ref="ptr:scene-runtime",
    )
    service.scene_end(
        scene_id=start["scene"]["id"],
        outcome="Reusable SceneCard ready.",
        outcome_status="success",
    )

    context = service.scene_context(
        query="Chotu needs scene runtime retrieval context",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
        limit=3,
    )
    assert context["included_cards"]
    assert context["cross_agent_scenes"]
    assert context["current_episode_id"] == start["episode"]["id"]
    assert context["same_episode_scenes"]
    assert "category_overlap:1" in context["included_cards"][0]["included_reasons"]
    assert context["included_cards"][0]["retrieval_score"] > 0
    assert context["retrieval_policy"]["raw_transcripts_included"] is False
    prior = service.narrative_prior(
        query="Chotu needs scene runtime retrieval context",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
    )
    assert prior["episode_id"] == start["episode"]["id"]
    assert prior["evidence_scene_cards"]
    assert any(item["source"] == "scene_card" for item in prior["anticipation_trace"])
    assert prior["proof_gate_status"] == "not_required"
    assert prior["advisory_only"] is True
    assert prior["scene_tension"]["episode_story_progress"]
    assert prior["scene_tension"]["series_arc_summary"]
    assert prior["scene_tension"]["series_active_tensions"]
    assert prior["scene_tension"]["season_arc_summary"]
    assert prior["scene_tension"]["season_open_threads"]
    assert any(item.startswith("Aim series arc:") for item in prior["likely_next_beats"])
    assert any(item.startswith("Advance season arc:") for item in prior["likely_next_beats"])
    assert any(item.startswith("Continue episode arc:") for item in prior["likely_next_beats"])

    blocked_prior = service.narrative_prior(
        query="mutate scene runtime code",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
        action_lane="code_mutation",
        has_task_contract=False,
        has_proof_bundle=False,
    )
    assert blocked_prior["proof_gate_status"] == "blocked"
    assert blocked_prior["evidence_scene_cards"]
    assert blocked_prior["guardrails"][0].startswith("Code mutation is blocked")

    secret_scene_id = db.add_scene(
        {
            "user_id": "default",
            "title": "secret",
            "summary": "Secret scene runtime note",
            "topic": "scene runtime",
            "namespace": "repo:dhee",
        }
    )
    db.replace_scene_categories(secret_scene_id, ["scene_runtime"])
    db.upsert_scene_card(
        {
            "id": "secret-card",
            "scene_id": secret_scene_id,
            "user_id": "default",
            "agent_id": "codex",
            "agent_category": "coding_agent",
            "namespace": "repo:dhee",
            "summary": "Secret scene runtime retrieval details.",
            "retrieval_tags": ["scene_runtime"],
            "visibility_scope": "category",
            "privacy_class": "secret",
            "importance": 1.0,
        }
    )
    filtered = service.scene_context(
        query="scene runtime retrieval",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
        limit=5,
    )
    assert any(item["reason"] == "privacy_class_blocked" for item in filtered["rejected"])

    gated = service.scene_context(
        query="scene runtime retrieval",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
        action_lane="code_mutation",
        has_task_contract=False,
        has_proof_bundle=False,
    )
    assert gated["included_cards"]
    assert gated["retrieval_policy"]["proof_gate"] == "blocked"
    assert gated["included_cards"][0]["use_policy"] == "advisory_prior_only_not_mutation_proof"

    proofed = service.scene_context(
        query="scene runtime retrieval",
        user_id="default",
        agent_id="chotu",
        agent_category="personal_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
        action_lane="code_mutation",
        has_task_contract=True,
        has_proof_bundle=True,
    )
    assert proofed["included_cards"]


def test_scene_context_uses_configured_embedding_and_reranker(tmp_path):
    db = SQLiteManager(str(tmp_path / "semantic-retrieval.db"))
    service = NarrativeSceneService(
        db,
        embedder=FakeSceneEmbedder(),
        reranker=FakeSceneReranker(),
    )

    proof_scene = service.scene_start(
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        namespace="repo:dhee",
        query="Build proof bundle SQLite retrieval for SceneCards",
        action_lane="planning",
        categories=["scene_runtime"],
    )
    service.scene_end(
        scene_id=proof_scene["scene"]["id"],
        outcome="Proof bundle SQLite retrieval path is ready.",
        outcome_status="success",
        decisions=["Use proof bundle evidence before code mutation."],
    )

    other_scene = service.scene_start(
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        namespace="repo:dhee",
        query="Calendar dinner planning note",
        action_lane="planning",
        categories=["scene_runtime"],
    )
    service.scene_end(
        scene_id=other_scene["scene"]["id"],
        outcome="Calendar dinner note stored.",
        outcome_status="success",
    )

    context = service.scene_context(
        query="proof bundle sqlite retrieval",
        user_id="default",
        agent_id="codex",
        agent_category="coding_agent",
        namespace="repo:dhee",
        categories=["scene_runtime"],
        limit=2,
    )

    top = context["included_cards"][0]
    assert top["scene_id"] == proof_scene["scene"]["id"]
    assert top["embedding_similarity"] > 0
    assert top["rerank_score"] == 1.0
    assert any(reason.startswith("embedding_similarity:") for reason in top["included_reasons"])
    assert any(reason.startswith("rerank_score:") for reason in top["included_reasons"])
    assert "embedding_similarity" in context["retrieval_policy"]["ranking_features"]
    assert "neural_rerank" in context["retrieval_policy"]["ranking_features"]
    assert context["retrieval_policy"]["semantic_backend"] == {
        "embedder": "fake-nemotron-embed",
        "reranker": "fake-nemotron-rerank",
        "embedding_similarity_active": True,
        "rerank_active": True,
        "fallback": None,
    }


def test_mcp_slim_narrative_scene_handlers_use_sqlite_cards(tmp_path, monkeypatch):
    from dhee import mcp_slim

    db = SQLiteManager(str(tmp_path / "mcp.db"))
    monkeypatch.setattr(mcp_slim, "_get_db", lambda: db)

    start = mcp_slim.HANDLERS["dhee_scene_start"](
        {
            "user_id": "default",
            "agent_id": "codex",
            "agent_category": "coding_agent",
            "namespace": "repo:dhee",
            "query": "MCP SceneCard retrieval",
            "categories": ["scene_runtime"],
        }
    )
    assert start["format"] == "dhee_scene_start.v1"

    mcp_slim.HANDLERS["dhee_scene_event"](
        {
            "scene_id": start["scene"]["id"],
            "summary": "MCP SceneCard retrieval stores prompt-safe evidence refs.",
            "evidence_ref": "ptr:mcp-scene",
        }
    )
    payload_event = mcp_slim.HANDLERS["dhee_scene_event"](
        {
            "scene_id": start["scene"]["id"],
            "event_type": "worker_result",
            "payload": {"payload": {"summary": "MCP accepted Chotu payload evidence."}},
        }
    )
    assert payload_event["event"]["summary"] == "MCP accepted Chotu payload evidence."
    end = mcp_slim.HANDLERS["dhee_scene_end"](
        {
            "scene_id": start["scene"]["id"],
            "outcome": "MCP SceneCard stored.",
            "outcome_status": "success",
            "durable_facts": ["MCP scene tools can promote explicit durable facts."],
            "evidence": [
                {
                    "kind": "chotu_scene_consolidation_input",
                    "summary": "MCP carried Chotu scene-end consolidation evidence.",
                    "payload": {
                        "schema_version": "chotu.dhee_scene_consolidation_input.v1",
                        "id": "mcp-consolidation",
                        "truth_model": {"thinking_events_are_provisional": True},
                        "prompt_causality": {"enabled": True, "prompt_sha256": "mcp-prompt"},
                    },
                }
            ],
            "promote_durable_facts": True,
        }
    )
    assert end["card"]["id"]
    assert any(ref["kind"] == "chotu_scene_consolidation_input" for ref in end["card"]["evidence_refs"])
    assert end["promoted_memory_ids"]

    context = mcp_slim.HANDLERS["dhee_scene_context"](
        {
            "query": "MCP SceneCard retrieval",
            "user_id": "default",
            "agent_id": "chotu",
            "agent_category": "personal_agent",
            "namespace": "repo:dhee",
            "categories": ["scene_runtime"],
        }
    )
    assert context["included_cards"]

    search = mcp_slim.HANDLERS["dhee_scene_search"](
        {
            "query": "MCP SceneCard retrieval",
            "user_id": "default",
            "agent_id": "chotu",
            "agent_category": "personal_agent",
            "namespace": "repo:dhee",
            "categories": ["scene_runtime"],
        }
    )
    assert search["source"] == "sqlite_scene_cards"

    pack = mcp_slim.HANDLERS["dhee_context_pack"](
        {
            "query": "MCP SceneCard retrieval",
            "user_id": "default",
            "agent_id": "chotu",
            "agent_category": "personal_agent",
            "namespace": "repo:dhee",
            "categories": ["scene_runtime"],
            "token_budget": 600,
        }
    )
    assert pack["source"] == "sqlite_scene_cards"

    prior = mcp_slim.HANDLERS["dhee_narrative_prior"](
        {
            "query": "what should Chotu do next with MCP SceneCard retrieval",
            "user_id": "default",
            "agent_id": "chotu",
            "agent_category": "personal_agent",
            "namespace": "repo:dhee",
            "categories": ["scene_runtime"],
        }
    )
    assert prior["schema_version"] == "dhee.narrative_prior.v1"
    assert prior["evidence_scene_cards"]
    assert prior["guardrails"]
