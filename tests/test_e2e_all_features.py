"""Opt-in live end-to-end coverage for the Dhee memory stack.

This suite exercises the NVIDIA-backed runtime plus optional power packages.
It is intentionally skipped unless ``DHEE_RUN_LIVE_TESTS=1`` is set.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import pytest

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._live import ensure_live_nvidia_runtime, require_live_nvidia_tests

pytestmark = pytest.mark.integration

if __name__ != "__main__":
    require_live_nvidia_tests("openai")

from dhee.configs.base import (
    CategoryMemConfig,
    EchoMemConfig,
    EmbedderConfig,
    LLMConfig,
    MemoryConfig,
    ProfileConfig,
    VectorStoreConfig,
)
from dhee.memory.main import FullMemory as Memory


def create_memory() -> Memory:
    config = MemoryConfig(
        llm=LLMConfig(
            provider="nvidia",
            config={"model": "meta/llama-3.1-8b-instruct"},
        ),
        embedder=EmbedderConfig(
            provider="nvidia",
            config={"model": "nvidia/nv-embedqa-e5-v5"},
        ),
        vector_store=VectorStoreConfig(provider="memory", config={}),
        history_db_path=":memory:",
        embedding_model_dims=1024,
        echo=EchoMemConfig(enable_echo=False),
        category=CategoryMemConfig(use_llm_categorization=False),
        profile=ProfileConfig(enable_profiles=False),
    )
    return Memory(config=config)


def run_live_e2e_suite() -> dict[str, object]:
    passed = 0
    failed = 0
    errors: list[tuple[str, str]] = []
    stored_ids: dict[str, object] = {}

    memory = create_memory()

    def step(name: str):
        def decorator(fn):
            nonlocal passed, failed
            print(f"\n{'-' * 60}")
            print(f"STEP: {name}")
            print(f"{'-' * 60}")
            try:
                fn()
                passed += 1
                print("  PASS")
            except Exception as exc:  # pragma: no cover - exercised only in live mode
                failed += 1
                errors.append((name, f"{exc}\n{traceback.format_exc()}"))
                print(f"  FAIL: {exc}")
            return fn

        return decorator

    try:
        @step("Core memory CRUD, search, stats, and decay")
        def _():
            first = memory.add(
                "Vivek prefers Python over JavaScript for backend work",
                user_id="vivek",
                metadata={"source": "e2e_test"},
                infer=False,
            )
            items = first.get("results", [])
            assert items, "add() returned no results"
            stored_ids["first_memory"] = items[0]["id"]

            for content in [
                "Production deploy uses GitHub Actions, not Jenkins",
                "PostgreSQL to MongoDB migration failed due to schema issues",
                "Team chose React for frontend because of components",
                "Rate limiting uses Redis with sliding window",
                "CI pipeline: pytest, eslint, then Docker build",
                "Auth uses JWT with 15-min expiry and refresh tokens",
                "Microservices use gRPC internally, REST externally",
            ]:
                result = memory.add(content, user_id="vivek", infer=False)
                assert result.get("results"), f"failed to add memory: {content}"

            search = memory.search("what deployment tool do we use", user_id="vivek", limit=5)
            hits = search.get("results", [])
            assert hits, "search returned no results"
            top_text = hits[0].get("memory", "").lower()
            assert any(word in top_text for word in ("deploy", "github", "ci", "docker", "pipeline"))

            stored = memory.get(stored_ids["first_memory"])
            assert stored and "Python" in stored.get("memory", "")

            updated = memory.update(
                stored_ids["first_memory"],
                {"content": "Vivek prefers Python over JS for all work"},
            )
            assert updated is not None

            all_memories = memory.get_all(user_id="vivek", limit=50).get("results", [])
            assert len(all_memories) >= 8

            stats = memory.get_stats(user_id="vivek")
            assert stats is not None

            decay_result = memory.apply_decay(scope={"user_id": "vivek"})
            assert isinstance(decay_result, dict)

        time.sleep(1)

        @step("Procedural memory extraction and refinement")
        def _():
            from engram_procedural import Procedural

            episodes = [
                "Debugged login: checked auth logs, traced JWT, found expired token, fixed refresh",
                "Fixed signup: checked logs, traced auth flow, found token expiry, updated refresh",
                "Session timeout fix: reviewed logs, traced token lifecycle, updated refresh process",
            ]
            episode_ids = []
            for episode in episodes:
                result = memory.add(
                    episode,
                    user_id="vivek",
                    metadata={"memory_type": "episodic", "explicit_remember": True},
                    infer=False,
                )
                items = result.get("results", [])
                assert items and "id" in items[0], f"invalid add() result: {items}"
                episode_ids.append(items[0]["id"])

            proc = Procedural(memory, user_id="vivek")
            extracted = proc.extract_procedure(
                episode_ids=episode_ids,
                name="debug_auth_issues",
                domain="authentication",
            )
            assert "error" not in extracted, f"procedure extraction failed: {extracted}"
            proc_id = extracted.get("id")
            assert proc_id, "procedure id missing"
            stored_ids["procedure_id"] = proc_id

            looked_up = proc.get_procedure("debug_auth_issues")
            assert looked_up is not None

            proc.log_execution(proc_id, success=True, context="Run 1")
            proc.log_execution(proc_id, success=True, context="Run 2")
            proc.log_execution(proc_id, success=False, context="Token was not expired this time")

            refined = proc.refine_procedure(
                proc_id,
                correction="Also check session cookie before tracing JWT",
            )
            assert refined.get("refined") is True

            results = proc.search_procedures("how to debug authentication problems")
            assert results, "procedure search returned no results"

            active = proc.list_procedures(status="active")
            assert active, "expected at least one active procedure"

        time.sleep(1)

        @step("Reconsolidation proposal, apply, reject, and history")
        def _():
            from engram_reconsolidation import Reconsolidation

            rc = Reconsolidation(memory, user_id="vivek")
            search = memory.search("deploy pipeline", user_id="vivek", limit=1)
            items = search.get("results", [])
            assert items, "could not find deploy pipeline memory"
            target_id = items[0]["id"]

            proposal = rc.propose_update(
                memory_id=target_id,
                new_context="We now also run Snyk security scanning before Docker build",
            )
            if proposal.get("id"):
                stored_ids["proposal_id"] = proposal["id"]

            pending = rc.list_pending_proposals()
            assert pending is not None

            proposal_id = stored_ids.get("proposal_id")
            if proposal_id:
                applied = rc.apply_update(proposal_id)
                assert applied.get("status") in {"applied", "accepted", "updated"}

            created = memory.add("The team standup is every Monday at 10am", user_id="vivek", infer=False)
            created_items = created.get("results", [])
            assert created_items and "id" in created_items[0]
            extra_id = created_items[0]["id"]

            rejection_candidate = rc.propose_update(extra_id, new_context="Standups moved to Tuesday at 2pm")
            if rejection_candidate.get("id"):
                rejected = rc.reject_update(rejection_candidate["id"], reason="Not confirmed yet")
                assert rejected.get("status") in {"rejected", "declined"}

            stats = rc.get_stats()
            assert isinstance(stats, dict)

            history = rc.get_version_history(target_id)
            assert isinstance(history, list)

        time.sleep(1)

        @step("Failure learning and anti-pattern extraction")
        def _():
            from engram_failure import FailureLearning

            fl = FailureLearning(memory, user_id="vivek")
            first = fl.log_failure(
                action="deploy_to_production",
                error="Connection timeout to AWS ECS",
                context="Deploy with reduced capacity",
                severity="high",
                agent_id="claude-code",
            )
            assert first.get("action") == "deploy_to_production" or first.get("status") == "logged"

            failure_ids = [first.get("id")]
            for action, error, context in [
                ("deploy_staging", "ECS cluster timeout", "Staging, Friday evening"),
                ("deploy_canary", "Load balancer refused", "Canary during peak"),
                ("deploy_hotfix", "ECS task start timeout", "Emergency hotfix midnight"),
            ]:
                result = fl.log_failure(action=action, error=error, context=context, severity="high")
                if result.get("id"):
                    failure_ids.append(result["id"])
            failure_ids = [failure_id for failure_id in failure_ids if failure_id]
            assert len(failure_ids) >= 3

            search_results = fl.search_failures("deployment timeout ECS")
            assert search_results, "expected failure search results"

            antipattern = fl.extract_antipattern(
                failure_ids=failure_ids[:3],
                name="deploy_during_off_hours",
            )
            assert antipattern is not None

            listed = fl.list_antipatterns()
            assert isinstance(listed, list)

            stats = fl.get_failure_stats()
            assert stats["total_failures"] >= 4

            recovery = fl.search_recovery_strategies("timeout during deploy")
            assert isinstance(recovery, list)

        time.sleep(1)

        @step("Working memory operations and long-term flush")
        def _():
            from engram_working import WorkingMemory

            wm = WorkingMemory(memory, user_id="vivek", capacity=5)
            first = wm.push("Current task: fix the auth token refresh bug", tag="task")
            second = wm.push("The JWT secret is rotated every 24h", tag="context")
            wm.push("Related PR: #1234 by Alice", tag="reference")
            assert first.get("key") and second.get("key")

            listed = wm.list()
            assert len(listed) == 3

            peeked = wm.peek(first["key"])
            assert peeked is not None and peeked["access_count"] >= 1

            evictor = WorkingMemory(memory, user_id="vivek_wm_evict", capacity=3)
            evictor.push("Item 1", tag="1")
            evictor.push("Item 2", tag="2")
            evictor.push("Item 3", tag="3")
            eviction = evictor.push("Item 4", tag="4")
            assert eviction.get("evicted") is not None

            popper = WorkingMemory(memory, user_id="vivek_wm_pop")
            popped_key = popper.push("Temporary note")["key"]
            popped = popper.pop(popped_key)
            assert popped is not None and popper.size == 0

            flusher = WorkingMemory(memory, user_id="vivek_wm_flush")
            flusher.push("Important insight 1", tag="insight")
            flusher.push("Important insight 2", tag="insight")
            flushed = flusher.flush_to_longterm()
            assert flushed["flushed"] == 2
            time.sleep(0.5)

            found = memory.search("Important insight", user_id="vivek_wm_flush", limit=5).get("results", [])
            assert found, "flushed items not found in long-term memory"

            relevance = WorkingMemory(memory, user_id="vivek_wm_relevant")
            relevance.push("Fix authentication token refresh bug", tag="task")
            relevance.push("Database migration plan for Q2 2026", tag="plan")
            relevance.push("Team standup at 10am tomorrow", tag="reminder")
            relevant_items = relevance.get_relevant("authentication token")
            assert relevant_items, "expected relevant working-memory items"

        @step("Salience scoring and decay modifiers")
        def _():
            from dhee.core.salience import compute_salience, salience_decay_modifier

            neutral = compute_salience("The meeting is at 3pm in room 204")
            positive = compute_salience("We just achieved an amazing breakthrough on the project!")
            urgent = compute_salience("CRITICAL production crash! Emergency deployment needed immediately!")
            assert urgent["sal_salience_score"] > neutral["sal_salience_score"]
            assert positive["sal_salience_score"] >= neutral["sal_salience_score"]

            created = memory.add(
                "CRITICAL: Production database corruption! Emergency fix!",
                user_id="vivek",
                infer=False,
            )
            items = created.get("results", [])
            assert items and "id" in items[0]
            memory_id = items[0]["id"]

            salience = compute_salience(items[0].get("memory", ""))
            metadata = items[0].get("metadata", {}) or {}
            metadata.update(salience)
            memory.update(memory_id, {"metadata": metadata})

            updated = memory.get(memory_id)
            updated_md = updated.get("metadata", {}) or {}
            assert updated_md.get("sal_salience_score", 0) > 0

            high = salience_decay_modifier(1.0)
            mid = salience_decay_modifier(0.5)
            none = salience_decay_modifier(0.0)
            assert high < mid < none
            assert none == 1.0

        @step("Causal reasoning graph utilities")
        def _():
            from dhee.core.graph import KnowledgeGraph, RelationType, detect_causal_language

            graph = KnowledgeGraph()
            graph.add_relationship("mem_bug", "mem_investigation", RelationType.LED_TO)
            graph.add_relationship("mem_investigation", "mem_root_cause", RelationType.LED_TO)
            graph.add_relationship("mem_root_cause", "mem_fix", RelationType.LED_TO)
            graph.add_relationship("mem_fix", "mem_bug", RelationType.PREVENTS)

            stats = graph.stats()
            assert stats["total_relationships"] >= 4

            backward = graph.get_causal_chain("mem_fix", direction="backward", depth=5)
            forward = graph.get_causal_chain("mem_bug", direction="forward", depth=5)
            assert backward and forward

            found = detect_causal_language("The outage was caused by a misconfigured load balancer")
            assert RelationType.CAUSED_BY in found

        @step("AGI loop health and cycle execution")
        def _():
            from dhee.core.agi_loop import get_system_health, run_agi_cycle

            health = get_system_health(memory, user_id="vivek")
            assert health["total"] >= health["available"]

            cycle = run_agi_cycle(memory, user_id="vivek")
            summary = cycle.get("summary", {})
            assert summary.get("total_subsystems", 0) >= summary.get("ok", 0)

        @step("Heartbeat behaviors")
        def _():
            from engram_heartbeat.behaviors import BUILTIN_BEHAVIORS, run_behavior

            assert BUILTIN_BEHAVIORS
            for action in [
                "extract_procedures",
                "process_reconsolidation",
                "extract_antipatterns",
                "wm_decay",
                "agi_loop",
            ]:
                result = run_behavior(action, memory, {"user_id": "vivek"}, agent_id="test")
                assert result["status"] in {"ok", "skipped"}

        @step("Cross-feature search and inline configuration validation")
        def _():
            results = memory.search("debug authentication", user_id="vivek", limit=5).get("results", [])
            assert isinstance(results, list)

            config = MemoryConfig()
            configs = {
                "procedural": config.procedural.model_dump(),
                "reconsolidation": config.reconsolidation.model_dump(),
                "failure": config.failure.model_dump(),
                "working_memory": config.working_memory.model_dump(),
                "salience": config.salience.model_dump(),
                "causal": config.causal.model_dump(),
            }
            assert all(isinstance(values, dict) for values in configs.values())
            print(json.dumps(configs, indent=2)[:500])

    finally:
        memory.close()

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
    }


def test_live_e2e_all_features() -> None:
    results = run_live_e2e_suite()
    failures = results["errors"]
    if failures:
        summary = "\n".join(f"- {name}: {details.splitlines()[0]}" for name, details in failures)
        pytest.fail(
            f"live E2E suite reported {results['failed']} failed step(s) out of "
            f"{results['passed'] + results['failed']}:\n{summary}"
        )


if __name__ == "__main__":
    ensure_live_nvidia_runtime("openai")
    results = run_live_e2e_suite()
    total = results["passed"] + results["failed"]
    print(f"\nLive E2E summary: {results['passed']} passed, {results['failed']} failed, {total} total")
    raise SystemExit(1 if results["failed"] else 0)
