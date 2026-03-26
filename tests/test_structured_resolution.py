"""Test: structured write-time extraction → SQL read-time resolution.

Proves that storing facts at WRITE time and resolving with SQL at READ time
fixes the LongMemEval failure modes:
  1. Counting (multi-session) — "how many X?" → COUNT(DISTINCT canonical_key)
  2. Knowledge-update — "what is current X?" → latest valid_from, valid_until IS NULL
  3. Temporal — "when did X?" → time field from engram_facts
  4. Set members — "which X?" → DISTINCT values for predicate
"""

import json
import os
import sys
import tempfile
import uuid

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dhee.core.engram import (
    AssociativeLink,
    ContextAnchor,
    Fact,
    EntityRef,
    SceneSnapshot,
    UniversalEngram,
)
from dhee.core.engram_extractor import EngramExtractor
from dhee.core.resolvers import ContextResolver, QueryPlan


def setup_test_db(tmp_dir: str):
    """Create a fresh SQLite DB with v3 engram tables."""
    from dhee.db.sqlite import SQLiteManager

    db = SQLiteManager(db_path=os.path.join(tmp_dir, "test.db"))
    # Force v3 migration
    with db._get_connection() as conn:
        db._ensure_v3_universal_engram(conn)
    return db


def insert_memory(db, memory_id: str, content: str, user_id: str = "default"):
    """Insert a raw memory row (minimal — just enough for foreign keys)."""
    with db._get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO memories (id, memory, user_id, created_at, strength)
            VALUES (?, ?, ?, datetime('now'), 1.0)""",
            (memory_id, content, user_id),
        )


# ═══════════════════════════════════════════════════════════════════════
# Test 1: COUNTING — "How many countries have I visited?"
# ═══════════════════════════════════════════════════════════════════════

def test_counting():
    """The #1 failure mode: LLM says "3" when the answer is "5" because
    it misses facts spread across multiple sessions."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        # Simulate 5 separate conversation sessions mentioning travel
        sessions = [
            ("I visited Japan last spring, the cherry blossoms were amazing", "Japan"),
            ("My trip to France was wonderful, loved the Eiffel Tower", "France"),
            ("I went to Brazil for carnival, incredible experience", "Brazil"),
            ("Traveled to Kenya for a safari, saw lions up close", "Kenya"),
            ("Just got back from Iceland, the northern lights were unreal", "Iceland"),
        ]

        for i, (content, country) in enumerate(sessions):
            mid = f"mem_{i}"
            insert_memory(db, mid, content)

            engram = UniversalEngram(
                raw_content=content,
                facts=[Fact(
                    subject="user",
                    predicate="visited",
                    value=country,
                    canonical_key=f"user|visited|{country.lower()}",
                )],
                user_id="default",
            )
            resolver.store_engram(engram, mid)

        # SQL resolution: COUNT(DISTINCT canonical_key) WHERE predicate = 'visited'
        count = resolver.resolve_count("visited")
        assert count == 5, f"Expected 5 countries, got {count}"

        # Full resolution via QueryPlan
        plan = QueryPlan(intent="count", predicate="visited")
        result = resolver.resolve("How many countries have I visited?", query_plan=plan)
        assert result is not None
        assert result.answer == "5", f"Expected '5', got '{result.answer}'"
        assert result.resolver_path == "context->sql->count"

        # Also test set_members: WHICH countries?
        plan2 = QueryPlan(intent="set_members", predicate="visited")
        result2 = resolver.resolve("Which countries have I visited?", query_plan=plan2)
        assert result2 is not None
        members = result2.answer.split(", ")
        assert len(members) == 5, f"Expected 5 members, got {len(members)}: {members}"
        assert "Japan" in members
        assert "Iceland" in members

        print("✓ COUNTING: 5 countries correctly counted via SQL (no LLM needed)")


# ═══════════════════════════════════════════════════════════════════════
# Test 2: KNOWLEDGE UPDATE — "What editor do I currently use?"
# ═══════════════════════════════════════════════════════════════════════

def test_knowledge_update():
    """Knowledge changes over time. The LATEST value should win."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        # User changed editors over time
        changes = [
            ("I use Sublime Text for all my coding", "Sublime Text", "2023-01-01", "2023-06-15"),
            ("Switched to VS Code, much better extensions", "VS Code", "2023-06-15", "2024-03-01"),
            ("Just moved to Cursor, the AI integration is amazing", "Cursor", "2024-03-01", None),
        ]

        for i, (content, editor, valid_from, valid_until) in enumerate(changes):
            mid = f"mem_{i}"
            insert_memory(db, mid, content)

            engram = UniversalEngram(
                raw_content=content,
                facts=[Fact(
                    subject="user",
                    predicate="uses_editor",
                    value=editor,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    canonical_key=f"user|uses_editor|{editor.lower().replace(' ', '_')}",
                )],
                user_id="default",
            )
            resolver.store_engram(engram, mid)

        # SQL resolution: WHERE valid_until IS NULL ORDER BY valid_from DESC
        plan = QueryPlan(intent="latest", subject="user", predicate="uses_editor")
        result = resolver.resolve("What editor do I currently use?", query_plan=plan)
        assert result is not None
        assert result.answer == "Cursor", f"Expected 'Cursor', got '{result.answer}'"
        assert result.resolver_path == "context->sql->latest"

        # Temporal sequence: show the evolution
        plan2 = QueryPlan(intent="temporal", subject="user", predicate="uses_editor")
        result2 = resolver.resolve("How has my editor changed?", query_plan=plan2)
        assert result2 is not None
        assert "Sublime Text" in result2.answer
        assert "Cursor" in result2.answer

        print("✓ KNOWLEDGE UPDATE: 'Cursor' correctly resolved as latest editor")


# ═══════════════════════════════════════════════════════════════════════
# Test 3: COUNTING WITH CONTEXT FILTER — "How many movies did I watch in 2024?"
# ═══════════════════════════════════════════════════════════════════════

def test_counting_with_context():
    """Counting with time-based context filtering."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        movies = [
            ("Watched Oppenheimer, great film", "Oppenheimer", "2023-08-15"),
            ("Saw Dune Part 2, amazing visuals", "Dune Part 2", "2024-03-10"),
            ("Watched The Fall Guy, fun movie", "The Fall Guy", "2024-05-20"),
            ("Saw Deadpool & Wolverine, hilarious", "Deadpool & Wolverine", "2024-07-26"),
            ("Watched Interstellar again", "Interstellar", "2024-11-05"),
        ]

        for i, (content, movie, date) in enumerate(movies):
            mid = f"mem_{i}"
            insert_memory(db, mid, content)

            engram = UniversalEngram(
                raw_content=content,
                context=ContextAnchor(
                    activity="movie",
                    time_absolute=date,
                ),
                facts=[Fact(
                    subject="user",
                    predicate="watched_movie",
                    value=movie,
                    time=date,
                    canonical_key=f"user|watched_movie|{movie.lower().replace(' ', '_')}",
                )],
                user_id="default",
            )
            resolver.store_engram(engram, mid)

        # Filter to 2024 only via context
        context_ids = resolver.filter_by_time_range("2024-01-01", "2024-12-31")
        assert len(context_ids) == 4, f"Expected 4 movies in 2024, context found {len(context_ids)}"

        count = resolver.resolve_count("watched_movie", context_ids)
        assert count == 4, f"Expected 4, got {count}"

        # Full resolution with context filter
        plan = QueryPlan(
            intent="count",
            predicate="watched_movie",
            context_filters={"time_range": ("2024-01-01", "2024-12-31")},
        )
        result = resolver.resolve("How many movies in 2024?", query_plan=plan)
        assert result is not None
        assert result.answer == "4", f"Expected '4', got '{result.answer}'"

        print("✓ COUNTING + CONTEXT: 4 movies in 2024 (not 5 total)")


# ═══════════════════════════════════════════════════════════════════════
# Test 4: RULE-BASED EXTRACTION (no LLM needed for simple patterns)
# ═══════════════════════════════════════════════════════════════════════

def test_rule_based_extraction():
    """EngramExtractor rule-based path should catch common patterns."""
    extractor = EngramExtractor(llm=None)  # No LLM

    engram = extractor.extract("I prefer Python over JavaScript for backend work")
    assert len(engram.facts) >= 1, f"Expected at least 1 fact, got {len(engram.facts)}"
    predicates = [f.predicate for f in engram.facts]
    assert "prefers" in predicates, f"Expected 'prefers' predicate, got {predicates}"

    engram2 = extractor.extract("I visited Tokyo, Paris, and London last year")
    visited_facts = [f for f in engram2.facts if f.predicate == "visited"]
    # Rule-based may not catch all 3 as separate facts from this phrasing,
    # but LLM extraction would. This tests the baseline.
    print(f"  Rule-based extraction found {len(visited_facts)} visited facts "
          f"(LLM would find 3)")

    engram3 = extractor.extract("I switched to Cursor because AI integration is better")
    switched = [f for f in engram3.facts if f.predicate == "switched_to"]
    assert len(switched) >= 1, f"Expected switched_to fact, got {[f.predicate for f in engram3.facts]}"
    assert "Cursor" in switched[0].value

    print("✓ RULE-BASED EXTRACTION: preferences and switches detected without LLM")


# ═══════════════════════════════════════════════════════════════════════
# Test 5: SUM — "How much have I spent on subscriptions?"
# ═══════════════════════════════════════════════════════════════════════

def test_sum_resolution():
    """Numeric aggregation over facts."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        subscriptions = [
            ("Netflix subscription $15.99/month", "Netflix", 15.99),
            ("Spotify premium $9.99/month", "Spotify", 9.99),
            ("GitHub Copilot $10/month", "GitHub Copilot", 10.0),
            ("Claude Pro $20/month", "Claude Pro", 20.0),
        ]

        for i, (content, service, cost) in enumerate(subscriptions):
            mid = f"mem_{i}"
            insert_memory(db, mid, content)

            engram = UniversalEngram(
                raw_content=content,
                facts=[Fact(
                    subject="user",
                    predicate="subscribes_to",
                    value=service,
                    value_numeric=cost,
                    value_unit="usd_monthly",
                    canonical_key=f"user|subscribes_to|{service.lower().replace(' ', '_')}",
                )],
                user_id="default",
            )
            resolver.store_engram(engram, mid)

        # Count subscriptions
        count = resolver.resolve_count("subscribes_to")
        assert count == 4, f"Expected 4 subscriptions, got {count}"

        # Sum costs
        total = resolver.resolve_sum("subscribes_to", "usd_monthly")
        assert abs(total - 55.98) < 0.01, f"Expected ~55.98, got {total}"

        plan = QueryPlan(intent="sum", predicate="subscribes_to")
        result = resolver.resolve("How much on subscriptions?", query_plan=plan)
        assert result is not None
        assert float(result.answer) > 55, f"Expected >55, got {result.answer}"

        print("✓ SUM: $55.98/month total subscriptions via SQL aggregation")


# ═══════════════════════════════════════════════════════════════════════
# Test 6: DEDUP via canonical_key — same fact stored twice
# ═══════════════════════════════════════════════════════════════════════

def test_canonical_key_dedup():
    """Same fact from two different sessions should count as ONE."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        # User mentions visiting Japan in two separate conversations
        sessions = [
            "I loved my trip to Japan, the food was incredible",
            "Remember when I went to Japan? The sushi was amazing",
        ]

        for i, content in enumerate(sessions):
            mid = f"mem_{i}"
            insert_memory(db, mid, content)

            engram = UniversalEngram(
                raw_content=content,
                facts=[Fact(
                    subject="user",
                    predicate="visited",
                    value="Japan",
                    canonical_key="user|visited|japan",  # SAME key for both
                )],
                user_id="default",
            )
            resolver.store_engram(engram, mid)

        # COUNT(DISTINCT canonical_key) should be 1, not 2
        count = resolver.resolve_count("visited")
        assert count == 1, f"Expected 1 (deduplicated), got {count}"

        print("✓ DEDUP: Same fact from 2 sessions → canonical_key counts as 1")


# ═══════════════════════════════════════════════════════════════════════
# Test 7: ERA-BASED CONTEXT FILTER — "What did I do during school?"
# ═══════════════════════════════════════════════════════════════════════

def test_era_context_filter():
    """Context anchoring: filter by life era."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        memories = [
            ("Played cricket every Saturday", "school", "played_sport", "Cricket"),
            ("Won the science fair in class 10", "school", "won_award", "Science Fair"),
            ("Got my first job at TCS", "work", "works_at", "TCS"),
            ("Started learning Kubernetes at work", "work", "learned", "Kubernetes"),
        ]

        for i, (content, era, predicate, value) in enumerate(memories):
            mid = f"mem_{i}"
            insert_memory(db, mid, content)

            engram = UniversalEngram(
                raw_content=content,
                context=ContextAnchor(era=era),
                facts=[Fact(
                    subject="user",
                    predicate=predicate,
                    value=value,
                    canonical_key=f"user|{predicate}|{value.lower().replace(' ', '_')}",
                )],
                user_id="default",
            )
            resolver.store_engram(engram, mid)

        # Filter to school era only
        school_ids = resolver.filter_by_era("school")
        assert len(school_ids) == 2, f"Expected 2 school memories, got {len(school_ids)}"

        work_ids = resolver.filter_by_era("work")
        assert len(work_ids) == 2, f"Expected 2 work memories, got {len(work_ids)}"

        print("✓ ERA FILTER: school=2, work=2 — context-first narrowing works")


def test_resolution_is_scoped_per_user():
    """Structured resolution must not read another user's facts."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        datasets = [
            ("default", "mem_default_1", "Japan"),
            ("other", "mem_other_1", "France"),
            ("other", "mem_other_2", "Brazil"),
        ]

        for user_id, memory_id, country in datasets:
            insert_memory(db, memory_id, f"I visited {country}", user_id=user_id)
            resolver.store_engram(
                UniversalEngram(
                    raw_content=f"I visited {country}",
                    facts=[Fact(
                        subject="user",
                        predicate="visited",
                        value=country,
                        canonical_key=f"user|visited|{country.lower()}",
                    )],
                    user_id=user_id,
                ),
                memory_id,
            )

        default_result = resolver.resolve(
            "How many countries have I visited?",
            query_plan=QueryPlan(intent="count", predicate="visited"),
            user_id="default",
        )
        other_result = resolver.resolve(
            "How many countries have I visited?",
            query_plan=QueryPlan(intent="count", predicate="visited"),
            user_id="other",
        )

        assert default_result is not None
        assert other_result is not None
        assert default_result.answer == "1"
        assert other_result.answer == "2"

        print("✓ USER SCOPE: deterministic resolution stays inside one user's memories")


def test_tombstone_cleanup_removes_structured_artifacts():
    """Deleting a memory must remove all resolver-facing structured rows."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        memory_id = "mem_cleanup"
        insert_memory(db, memory_id, "I visited Tokyo during school", user_id="default")
        resolver.store_engram(
            UniversalEngram(
                raw_content="I visited Tokyo during school",
                context=ContextAnchor(era="school", place="Tokyo"),
                scene=SceneSnapshot(setting="Tokyo station"),
                facts=[Fact(
                    subject="user",
                    predicate="visited",
                    value="Tokyo",
                    canonical_key="user|visited|tokyo",
                )],
                entities=[EntityRef(name="Tokyo", entity_type="location")],
                links=[AssociativeLink(target_canonical_key="user|visited|tokyo_trip")],
                user_id="default",
            ),
            memory_id,
        )

        assert db.delete_memory(memory_id, use_tombstone=True) is True

        with db._get_connection() as conn:
            table_counts = {
                "engram_context": conn.execute(
                    "SELECT COUNT(*) AS cnt FROM engram_context WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()["cnt"],
                "engram_scenes": conn.execute(
                    "SELECT COUNT(*) AS cnt FROM engram_scenes WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()["cnt"],
                "engram_facts": conn.execute(
                    "SELECT COUNT(*) AS cnt FROM engram_facts WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()["cnt"],
                "engram_entities": conn.execute(
                    "SELECT COUNT(*) AS cnt FROM engram_entities WHERE memory_id = ?",
                    (memory_id,),
                ).fetchone()["cnt"],
                "engram_links": conn.execute(
                    "SELECT COUNT(*) AS cnt FROM engram_links WHERE source_memory_id = ?",
                    (memory_id,),
                ).fetchone()["cnt"],
            }
            tombstone = conn.execute(
                "SELECT tombstone FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()["tombstone"]

        assert tombstone == 1
        assert all(count == 0 for count in table_counts.values()), table_counts
        assert resolver.resolve(
            "How many countries have I visited?",
            query_plan=QueryPlan(intent="count", predicate="visited"),
            user_id="default",
        ) is None

        print("✓ TOMBSTONE CLEANUP: deleted memories no longer participate in SQL resolution")


def test_deterministic_resolution_requires_active_supporting_memory():
    """Orphaned structured facts must not produce deterministic answers."""
    with tempfile.TemporaryDirectory() as tmp:
        db = setup_test_db(tmp)
        resolver = ContextResolver(db)

        with db._get_connection() as conn:
            conn.execute(
                """INSERT INTO engram_facts
                (id, memory_id, subject, predicate, value, canonical_key)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    "missing_memory",
                    "user",
                    "visited",
                    "Japan",
                    "user|visited|japan",
                ),
            )

        result = resolver.resolve(
            "How many countries have I visited?",
            query_plan=QueryPlan(intent="count", predicate="visited"),
            user_id="default",
        )

        assert result is None

        print("✓ GROUNDING: orphaned facts cannot short-circuit retrieval")


def main():
    print("=" * 70)
    print("STRUCTURED WRITE → SQL READ: End-to-End Resolution Tests")
    print("Testing if write-time fact extraction fixes LongMemEval failures")
    print("=" * 70)
    print()

    tests = [
        ("Counting (multi-session)", test_counting),
        ("Knowledge update (latest value)", test_knowledge_update),
        ("Counting + context filter", test_counting_with_context),
        ("Rule-based extraction", test_rule_based_extraction),
        ("Sum aggregation", test_sum_resolution),
        ("Canonical key dedup", test_canonical_key_dedup),
        ("Era-based context filter", test_era_context_filter),
        ("User-scoped resolution", test_resolution_is_scoped_per_user),
        ("Tombstone cleanup", test_tombstone_cleanup_removes_structured_artifacts),
        ("Grounding required", test_deterministic_resolution_requires_active_supporting_memory),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"✗ {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
        print()

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")
    if failed == 0:
        print("\nALL TESTS PASS — structured resolution fixes counting, ")
        print("knowledge-update, temporal, and aggregation failures.")
        print("No LLM needed at read time for these query types.")
    print("=" * 70)


if __name__ == "__main__":
    main()
