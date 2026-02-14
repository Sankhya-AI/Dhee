"""End-to-end real user test for Engram with NVIDIA APIs."""

import os
import sys
import tempfile
import time

# Load .env manually
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                os.environ[key] = val

from engram.configs.base import MemoryConfig
from engram.memory.main import Memory


def main():
    # Use a temp dir for all storage
    tmpdir = tempfile.mkdtemp(prefix="engram_test_")
    print(f"[setup] Temp dir: {tmpdir}")

    config = MemoryConfig(
        vector_store={"provider": "memory", "config": {}},
        llm={
            "provider": "nvidia",
            "config": {
                "model": "meta/llama-3.1-8b-instruct",
                "temperature": 0.2,
                "max_tokens": 1024,
                "timeout": 120,
            },
        },
        embedder={
            "provider": "nvidia",
            "config": {"model": "nvidia/nv-embed-v1"},
        },
        history_db_path=os.path.join(tmpdir, "history.db"),
        embedding_model_dims=4096,
        # Disable LLM-heavy features to reduce API calls & timeouts
        category={"enable_categories": True, "use_llm_categorization": False},
        echo={"enable_echo": True, "use_question_embedding": False},
        graph={"enable_graph": True, "use_llm_extraction": False},
        scene={"enable_scenes": True, "use_llm_summarization": False},
        profile={"enable_profiles": True, "use_llm_extraction": False},
    )

    print("[setup] Creating Memory instance...")
    mem = Memory(config)
    print("[setup] Memory ready.\n")

    # --- ADD MEMORIES ---
    messages = [
        {"role": "user", "content": "I just deployed our backend to production on AWS ECS. The service is running on Fargate with 2 tasks."},
        {"role": "assistant", "content": "Great! Running on Fargate with 2 tasks is a solid setup. Are you using an Application Load Balancer in front?"},
        {"role": "user", "content": "Yes, ALB with path-based routing. We also have a Redis cluster on ElastiCache for session caching."},
        {"role": "user", "content": "My favorite programming language is Python, and I've been using it for 8 years now."},
        {"role": "user", "content": "Last Friday we had a major outage because the database connection pool was exhausted. We fixed it by increasing max_connections to 200."},
    ]

    print("=== ADDING MEMORIES ===")
    for i, msg in enumerate(messages):
        t0 = time.time()
        try:
            result = mem.add(
                messages=[msg],
                user_id="test_user",
                metadata={"session_id": "test_session_1"},
            )
            elapsed = time.time() - t0
            added = result.get("results", [])
            print(f"  [{i+1}] Added {len(added)} memory(ies) in {elapsed:.1f}s")
            if added:
                m = added[0]
                print(f"       id={m.get('id','?')[:12]}... type={m.get('memory_type','?')} strength={m.get('strength','?')}")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [{i+1}] FAILED after {elapsed:.1f}s: {e}")

    # --- SEARCH ---
    print("\n=== SEARCH TESTS ===")
    queries = [
        ("What infrastructure do we use?", "semantic"),
        ("When did the outage happen?", "episodic"),
        ("Tell me about our deployment", "mixed"),
    ]

    for query, expected_intent in queries:
        t0 = time.time()
        try:
            results = mem.search(query=query, user_id="test_user", limit=3)
            elapsed = time.time() - t0
            hits = results.get("results", [])
            intent = hits[0].get("query_intent", "unknown") if hits else "no results"
            print(f"\n  Query: \"{query}\"")
            print(f"  Intent: {intent} (expected ~{expected_intent}) | {len(hits)} results in {elapsed:.1f}s")
            for j, hit in enumerate(hits):
                score = hit.get("score", 0)
                mem_type = hit.get("memory_type", "?")
                text = hit.get("memory", "")[:80]
                print(f"    [{j+1}] score={score:.3f} type={mem_type} | {text}...")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"\n  Query: \"{query}\" FAILED after {elapsed:.1f}s: {e}")

    # --- MULTI-TRACE STATE ---
    print("\n=== MULTI-TRACE CHECK ===")
    try:
        from engram.db.sqlite import SQLiteManager
        db = SQLiteManager(os.path.join(tmpdir, "history.db"))
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT id, memory_type, strength, s_fast, s_mid, s_slow FROM memories WHERE user_id='test_user' LIMIT 5"
            ).fetchall()
        for r in rows:
            print(f"  id={r['id'][:12]}... type={r['memory_type']} str={r['strength']:.3f} fast={r['s_fast']} mid={r['s_mid']} slow={r['s_slow']}")
        db.close()
    except Exception as e:
        print(f"  FAILED: {e}")

    # --- STATS ---
    print("\n=== STATS ===")
    try:
        stats = mem.get_stats(user_id="test_user")
        for k, v in stats.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for sk, sv in v.items():
                    print(f"    {sk}: {sv}")
            else:
                print(f"  {k}: {v}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # --- DECAY ---
    print("\n=== DECAY CYCLE ===")
    try:
        t0 = time.time()
        decay_result = mem.apply_decay(scope={"user_id": "test_user"})
        elapsed = time.time() - t0
        print(f"  Completed in {elapsed:.1f}s")
        for k, v in decay_result.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"  FAILED: {e}")

    print("\n=== ALL TESTS COMPLETE ===")


if __name__ == "__main__":
    main()
