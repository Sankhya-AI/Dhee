from __future__ import annotations

from dhee.context_state import (
    BURN_ROLLOVER_TOKENS,
    CACHE_FIVE_MINUTE,
    CACHE_ONE_HOUR,
    AdmissionResult,
    classify_task_intent,
    ContextAdmissionController,
    ContextBlock,
    ContextStateStore,
    detect_echo,
    task_aware_read_schema,
)


def make_store(tmp_path):
    return ContextStateStore(
        repo=str(tmp_path),
        workspace_id=str(tmp_path),
        user_id="u",
        agent_id="test",
        data_dir=tmp_path / "state",
    )


def test_state_card_contains_canonical_state_not_audit_journey(tmp_path):
    store = make_store(tmp_path)
    store.observe_prompt("Fix expired-token KeyError in login")
    store.add_fact("middleware.py line 47 raises KeyError iat", source="pytest")
    store.add_decision("Use python-jose validation path")

    card = store.render_state_card()

    assert "<dhee_state" in card
    assert "<goal>Fix expired-token KeyError in login</goal>" in card
    assert "middleware.py line 47" in card
    assert "Use python-jose" in card
    assert "audit" not in card.lower()


def test_decision_supersession_tombstones_old_decision(tmp_path):
    store = make_store(tmp_path)
    state = store.add_decision("Roll our own JWT validator")
    old_id = state["decisions"][0]["id"]

    store.supersede_decision(old_id, "Use python-jose validator", reason="library handles edge cases")
    active = store.render_decisions()
    superseded = store.render_decisions(superseded=True)
    card = store.render_state_card()

    assert "Use python-jose validator" in active
    assert "Roll our own JWT validator" not in active
    assert "Roll our own JWT validator" in superseded
    assert old_id in card
    assert "Roll our own JWT validator" not in card


def test_admission_suppresses_duplicate_context(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)
    block = ContextBlock(kind="doc", text="Important migration note", source="README", relevance=0.9)

    first = controller.decide(block)
    second = controller.decide(block)

    assert first.decision == "admit"
    assert second.decision == "suppress"
    assert "already admitted" in second.reason
    assert store.debt_summary()["suppressed_tokens"] > 0


def test_admission_records_cache_tier_and_deterministic_receipt(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)
    block = ContextBlock(kind="doc", text="Project rule: use Dhee first", source="AGENTS.md", relevance=0.9)

    result = controller.decide(block)
    first_receipt = store.load()["admission_receipts"][-1]

    store.reset()
    second = ContextAdmissionController(store).decide(block)
    second_receipt = store.load()["admission_receipts"][-1]

    assert result.cache_tier == CACHE_ONE_HOUR
    assert second.cache_tier == CACHE_ONE_HOUR
    assert first_receipt["id"] == second_receipt["id"]
    assert first_receipt["content_hash"] == block.hash


def test_stable_prefix_mutation_downgrades_and_warns(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)

    first = controller.decide(ContextBlock(kind="doc", text="old project rule", source="AGENTS.md", relevance=0.9))
    second = controller.decide(ContextBlock(kind="doc", text="new project rule", source="AGENTS.md", relevance=0.9))
    state = store.load()

    assert first.cache_tier == CACHE_ONE_HOUR
    assert second.cache_tier == CACHE_FIVE_MINUTE
    assert second.metadata["stable_prefix_mutation"] is True
    assert any(row["kind"] == "stable_prefix_mutation" for row in state["ledger_warnings"])


def test_admission_requires_relevance_for_docs(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)

    result = controller.decide(ContextBlock(kind="doc", text="unrelated style guide", source="AGENTS.md", relevance=0.2))

    assert result.decision == "suppress"
    assert "relevance" in result.reason


def test_large_context_triggers_rollover_threshold(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)
    big_text = "x" * int((BURN_ROLLOVER_TOKENS + 1000) * 3.5)

    result = controller.decide(ContextBlock(kind="tool_result", text=big_text, source="pytest"))

    assert result.decision == "rollover_required"
    assert store.status()["rollover_required"] is True


def test_rollover_receipt_lists_hashes_files_and_supersession_edges(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)
    old_state = store.add_decision("Use append-only transcript")
    old_id = old_state["decisions"][0]["id"]
    store.supersede_decision(old_id, "Use compiled state card", reason="survey evidence")
    block = ContextBlock(
        kind="routed_read",
        text="<dhee_read>src/app.py relevant slice</dhee_read>",
        source="src/app.py",
        ptr="R-src",
        content_hash="file-hash-1",
        metadata={"token_estimate": 300},
    )
    controller.decide(block)

    rollover = store.rollover(reason="test rollover")
    receipt = rollover["rollover_receipt"]

    assert receipt["id"].startswith("RR-")
    assert "src/app.py" in receipt["summarized_files"]
    assert "file-hash-1" in receipt["summarized_hashes"]
    assert receipt["supersession_edges"][0]["from"] == old_id
    assert store.load()["rollover_receipts"][-1]["id"] == receipt["id"]


def test_reread_after_rollover_short_circuits_to_receipt(tmp_path):
    store = make_store(tmp_path)
    controller = ContextAdmissionController(store)
    block = ContextBlock(
        kind="routed_read",
        text="<dhee_read>src/app.py relevant slice</dhee_read>",
        source="src/app.py",
        ptr="R-src",
        content_hash="file-hash-1",
        metadata={"token_estimate": 300},
    )
    controller.decide(block)
    receipt = store.rollover(reason="test rollover")["rollover_receipt"]

    reread = controller.decide(block)
    debt = store.debt_summary()

    assert reread.decision == "suppress"
    assert "rollover receipt" in reread.reason
    assert reread.metadata["rollover_receipt_id"] == receipt["id"]
    assert debt["reread_short_circuit_count"] == 1


def test_echo_detection_catches_paraphrased_tool_result():
    tool = (
        "tests/test_auth.py failed with KeyError iat in middleware.py line 47 "
        "for expired token path after verify_jwt returned missing issued_at claim "
        "inside login handler regression suite"
    )
    assistant = (
        "I see tests/test_auth.py failed with KeyError iat in middleware.py line 47, "
        "for the expired token path after verify_jwt returned a missing issued_at claim "
        "inside the login handler regression suite."
    )

    report = detect_echo(tool, assistant)

    assert report["is_echo"] is True
    assert report["overlap"] >= report["threshold"]


def test_task_aware_schema_selects_debug_failure():
    schema = task_aware_read_schema("auth.py", query="debug failing pytest traceback")

    assert schema["intent"] == "debug_failure"
    assert schema["preferred_depth"] == "deep"
    assert "failure landmarks" in schema["note"]


def test_routing_query_compiles_task_signal_from_state(tmp_path):
    store = make_store(tmp_path)
    store.observe_prompt("Fix parser crash from pytest traceback")
    store.add_fact("tests/test_parser.py line 42 raises ValueError", source="pytest")

    route = store.routing_query(extra="parser.py")

    assert route["source"] == "compiled_state"
    assert route["intent"] == "debug_failure"
    assert "parser.py" in route["query"]


def test_prompt_pivot_starts_new_epoch_and_tombstones_stale_state(tmp_path):
    store = make_store(tmp_path)
    store.observe_prompt("Fix bundled handoff bus installer failure")
    store.add_fact("install.sh verifies engram_bus", source="installer")
    state = store.add_decision("Bundle engram_bus in the main wheel")
    decision_id = state["decisions"][0]["id"]

    store.observe_prompt("Strengthen compiled state and cross-agent continuity for Dhee pillars")
    state = store.load()
    card = store.render_state_card()

    assert state["task_epoch"] == 2
    assert state["goal"].startswith("Strengthen compiled state")
    assert state["goal_history"][0]["goal"].startswith("Fix bundled")
    assert state["stale_facts"]
    assert state["facts"] == []
    assert state["seen_hashes"] == {}
    assert state["decisions"][0]["id"] == decision_id
    assert state["decisions"][0]["status"] == "superseded"
    assert 'epoch="2"' in card


def test_routed_bash_admission_updates_canonical_test_state(tmp_path):
    store = make_store(tmp_path)
    block = ContextBlock(
        kind="routed_bash",
        text="<dhee_bash>pytest failed first_fail=tests/test_parser.py:42</dhee_bash>",
        source=str(tmp_path),
        ptr="R-test123",
        metadata={"command": "pytest tests/test_parser.py", "exit_code": 1, "token_estimate": 900},
    )
    result = AdmissionResult(
        decision="admit_digest",
        reason="digest is safer",
        token_estimate=900,
        content_hash=block.hash,
        liability_tokens=900,
    )

    store.record_admission(block, result)
    state = store.load()

    assert "pytest tests/test_parser.py exit=1" in state["test_status"]
    assert state["next_action"].startswith("Fix the current failing test")
    assert state["evidence"][0]["ptr"] == "R-test123"
    assert any("pytest tests/test_parser.py" in row["text"] for row in state["facts"])


def test_classify_task_intent_keeps_router_read_schema_small():
    assert classify_task_intent("where is ContextStateStore defined?") == "find_definition"
    assert classify_task_intent("understand module boundaries and exports") == "understand_module"
