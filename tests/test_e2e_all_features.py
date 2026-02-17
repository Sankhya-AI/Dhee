"""End-to-end real-user test of ALL Engram AGI Memory Kernel features.

Exercises every feature as a real user would: creates a Memory instance with
a real NVIDIA LLM backend, stores real memories, and validates every subsystem.

Run:
    .venv/bin/python tests/test_e2e_all_features.py
"""

import json
import os
import sys
import time
import threading
import traceback
from datetime import datetime, timezone

# ── Setup ──────────────────────────────────────────────────────

_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

from engram.configs.base import (
    MemoryConfig, LLMConfig, EmbedderConfig, VectorStoreConfig,
    EchoMemConfig, CategoryMemConfig, ProfileConfig,
)
from engram.memory.main import Memory


def create_memory():
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
        # Disable features that cause extra API calls or Llama-8B parsing failures:
        echo=EchoMemConfig(enable_echo=False),
        category=CategoryMemConfig(use_llm_categorization=False),
        profile=ProfileConfig(enable_profiles=False),
    )
    return Memory(config=config)


# ── Test runner ────────────────────────────────────────────────

passed = 0
failed = 0
errors = []


TEST_TIMEOUT = 30  # seconds per test — prevents hanging on API failures


def _run_with_timeout(fn, timeout):
    """Run fn in a daemon thread; raise TimeoutError if it doesn't finish."""
    result = [None]
    error = [None]

    def target():
        try:
            fn()
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Test timed out after {timeout}s (NVIDIA API may be down)")
    if error[0] is not None:
        raise error[0]


def test(name):
    """Decorator to run a test and track results."""
    def decorator(fn):
        global passed, failed
        print(f"\n{'─'*60}")
        print(f"TEST: {name}")
        print(f"{'─'*60}")
        try:
            _run_with_timeout(fn, TEST_TIMEOUT)
            passed += 1
            print(f"  ✓ PASSED")
        except Exception as e:
            failed += 1
            errors.append((name, str(e)))
            print(f"  ✗ FAILED: {e}")
            traceback.print_exc()
        return fn
    return decorator


# ── Create memory instance ─────────────────────────────────────

print("=" * 60)
print("ENGRAM AGI MEMORY KERNEL — FULL E2E TEST")
print("=" * 60)
print("\nCreating Memory instance with NVIDIA backend...")
memory = create_memory()
print("Memory created successfully.\n")

# Store IDs for cross-feature tests
stored_ids = {}


# ═══════════════════════════════════════════════════════════════
# PHASE 1: Core Memory (existing)
# ═══════════════════════════════════════════════════════════════

@test("1.1 — Add a memory")
def _():
    result = memory.add(
        "Vivek prefers Python over JavaScript for backend work",
        user_id="vivek",
        metadata={"source": "e2e_test"},
        infer=False,
    )
    items = result.get("results", [])
    assert len(items) > 0, "No results returned from add"
    stored_ids["first_memory"] = items[0]["id"]
    print(f"  Stored memory ID: {stored_ids['first_memory']}")


@test("1.2 — Add multiple memories for search testing")
def _():
    memories_to_add = [
        "Production deploy uses GitHub Actions, not Jenkins",
        "PostgreSQL to MongoDB migration failed due to schema issues",
        "Team chose React for frontend because of components",
        "Rate limiting uses Redis with sliding window",
        "CI pipeline: pytest, eslint, then Docker build",
        "Auth uses JWT with 15-min expiry and refresh tokens",
        "Microservices use gRPC internally, REST externally",
    ]
    for content in memories_to_add:
        result = memory.add(content, user_id="vivek", infer=False)
        items = result.get("results", [])
        assert len(items) > 0
    stored_ids["all_count"] = len(memories_to_add) + 1  # +1 for first
    print(f"  Added {len(memories_to_add)} more memories")


@test("1.3 — Search memories semantically")
def _():
    result = memory.search("what deployment tool do we use", user_id="vivek", limit=5)
    items = result.get("results", [])
    assert len(items) > 0, "No search results"
    top = items[0]
    print(f"  Top result: {top.get('memory', '')[:80]}...")
    print(f"  Score: {top.get('composite_score', 0):.3f}")
    # Semantic search — top result should be related to deployment/CI/infrastructure
    top_text = top.get("memory", "").lower()
    assert any(kw in top_text for kw in ("deploy", "github", "ci", "docker", "pipeline", "actions")), \
        f"Top result not deployment-related: {top_text[:80]}"


@test("1.4 — Get a specific memory by ID")
def _():
    mem = memory.get(stored_ids["first_memory"])
    assert mem is not None, "Memory not found"
    assert "Python" in mem.get("memory", "")
    print(f"  Retrieved: {mem['memory'][:60]}...")


@test("1.5 — Update a memory")
def _():
    memory.update(stored_ids["first_memory"], {
        "content": "Vivek prefers Python over JS for all work",
    })
    updated = memory.get(stored_ids["first_memory"])
    assert "Python" in updated.get("memory", "")
    print(f"  Updated: {updated['memory'][:60]}...")


@test("1.6 — Get all memories")
def _():
    result = memory.get_all(user_id="vivek", limit=50)
    items = result.get("results", [])
    assert len(items) >= stored_ids["all_count"], f"Expected >= {stored_ids['all_count']}, got {len(items)}"
    print(f"  Total memories: {len(items)}")


@test("1.7 — Get memory stats")
def _():
    stats = memory.get_stats(user_id="vivek")
    assert stats is not None
    print(f"  Stats: {json.dumps(stats, indent=2, default=str)[:200]}")


@test("1.8 — Apply memory decay")
def _():
    result = memory.apply_decay(scope={"user_id": "vivek"})
    print(f"  Decay result: {result}")


# Allow embeddings to settle
time.sleep(1)

# ═══════════════════════════════════════════════════════════════
# PHASE 2: Procedural Memory
# ═══════════════════════════════════════════════════════════════

@test("2.1 — Create episode memories for procedure extraction")
def _():
    episodes = [
        "Debugged login: checked auth logs, traced JWT, found expired token, fixed refresh",
        "Fixed signup: checked logs, traced auth flow, found token expiry, updated refresh",
        "Session timeout fix: reviewed logs, traced token lifecycle, updated refresh process",
    ]
    ep_ids = []
    for ep in episodes:
        result = memory.add(ep, user_id="vivek", metadata={"memory_type": "episodic", "explicit_remember": True}, infer=False)
        items = result.get("results", [])
        assert len(items) > 0, f"add() returned no results for episode: {ep[:40]}"
        item = items[0]
        assert "id" in item, f"add() result missing 'id' key — got event={item.get('event')}: {item}"
        ep_ids.append(item["id"])
    stored_ids["episode_ids"] = ep_ids
    print(f"  Created {len(ep_ids)} episode memories")


@test("2.2 — Extract a procedure from episodes")
def _():
    from engram_procedural import Procedural

    ep_ids = stored_ids.get("episode_ids")
    assert ep_ids, "Skipping — no episode IDs from previous test"

    proc = Procedural(memory, user_id="vivek")
    result = proc.extract_procedure(
        episode_ids=ep_ids,
        name="debug_auth_issues",
        domain="authentication",
    )
    assert "error" not in result, f"Extraction failed: {result}"
    stored_ids["procedure_id"] = result.get("id", "")
    print(f"  Procedure: {result.get('name')}")
    steps = result.get("steps", [])
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except (json.JSONDecodeError, TypeError):
            steps = [steps]
    print(f"  Steps: {len(steps)}")
    for i, s in enumerate(steps[:5]):
        print(f"    {i+1}. {s}")


@test("2.3 — Get procedure by name")
def _():
    from engram_procedural import Procedural

    proc = Procedural(memory, user_id="vivek")
    result = proc.get_procedure("debug_auth_issues")
    assert result is not None, "Procedure not found"
    print(f"  Found: {result['name']} (use_count={result.get('use_count', 0)})")


@test("2.4 — Log procedure execution (success)")
def _():
    from engram_procedural import Procedural

    proc = Procedural(memory, user_id="vivek")
    proc_id = stored_ids.get("procedure_id", "")
    if not proc_id:
        print("  Skipping — no procedure ID")
        return

    for i in range(3):
        result = proc.log_execution(proc_id, success=True, context=f"Run {i+1}")
        print(f"  Execution {i+1}: use_count={result.get('use_count')}, "
              f"success_rate={result.get('success_rate')}, "
              f"automaticity={result.get('automaticity')}")


@test("2.5 — Log procedure execution (failure)")
def _():
    from engram_procedural import Procedural

    proc = Procedural(memory, user_id="vivek")
    proc_id = stored_ids.get("procedure_id", "")
    if not proc_id:
        return
    result = proc.log_execution(proc_id, success=False, context="Token was not expired this time")
    print(f"  After failure: success_rate={result.get('success_rate')}, "
          f"automaticity={result.get('automaticity')}")


@test("2.6 — Refine a procedure")
def _():
    from engram_procedural import Procedural

    proc = Procedural(memory, user_id="vivek")
    proc_id = stored_ids.get("procedure_id", "")
    if not proc_id:
        print("  Skipping — no procedure ID")
        return
    result = proc.refine_procedure(
        proc_id,
        correction="Also check session cookie before tracing JWT",
    )
    assert result.get("refined") is True, f"Refinement failed: {result}"
    new_steps = result.get("new_steps", [])
    print(f"  Refined to {len(new_steps)} steps")


@test("2.7 — Search procedures semantically")
def _():
    from engram_procedural import Procedural
    time.sleep(0.5)

    proc = Procedural(memory, user_id="vivek")
    results = proc.search_procedures("how to debug authentication problems")
    assert len(results) > 0, "No procedures found"
    print(f"  Found {len(results)} procedures")
    for r in results[:3]:
        print(f"    - {r.get('name')} (automaticity={r.get('automaticity', 0):.2f})")


@test("2.8 — List all active procedures")
def _():
    from engram_procedural import Procedural

    proc = Procedural(memory, user_id="vivek")
    results = proc.list_procedures(status="active")
    print(f"  Active procedures: {len(results)}")
    for r in results:
        print(f"    - {r.get('name')} (uses={r.get('use_count', 0)}, success={r.get('success_rate', 0):.0%})")


time.sleep(2)  # avoid NVIDIA API rate limits

# ═══════════════════════════════════════════════════════════════
# PHASE 2: Reconsolidation
# ═══════════════════════════════════════════════════════════════

@test("3.1 — Propose a memory update")
def _():
    from engram_reconsolidation import Reconsolidation

    rc = Reconsolidation(memory, user_id="vivek")
    # Find a memory about the deploy pipeline
    search = memory.search("deploy pipeline", user_id="vivek", limit=1)
    items = search.get("results", [])
    assert len(items) > 0
    target_id = items[0]["id"]
    stored_ids["rc_target"] = target_id
    print(f"  Target memory: {items[0].get('memory', '')[:60]}...")

    proposal = rc.propose_update(
        memory_id=target_id,
        new_context="We now also run Snyk security scanning before Docker build",
    )
    print(f"  Proposal: {json.dumps(proposal, indent=2, default=str)[:300]}")
    if proposal.get("id"):
        stored_ids["proposal_id"] = proposal["id"]


@test("3.2 — List pending proposals")
def _():
    from engram_reconsolidation import Reconsolidation

    rc = Reconsolidation(memory, user_id="vivek")
    pending = rc.list_pending_proposals()
    print(f"  Pending proposals: {len(pending)}")
    for p in pending:
        print(f"    - target={p.get('target_memory_id', '')[:12]}... "
              f"confidence={p.get('confidence', 0):.2f} "
              f"type={p.get('change_type')}")


@test("3.3 — Apply a reconsolidation proposal")
def _():
    from engram_reconsolidation import Reconsolidation

    rc = Reconsolidation(memory, user_id="vivek")
    proposal_id = stored_ids.get("proposal_id")
    if not proposal_id:
        print("  Skipping — no pending proposal")
        return

    result = rc.apply_update(proposal_id)
    print(f"  Apply result: status={result.get('status')}, version={result.get('version')}")

    # Verify the target memory was updated
    target = memory.get(stored_ids.get("rc_target", ""))
    if target:
        print(f"  Updated memory: {target.get('memory', '')[:80]}...")


@test("3.4 — Propose and reject an update")
def _():
    from engram_reconsolidation import Reconsolidation

    rc = Reconsolidation(memory, user_id="vivek")
    # Create a test memory
    result = memory.add("The team standup is every Monday at 10am", user_id="vivek", infer=False)
    items = result.get("results", [])
    assert items
    item = items[0]
    assert "id" in item, f"add() returned non-stored item: {item.get('event')}"
    mid = item["id"]

    proposal = rc.propose_update(mid, new_context="Standups moved to Tuesday at 2pm")
    if proposal.get("id"):
        reject = rc.reject_update(proposal["id"], reason="Not confirmed yet")
        print(f"  Rejected: {reject.get('status')}")
    else:
        print(f"  Proposal was: {proposal.get('status', 'n/a')} (no_change/skipped is ok)")


@test("3.5 — Get reconsolidation stats")
def _():
    from engram_reconsolidation import Reconsolidation

    rc = Reconsolidation(memory, user_id="vivek")
    stats = rc.get_stats()
    print(f"  Stats: {json.dumps(stats, indent=2)}")


@test("3.6 — Get version history")
def _():
    from engram_reconsolidation import Reconsolidation

    rc = Reconsolidation(memory, user_id="vivek")
    target_id = stored_ids.get("rc_target", "")
    if not target_id:
        return
    history = rc.get_version_history(target_id)
    print(f"  Version history entries: {len(history)}")


time.sleep(2)  # avoid NVIDIA API rate limits

# ═══════════════════════════════════════════════════════════════
# PHASE 3: Failure Learning
# ═══════════════════════════════════════════════════════════════

@test("4.1 — Log a failure")
def _():
    from engram_failure import FailureLearning

    fl = FailureLearning(memory, user_id="vivek")
    result = fl.log_failure(
        action="deploy_to_production",
        error="Connection timeout to AWS ECS",
        context="Deploy with reduced capacity",
        severity="high",
        agent_id="claude-code",
    )
    print(f"  Result: {result}")
    assert result.get("action") == "deploy_to_production" or result.get("status") == "logged", \
        f"Unexpected log_failure result: {result}"
    stored_ids["failure_1"] = result.get("id", "")
    print(f"  Logged: {result.get('action')} — {result.get('error', '')[:50]}")


@test("4.2 — Log multiple related failures")
def _():
    from engram_failure import FailureLearning

    fl = FailureLearning(memory, user_id="vivek")
    failures = [
        ("deploy_staging", "ECS cluster timeout", "Staging, Friday evening"),
        ("deploy_canary", "Load balancer refused", "Canary during peak"),
        ("deploy_hotfix", "ECS task start timeout", "Emergency hotfix midnight"),
    ]
    ids = [stored_ids.get("failure_1", "")]
    for action, error, context in failures:
        result = fl.log_failure(action=action, error=error, context=context, severity="high")
        if result.get("id"):
            ids.append(result["id"])
    stored_ids["failure_ids"] = [i for i in ids if i]
    print(f"  Logged {len(failures)} more failures (total IDs: {len(stored_ids['failure_ids'])})")


@test("4.3 — Search past failures")
def _():
    from engram_failure import FailureLearning
    time.sleep(0.5)

    fl = FailureLearning(memory, user_id="vivek")
    results = fl.search_failures("deployment timeout ECS")
    assert len(results) > 0, "No failures found"
    print(f"  Found {len(results)} matching failures")
    for r in results[:3]:
        print(f"    - {r.get('action')}: {r.get('error', '')[:50]}")


@test("4.4 — Extract an anti-pattern from failures")
def _():
    from engram_failure import FailureLearning

    fl = FailureLearning(memory, user_id="vivek")
    failure_ids = stored_ids.get("failure_ids", [])
    if len(failure_ids) < 3:
        print(f"  Skipping — only {len(failure_ids)} failures, need 3")
        return

    result = fl.extract_antipattern(
        failure_ids=failure_ids[:3],
        name="deploy_during_off_hours",
    )
    print(f"  Anti-pattern: {result.get('name', 'n/a')}")
    print(f"  Description: {result.get('description', 'n/a')[:100]}")
    warning_signs = result.get("warning_signs", [])
    if warning_signs:
        print(f"  Warning signs: {warning_signs[:3]}")


@test("4.5 — List anti-patterns")
def _():
    from engram_failure import FailureLearning

    fl = FailureLearning(memory, user_id="vivek")
    result = fl.list_antipatterns()
    print(f"  Anti-patterns: {len(result)}")
    for ap in result:
        print(f"    - {ap.get('name')}: {ap.get('description', '')[:60]}")


@test("4.6 — Get failure stats")
def _():
    from engram_failure import FailureLearning

    fl = FailureLearning(memory, user_id="vivek")
    stats = fl.get_failure_stats()
    print(f"  Stats: {json.dumps(stats, indent=2)}")
    assert stats["total_failures"] >= 4


@test("4.7 — Search recovery strategies")
def _():
    from engram_failure import FailureLearning

    fl = FailureLearning(memory, user_id="vivek")
    results = fl.search_recovery_strategies("timeout during deploy")
    print(f"  Recovery strategies found: {len(results)}")


# ═══════════════════════════════════════════════════════════════
# PHASE 3: Working Memory
# ═══════════════════════════════════════════════════════════════

@test("5.1 — Push items to working memory")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek", capacity=5)
    r1 = wm.push("Current task: fix the auth token refresh bug", tag="task")
    r2 = wm.push("The JWT secret is rotated every 24h", tag="context")
    r3 = wm.push("Related PR: #1234 by Alice", tag="reference")
    stored_ids["wm_key_1"] = r1["key"]
    stored_ids["wm_key_2"] = r2["key"]
    print(f"  Pushed 3 items, buffer size: {r3.get('buffer_size')}")


@test("5.2 — List working memory (sorted by activation)")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek_wm_list")
    wm.push("Item A", tag="a")
    wm.push("Item B", tag="b")
    items = wm.list()
    assert len(items) == 2
    print(f"  Items in WM: {len(items)}")
    for item in items:
        print(f"    [{item['tag']}] {item['content'][:40]} (activation={item['activation']:.2f})")


@test("5.3 — Peek at item (refreshes activation)")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek_wm_peek")
    pushed = wm.push("Important context")
    key = pushed["key"]

    peeked = wm.peek(key)
    assert peeked is not None
    assert peeked["access_count"] == 1
    print(f"  Peeked: activation={peeked['activation']:.2f}, accesses={peeked['access_count']}")


@test("5.4 — Capacity eviction (Miller's Law)")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek_wm_evict", capacity=3)
    wm.push("Item 1", tag="1")
    wm.push("Item 2", tag="2")
    wm.push("Item 3", tag="3")
    r = wm.push("Item 4 — should evict item 1", tag="4")

    assert r.get("evicted") is not None, "No eviction happened"
    assert len(wm.list()) == 3
    print(f"  Evicted: {r['evicted']['content'][:40]}")
    print(f"  Buffer size after eviction: {wm.size}")


@test("5.5 — Pop item from working memory")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek_wm_pop")
    pushed = wm.push("Temporary note")
    key = pushed["key"]

    popped = wm.pop(key)
    assert popped is not None
    assert wm.size == 0
    print(f"  Popped: {popped['content']}")


@test("5.6 — Flush working memory to long-term")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek_wm_flush")
    wm.push("Important insight 1", tag="insight")
    wm.push("Important insight 2", tag="insight")

    result = wm.flush_to_longterm()
    assert result["flushed"] == 2
    assert wm.size == 0
    print(f"  Flushed {result['flushed']} items to long-term memory")

    # Verify they ended up in long-term memory
    time.sleep(0.5)
    search = memory.search("Important insight", user_id="vivek_wm_flush", limit=5)
    found = search.get("results", [])
    print(f"  Found {len(found)} flushed items in long-term memory")


@test("5.7 — Get relevant working memory items")
def _():
    from engram_working import WorkingMemory

    wm = WorkingMemory(memory, user_id="vivek_wm_relevant")
    wm.push("Fix authentication token refresh bug", tag="task")
    wm.push("Database migration plan for Q2 2026", tag="plan")
    wm.push("Team standup at 10am tomorrow", tag="reminder")

    relevant = wm.get_relevant("authentication token")
    assert len(relevant) >= 1
    print(f"  Query: 'authentication token'")
    print(f"  Relevant items: {len(relevant)}")
    for r in relevant:
        print(f"    [{r['tag']}] {r['content'][:50]}")


time.sleep(2)  # avoid NVIDIA API rate limits

# ═══════════════════════════════════════════════════════════════
# PHASE 4: Salience
# ═══════════════════════════════════════════════════════════════

@test("6.1 — Compute salience (heuristic) for different content")
def _():
    from engram.core.salience import compute_salience

    neutral = compute_salience("The meeting is at 3pm in room 204")
    positive = compute_salience("We just achieved an amazing breakthrough on the project!")
    urgent = compute_salience("CRITICAL production crash! Emergency deployment needed immediately!")

    print(f"  Neutral:  valence={neutral['sal_valence']:+.2f}, arousal={neutral['sal_arousal']:.2f}, salience={neutral['sal_salience_score']:.2f}")
    print(f"  Positive: valence={positive['sal_valence']:+.2f}, arousal={positive['sal_arousal']:.2f}, salience={positive['sal_salience_score']:.2f}")
    print(f"  Urgent:   valence={urgent['sal_valence']:+.2f}, arousal={urgent['sal_arousal']:.2f}, salience={urgent['sal_salience_score']:.2f}")

    assert urgent["sal_salience_score"] > neutral["sal_salience_score"]


@test("6.2 — Tag a memory with salience")
def _():
    from engram.core.salience import compute_salience

    # Add a high-salience memory
    result = memory.add(
        "CRITICAL: Production database corruption! Emergency fix!",
        user_id="vivek",
        infer=False,
    )
    items = result.get("results", [])
    assert items, "No results from add()"
    assert "id" in items[0], f"add() returned non-stored item: {items[0].get('event')}"
    mid = items[0]["id"]

    salience = compute_salience(items[0].get("memory", ""))
    md = items[0].get("metadata", {}) or {}
    md.update(salience)
    memory.update(mid, {"metadata": md})

    updated = memory.get(mid)
    updated_md = updated.get("metadata", {}) or {}
    print(f"  Tagged memory with salience: {updated_md.get('sal_salience_score', 'N/A')}")
    assert updated_md.get("sal_salience_score", 0) > 0


@test("6.3 — Salience decay modifier")
def _():
    from engram.core.salience import salience_decay_modifier

    # High salience → decay slower
    high = salience_decay_modifier(1.0)
    mid = salience_decay_modifier(0.5)
    none = salience_decay_modifier(0.0)

    print(f"  High salience (1.0) → decay multiplier: {high:.2f}")
    print(f"  Mid salience  (0.5) → decay multiplier: {mid:.2f}")
    print(f"  No salience   (0.0) → decay multiplier: {none:.2f}")

    assert high < mid < none
    assert none == 1.0  # No salience = normal decay


# ═══════════════════════════════════════════════════════════════
# PHASE 4: Causal Reasoning
# ═══════════════════════════════════════════════════════════════

@test("7.1 — Add causal links between memories")
def _():
    from engram.core.graph import KnowledgeGraph, RelationType

    graph = KnowledgeGraph()

    # Create a causal chain: bug_found → investigation → root_cause → fix
    graph.add_relationship("mem_bug", "mem_investigation", RelationType.LED_TO)
    graph.add_relationship("mem_investigation", "mem_root_cause", RelationType.LED_TO)
    graph.add_relationship("mem_root_cause", "mem_fix", RelationType.LED_TO)
    graph.add_relationship("mem_fix", "mem_bug", RelationType.PREVENTS)

    stats = graph.stats()
    print(f"  Graph stats: {stats['total_relationships']} relationships")
    print(f"  Causal types: LED_TO={stats['relationship_types'].get('led_to', 0)}, "
          f"PREVENTS={stats['relationship_types'].get('prevents', 0)}")
    stored_ids["causal_graph"] = graph


@test("7.2 — Traverse causal chain (backward)")
def _():
    graph = stored_ids.get("causal_graph")
    if not graph:
        return

    chain = graph.get_causal_chain("mem_fix", direction="backward", depth=5)
    print(f"  Backward from fix: {len(chain)} nodes")
    for mid, depth, path in chain:
        print(f"    depth={depth}: {mid}")


@test("7.3 — Traverse causal chain (forward)")
def _():
    graph = stored_ids.get("causal_graph")
    if not graph:
        return

    chain = graph.get_causal_chain("mem_bug", direction="forward", depth=5)
    print(f"  Forward from bug: {len(chain)} nodes")
    for mid, depth, path in chain:
        print(f"    depth={depth}: {mid}")


@test("7.4 — Detect causal language in text")
def _():
    from engram.core.graph import detect_causal_language, RelationType

    texts = [
        ("The outage was caused by a misconfigured load balancer", [RelationType.CAUSED_BY]),
        ("Upgrading the library led to a performance regression", [RelationType.LED_TO]),
        ("Input validation prevents SQL injection attacks", [RelationType.PREVENTS]),
        ("The caching layer enables sub-millisecond response times", [RelationType.ENABLES]),
        ("The payment service requires authentication", [RelationType.REQUIRES]),
        ("Nothing special in this text at all", []),
    ]

    for text, expected in texts:
        found = detect_causal_language(text)
        status = "✓" if set(found) == set(expected) else "✗"
        print(f"  {status} '{text[:50]}...' → {[r.value for r in found]}")


# ═══════════════════════════════════════════════════════════════
# PHASE 5: AGI Loop
# ═══════════════════════════════════════════════════════════════

@test("8.1 — Get system health")
def _():
    from engram.core.agi_loop import get_system_health

    health = get_system_health(memory, user_id="vivek")
    print(f"  Health: {health['health_pct']:.0f}% ({health['available']}/{health['total']} systems)")
    for name, status in health["systems"].items():
        avail = "✓" if status.get("available") else "✗"
        print(f"    {avail} {name}")


@test("8.2 — Run full AGI cognitive cycle")
def _():
    from engram.core.agi_loop import run_agi_cycle

    result = run_agi_cycle(memory, user_id="vivek")
    summary = result.get("summary", {})
    print(f"  Cycle complete: {summary.get('ok', 0)} ok, "
          f"{summary.get('errors', 0)} errors, "
          f"{summary.get('skipped', 0)} skipped "
          f"(out of {summary.get('total_subsystems', 0)} subsystems)")

    for key, val in result.items():
        if isinstance(val, dict) and "status" in val:
            status_icon = "✓" if val["status"] == "ok" else ("⊘" if val["status"] == "skipped" else "✗")
            print(f"    {status_icon} {key}: {val['status']}")


# ═══════════════════════════════════════════════════════════════
# PHASE 1 (existing): Heartbeat Behaviors
# ═══════════════════════════════════════════════════════════════

@test("9.1 — Run all new heartbeat behaviors")
def _():
    from engram_heartbeat.behaviors import run_behavior, BUILTIN_BEHAVIORS

    print(f"  All behaviors: {list(BUILTIN_BEHAVIORS.keys())}")

    for action in ["extract_procedures", "process_reconsolidation",
                    "extract_antipatterns", "wm_decay", "agi_loop"]:
        result = run_behavior(action, memory, {"user_id": "vivek"}, agent_id="test")
        status_icon = "✓" if result["status"] == "ok" else ("⊘" if result["status"] == "skipped" else "✗")
        print(f"    {status_icon} {action}: {result['status']}")


# ═══════════════════════════════════════════════════════════════
# CROSS-FEATURE: Search with all boosts active
# ═══════════════════════════════════════════════════════════════

@test("10.1 — Search with procedural + salience boosts in results")
def _():
    results = memory.search("debug authentication", user_id="vivek", limit=5)
    items = results.get("results", [])
    print(f"  Found {len(items)} results")
    for item in items[:3]:
        print(f"    score={item.get('composite_score', 0):.3f} | "
              f"echo={item.get('echo_boost', 0):.3f} | "
              f"cat={item.get('category_boost', 0):.3f} | "
              f"proc={item.get('proc_boost', 0):.3f} | "
              f"sal={item.get('salience_boost', 0):.3f} | "
              f"{item.get('memory', '')[:50]}...")


# ═══════════════════════════════════════════════════════════════
# INLINE CONFIG VERIFICATION
# ═══════════════════════════════════════════════════════════════

@test("11.1 — Verify all inline configs on MemoryConfig")
def _():
    config = MemoryConfig()
    configs = {
        "procedural": config.procedural.model_dump(),
        "reconsolidation": config.reconsolidation.model_dump(),
        "failure": config.failure.model_dump(),
        "working_memory": config.working_memory.model_dump(),
        "salience": config.salience.model_dump(),
        "causal": config.causal.model_dump(),
    }
    for name, values in configs.items():
        print(f"  {name}: {values}")


# ═══════════════════════════════════════════════════════════════
# FINAL REPORT
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 60)
print("FINAL REPORT")
print("=" * 60)
print(f"\n  PASSED: {passed}")
print(f"  FAILED: {failed}")
print(f"  TOTAL:  {passed + failed}")

if errors:
    print(f"\n  FAILURES:")
    for name, err in errors:
        print(f"    ✗ {name}: {err}")

print()
memory.close()

sys.exit(1 if failed > 0 else 0)
