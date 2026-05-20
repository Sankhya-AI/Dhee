from __future__ import annotations

from pathlib import Path

import pytest

from dhee.core.learnings import LearningExchange
from dhee.world_memory.capture_store import CaptureStore
from dhee.world_memory.causal_graph import CausalGraphProjection
from dhee.world_memory.gem_extractor import (
    extract_memory_gems,
    write_gem_raw_events,
)
from dhee.world_memory.schema import CAUSAL_SCHEMA_VERSION, CausalEdge, RetrievalTrace
from dhee.world_memory.service import MemoryOSService
from dhee.world_memory.session_graph import SessionGraphStore
from dhee.world_memory.store import WorldMemoryStore


class FakeMemoryClient:
    def __init__(self) -> None:
        self.rows = []

    def remember(self, content: str, **kwargs):
        row = {
            "id": f"mem-{len(self.rows) + 1}",
            "memory": content,
            "metadata": dict(kwargs.get("metadata") or {}),
            "source_app": kwargs.get("source_app"),
        }
        self.rows.append(row)
        return row

    def recall(self, *args, **kwargs):
        return []

    def recent(self, *args, **kwargs):
        return list(reversed(self.rows))[: kwargs.get("limit", 12)]


def _service(tmp_path: Path) -> tuple[MemoryOSService, FakeMemoryClient]:
    memory = FakeMemoryClient()
    service = MemoryOSService(
        capture_store=CaptureStore(str(tmp_path / "capture.db")),
        world_store=WorldMemoryStore(str(tmp_path / "world.db")),
        graph_store=SessionGraphStore(str(tmp_path / "sessions")),
        memory_client=memory,
        graph_projection=CausalGraphProjection(str(tmp_path / "causal_scene.kuzu")),
    )
    return service, memory


def test_raw_event_truth_layer_and_kuzu_rebuild_verify(tmp_path: Path) -> None:
    service, memory = _service(tmp_path)

    recorded = service.record_raw_event(
        {
            "id": "evt-1",
            "user_id": "default",
            "source_app": "gmail",
            "event_type": "email_received",
            "timestamp": "2026-05-20T00:00:00+00:00",
            "session_id": "session-1",
            "privacy_scope": "global",
            "metadata": {"entities": ["Dhee"], "threads": ["task"]},
        }
    )

    stored = service.capture_store.get_raw_event("evt-1")
    assert stored is not None
    assert stored.schema_version == CAUSAL_SCHEMA_VERSION
    assert stored.deleted_at is None
    assert stored.redacted_at is None
    assert recorded["projection"]["backend"] == "kuzu"
    assert memory.rows == []

    projection = service.graph_projection
    assert projection is not None
    projection.delete()
    rebuilt = projection.rebuild(service.capture_store)
    assert rebuilt["backend"] == "kuzu"
    verified = projection.verify(service.capture_store)
    assert verified["ok"] is True
    assert verified["checks"]["node_count"]["projected_raw_events"] == 1


def test_redacted_events_do_not_project_as_active_nodes(tmp_path: Path) -> None:
    service, _memory = _service(tmp_path)
    service.record_raw_event(
        {
            "id": "evt-redact",
            "user_id": "default",
            "source_app": "chrome",
            "event_type": "browser_decision",
            "timestamp": "2026-05-20T00:00:00+00:00",
            "privacy_scope": "private",
        }
    )

    service.capture_store.redact_raw_event("evt-redact", reason="user requested redaction")
    assert service.graph_projection is not None
    service.graph_projection.sync(service.capture_store)

    verified = service.graph_projection.verify(service.capture_store)
    assert verified["ok"] is True
    assert verified["checks"]["node_count"]["projected_raw_events"] == 0
    assert service.graph_projection.show_event("evt-redact")["status"] == "not_found"


def test_causal_edge_requires_evidence_for_caused(tmp_path: Path) -> None:
    store = CaptureStore(str(tmp_path / "capture.db"))

    with pytest.raises(ValueError, match="CAUSED edges require evidence_event_ids"):
        store.add_causal_edge(
            CausalEdge(
                id="edge-1",
                source_id="frame-a",
                target_id="frame-b",
                edge_type="CAUSED",
                confidence=0.8,
                status="inferred",
                evidence_event_ids=[],
                inferred_by="rule",
                explanation="invalid",
                created_at="2026-05-20T00:00:00+00:00",
            )
        )


def test_checkpoint_creates_event_frames_edges_and_report(tmp_path: Path) -> None:
    service, memory = _service(tmp_path)
    service.record_raw_event(
        {
            "id": "evt-a",
            "user_id": "default",
            "source_app": "gmail",
            "event_type": "email_received",
            "timestamp": "2026-05-20T00:00:00+00:00",
            "session_id": "session-2",
        }
    )
    service.record_raw_event(
        {
            "id": "evt-b",
            "user_id": "default",
            "source_app": "chrome",
            "event_type": "task_created",
            "timestamp": "2026-05-20T00:01:00+00:00",
            "session_id": "session-2",
        }
    )

    checkpoint = service.compile_causal_checkpoint(session_id="session-2")

    assert [frame["id"] for frame in checkpoint["eventFrames"]] == ["frame:evt-a", "frame:evt-b"]
    assert checkpoint["causalEdges"][0]["edge_type"] == "SUPPORTED"
    assert checkpoint["causalEdges"][0]["evidence_event_ids"] == ["evt-a", "evt-b"]
    assert checkpoint["report"]["summary_memory_id"] == "mem-1"
    assert memory.rows[0]["metadata"]["source_event_ids"] == ["evt-a", "evt-b"]


def test_threads_scope_filter_and_retrieval_shapes(tmp_path: Path) -> None:
    service, _memory = _service(tmp_path)
    service.record_raw_event(
        {
            "id": "evt-thread",
            "user_id": "default",
            "source_app": "chrome",
            "event_type": "preference",
            "timestamp": "2026-05-20T00:00:00+00:00",
            "session_id": "session-3",
            "privacy_scope": "private",
            "metadata": {"threads": ["task"], "project": "Dhee"},
        }
    )

    projection = service.graph_projection
    assert projection is not None
    shown = projection.show_event("evt-thread")
    assert shown["event"]["sqlite_id"] == "evt-thread"
    assert len(shown["threads"]) >= 4

    global_frontier = service.get_active_frontier(user_id="default", scope="global")
    assert global_frontier["active_threads"] == []

    private_frontier = service.get_active_frontier(user_id="default", scope="private")
    assert private_frontier["high_confidence_preferences"]
    assert private_frontier["evidence"] == [{"event_id": "evt-thread"}]

    why = service.causal_why(event_id="evt-thread", user_id="default", scope="private")
    assert why["target_event_id"] == "evt-thread"
    assert "evidence" in why


def test_memory_gem_extractor_writes_provenance_raw_events(tmp_path: Path) -> None:
    store = CaptureStore(str(tmp_path / "capture.db"))
    memories = [
        {
            "id": "mem-pref",
            "user_id": "default",
            "memory": "User prefers brutally honest architectural critique with concrete tradeoffs.",
            "strength": 0.9,
            "importance": 0.9,
            "memory_type": "semantic",
            "categories": ["preference"],
            "source_app": "codex",
            "created_at": "2026-05-20T00:00:00+00:00",
            "metadata": {"scope": "personal"},
        },
        {
            "id": "mem-noise",
            "user_id": "default",
            "memory": "Chotu observed useful visible screen activity. App: Chrome",
            "strength": 0.5,
            "importance": 0.2,
            "memory_type": "episodic",
            "categories": [],
            "source_app": "chotu",
            "created_at": "2026-05-20T00:01:00+00:00",
            "metadata": {},
        },
    ]

    gems = extract_memory_gems(memories, user_id="default", min_score=0.5)
    assert len(gems) == 1
    assert gems[0].kind == "preference"
    assert gems[0].privacy_scope == "private"

    report = write_gem_raw_events(store, gems)
    assert report["written"] == [f"gem:{gems[0].id}"]
    stored = store.get_raw_event(f"gem:{gems[0].id}")
    assert stored is not None
    assert stored.content_ref == "memory:mem-pref"
    assert stored.metadata["source_memory_id"] == "mem-pref"

    again = write_gem_raw_events(store, gems)
    assert again["written"] == []
    assert again["skipped_existing"] == [f"gem:{gems[0].id}"]


def test_memory_gem_projects_into_semantic_threads_and_gem_listing(tmp_path: Path) -> None:
    service, _memory = _service(tmp_path)
    memories = [
        {
            "id": "mem-pref",
            "user_id": "default",
            "memory": "User prefers product-first framing and dislikes vague architecture plans.",
            "strength": 0.92,
            "importance": 0.88,
            "memory_type": "semantic",
            "categories": ["preference"],
            "source_app": "codex",
            "created_at": "2026-05-20T00:00:00+00:00",
            "metadata": {"scope": "work"},
        },
        {
            "id": "mem-pref-visual",
            "user_id": "default",
            "memory": "User prefers polished frontend layouts with concrete controls and restrained cards.",
            "strength": 0.82,
            "importance": 0.8,
            "memory_type": "semantic",
            "categories": ["preference"],
            "source_app": "codex",
            "created_at": "2026-05-20T00:01:00+00:00",
            "metadata": {"scope": "work"},
        },
        {
            "id": "mem-pref-private",
            "user_id": "default",
            "memory": "User prefers private personal notes to stay out of global retrieval.",
            "strength": 0.91,
            "importance": 0.91,
            "memory_type": "semantic",
            "categories": ["preference"],
            "source_app": "codex",
            "created_at": "2026-05-20T00:02:00+00:00",
            "metadata": {"scope": "personal"},
        },
    ]
    gems = extract_memory_gems(memories, user_id="default", min_score=0.5)
    write_gem_raw_events(service.capture_store, gems)
    assert service.graph_projection is not None
    service.graph_projection.rebuild(service.capture_store)

    product_gem = next(gem for gem in gems if gem.source_memory_id == "mem-pref")
    event_id = f"gem:{product_gem.id}"
    shown = service.graph_projection.show_event(event_id)
    thread_types = {row.get("thread_type") for row in shown["threads"]}
    assert {"gem", "gem_kind", "preference", "source_memory"}.issubset(thread_types)

    shown_gem = service.causal_show_gem(event_id, user_id="default", scope="global")
    assert shown_gem["status"] == "ok"
    assert shown_gem["gem"]["event_id"] == event_id
    assert shown_gem["gem"]["projection_version"]
    assert shown_gem["source_memory"]["memory_id"] == "mem-pref"
    assert shown_gem["supporting_events"][0]["source_memory_id"] == "mem-pref"
    assert {"preference", "source_memory"}.issubset({row.get("thread_type") for row in shown_gem["threads"]})
    assert shown_gem["retrieval_path"][0]["step"] == "match_scoped_memory_gem"

    shown_by_gem_id = service.causal_show_gem(product_gem.id, user_id="default", scope="global")
    assert shown_by_gem_id["gem"]["event_id"] == event_id

    exchange = LearningExchange(tmp_path / "learnings")
    submitted = service.causal_submit_gem(
        product_gem.id,
        learning_exchange=exchange,
        user_id="default",
        scope="global",
        repo=str(tmp_path),
    )
    assert submitted["submitted"] == [f"lrn_{product_gem.id[-16:]}"]
    candidate = exchange.get(submitted["submitted"][0])
    assert candidate is not None
    assert candidate.kind == "policy"
    assert candidate.metadata["gem_id"] == product_gem.id
    assert candidate.metadata["source_memory_id"] == "mem-pref"
    assert candidate.evidence

    private_gem = next(gem for gem in gems if gem.source_memory_id == "mem-pref-private")
    private_hidden = service.causal_show_gem(private_gem.id, user_id="default", scope="global")
    assert private_hidden["status"] == "not_found"
    private_submit = service.causal_submit_gem(
        private_gem.id,
        learning_exchange=exchange,
        user_id="default",
        scope="global",
    )
    assert private_submit["submitted"] == []
    assert private_submit["rejected"][0]["reason"] == "not_found"
    private_visible = service.causal_show_gem(private_gem.id, user_id="default", scope="private")
    assert private_visible["status"] == "ok"

    preferences = service.causal_preference(query="product-first architecture", user_id="default", scope="global")
    assert preferences["preference_signal"]["signal_type"] == "memory_gem"
    assert preferences["preference_signal"]["thread_type"] == "preference"
    assert preferences["preference_signal"]["source_memory_id"] == "mem-pref"
    assert preferences["supporting_events"][0]["event_id"] == event_id
    assert preferences["retrieval_path"][0]["step"] == "list_gems"
    assert preferences["retrieval_id"].startswith("retr_")
    explained = service.explain_causal_retrieval(preferences["retrieval_id"], user_id="default")
    assert explained["status"] == "ok"
    assert explained["mode"] == "preference"
    assert explained["query"] == "product-first architecture"
    assert explained["traversal"][0]["step"] == "list_gems"
    assert explained["evidence"][0]["event_id"] == event_id
    assert explained["result"]["preference_signal"]["source_memory_id"] == "mem-pref"
    assert service.explain_causal_retrieval(preferences["retrieval_id"], user_id="other")["status"] == "not_found"

    frontier = service.get_active_frontier(user_id="default", scope="global")
    assert frontier["retrieval_id"].startswith("retr_")
    frontier_preferences = frontier["high_confidence_preferences"]
    assert frontier_preferences[0]["signal_type"] == "memory_gem"
    assert frontier_preferences[0]["thread_type"] == "preference"
    assert {
        preference["source_memory_id"] for preference in frontier_preferences
    } == {"mem-pref", "mem-pref-visual"}

    listed = service.causal_gems(user_id="default", scope="global", kind="preference")
    assert listed["count"] == 2
    assert listed["by_kind"] == {"preference": 2}
    assert {gem["source_memory_id"] for gem in listed["gems"]} == {"mem-pref", "mem-pref-visual"}

    private_listed = service.causal_gems(user_id="default", scope="private", kind="preference")
    assert private_listed["count"] == 3
    private_frontier = service.get_active_frontier(user_id="default", scope="private")
    assert {
        preference["source_memory_id"] for preference in private_frontier["high_confidence_preferences"]
    } == {"mem-pref", "mem-pref-visual", "mem-pref-private"}

    service.capture_store.redact_raw_event(event_id, reason="user requested trace redaction")
    assert service.explain_causal_retrieval(preferences["retrieval_id"], user_id="default")["status"] == "not_found"
    redacted_trace = service.capture_store.get_retrieval_trace(
        preferences["retrieval_id"],
        include_redacted=True,
    )
    assert redacted_trace is not None
    assert redacted_trace.redacted_at
    assert redacted_trace.redaction_reason == "user requested trace redaction"


def test_retrieval_trace_pruning_preserves_latest_and_supports_dry_run(tmp_path: Path) -> None:
    store = CaptureStore(str(tmp_path / "capture.db"))
    for index in range(5):
        store.add_retrieval_trace(
            RetrievalTrace(
                id=f"retr-{index}",
                user_id="default",
                mode="preference",
                scope="global",
                query="",
                target_id=f"evt-{index}",
                retrieval_path=[],
                evidence=[],
                result={"index": index},
                privacy_scope="global",
                created_at=f"2026-05-20T00:0{index}:00+00:00",
            )
        )
    store.add_retrieval_trace(
        RetrievalTrace(
            id="retr-other",
            user_id="other",
            mode="preference",
            scope="global",
            query="",
            target_id="evt-other",
            retrieval_path=[],
            evidence=[],
            result={},
            privacy_scope="global",
            created_at="2026-05-20T00:10:00+00:00",
        )
    )

    dry_run = store.prune_retrieval_traces(user_id="default", keep_latest=2, dry_run=True)
    assert dry_run["candidate_count"] == 3
    assert dry_run["pruned_count"] == 0
    assert store.get_retrieval_trace("retr-0") is not None

    pruned = store.prune_retrieval_traces(user_id="default", keep_latest=2)
    assert pruned["pruned_count"] == 3
    assert store.get_retrieval_trace("retr-0") is None
    assert store.get_retrieval_trace("retr-1") is None
    assert store.get_retrieval_trace("retr-2") is None
    assert store.get_retrieval_trace("retr-3") is not None
    assert store.get_retrieval_trace("retr-4") is not None
    assert store.get_retrieval_trace("retr-other") is not None
