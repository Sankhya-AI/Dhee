#!/usr/bin/env python3
"""Seed demo data for testing the engram-bridge UI.

Creates a project, tasks on the board, a war room with messages,
and triggers routing — so every tab has something to show.

Usage:
    python scripts/seed_warroom_demo.py
    python scripts/seed_warroom_demo.py --base http://127.0.0.1:8200
"""

import argparse
import json
import time
import requests

BASE = "http://127.0.0.1:8200"


def api(method, path, body=None):
    url = f"{BASE}{path}"
    if method == "GET":
        r = requests.get(url)
    else:
        r = requests.post(url, json=body)
    if not r.ok:
        print(f"  WARN {method} {path} → {r.status_code}: {r.text[:120]}")
        return None
    return r.json()


def main():
    parser = argparse.ArgumentParser(description="Seed demo data for engram-bridge UI")
    parser.add_argument("--base", default="http://127.0.0.1:8200", help="Bridge base URL")
    args = parser.parse_args()
    global BASE
    BASE = args.base

    print(f"Seeding demo data on {BASE}...\n")

    # ── 1. Ensure a project exists ──
    print("1. Projects...")
    projects = api("GET", "/api/projects") or []
    if projects:
        project = projects[0]
        print(f"   Using existing project: {project['name']}")
    else:
        project = api("POST", "/api/projects", {
            "name": "Demo Project",
            "color": "#6366f1",
            "description": "Auto-seeded demo project",
        })
        if project:
            print(f"   Created project: {project['name']}")
        else:
            print("   Failed to create project, continuing anyway...")
            project = {"id": "unknown"}

    pid = project.get("id", "")

    # ── 2. Create tasks on the board ──
    print("\n2. Creating board tasks...")
    tasks = [
        {"title": "Refactor authentication middleware", "description": "Extract JWT validation into reusable middleware. Current code duplicates token parsing in 5 endpoints.", "priority": "high"},
        {"title": "Add WebSocket reconnection logic", "description": "Client should auto-reconnect with exponential backoff when WS drops.", "priority": "medium"},
        {"title": "Write integration tests for memory API", "description": "Cover add/search/decay lifecycle. Use pytest-asyncio.", "priority": "medium"},
        {"title": "Fix CSS overflow in chat view on mobile", "description": "Messages overflow the viewport on screens < 768px.", "priority": "low"},
        {"title": "Implement batch memory import endpoint", "description": "POST /api/memory/batch — accept array of memories, return created IDs.", "priority": "high"},
    ]
    created_tasks = []
    for t in tasks:
        result = api("POST", "/api/issues", {**t, "project_id": pid})
        if result:
            created_tasks.append(result)
            print(f"   + {t['title'][:50]}")

    # ── 3. Assign tasks to agents (claim directly) ──
    print("\n3. Assigning tasks to agents...")
    agents = ["claude-code", "codex"]
    for i, t in enumerate(created_tasks):
        agent = agents[i % len(agents)]
        r = api("POST", f"/api/coordination/claim/{t['id']}", {"agent_name": agent})
        if r:
            print(f"   Assigned: {t['title'][:40]} → {agent}")
        else:
            print(f"   WARN: Could not assign {t['title'][:40]}")

    # ── 4. Create a war room ──
    print("\n4. Creating war room...")
    room = api("POST", "/api/warrooms", {
        "topic": "Auth system redesign",
        "agenda": "Decide between JWT refresh tokens vs session-based auth. Need to consider: mobile clients, token rotation, revocation, and the existing middleware refactor task.",
        "created_by": "user",
    })
    if not room:
        print("   Failed to create war room")
        return

    room_id = room["id"]
    print(f"   Created room: {room['wr_topic']} ({room_id})")

    # Set monitor
    api("POST", f"/api/warrooms/{room_id}/monitor", {"agent_name": "claude-code"})
    print("   Set monitor: claude-code")

    # ── 5. Seed war room conversation ──
    print("\n5. Seeding war room messages...")
    messages = [
        ("user", "message", "We need to decide on the auth approach before the middleware refactor. Currently using stateless JWT but we're hitting issues with token revocation."),
        ("claude-code", "message", "I've analyzed the codebase. Current issues with JWT:\n\n1. No revocation mechanism — compromised tokens stay valid\n2. Token refresh requires full re-auth\n3. Mobile clients store tokens insecurely\n\n@delegate(codex, Review the current auth middleware in src/middleware/auth.py and list all endpoints that handle tokens)\n\nLet me outline the two approaches..."),
        ("codex", "message", "Reviewed `src/middleware/auth.py`. Found 5 endpoints handling tokens directly:\n- `POST /auth/login` — issues access + refresh tokens\n- `POST /auth/refresh` — validates refresh, issues new pair\n- `GET /auth/verify` — checks access token validity\n- `POST /auth/logout` — currently a no-op (stateless)\n- `DELETE /auth/sessions` — not implemented\n\nThe refresh endpoint has no rate limiting."),
        ("claude-code", "proposal", "**Proposal: Hybrid approach**\n\nKeep JWT for access tokens (short-lived, 15min) but add a server-side session store for refresh tokens:\n\n- Access tokens: stateless JWT, 15min expiry\n- Refresh tokens: opaque, stored in Redis, 7-day expiry\n- Revocation: delete from Redis (instant)\n- Mobile: refresh token in secure keychain, access in memory\n\nThis gives us the best of both worlds — fast stateless validation for most requests, with revocation capability via the refresh store."),
        ("user", "message", "I like the hybrid approach. What about the migration path? We have ~2000 active users with existing tokens."),
        ("claude-code", "message", "Good question. Migration plan:\n\n1. Deploy new auth alongside old (feature flag)\n2. Old tokens continue to work during transition\n3. On next refresh, users get new-style tokens automatically\n4. After 30 days, deprecate old endpoint\n\n@ask(codex, What's the estimated effort to add Redis as a dependency and implement the refresh token store?)\n\nI think we're close to a decision."),
        ("codex", "message", "Effort estimate:\n- Redis setup + connection pooling: 2 hours\n- Refresh token CRUD in Redis: 3 hours\n- Migration middleware (dual-mode): 4 hours\n- Tests: 3 hours\n- **Total: ~12 hours / 1.5 days**\n\nWe already have `redis` in requirements.txt (used by the cache layer), so no new dependency."),
        ("claude-code", "decision", "**Decision: Hybrid JWT + Redis refresh tokens**\n\nWe'll implement the hybrid approach with a phased migration:\n\n@decide(Adopt hybrid auth: stateless JWT access tokens (15min) + Redis-backed refresh tokens (7d). Migration via feature flag with 30-day transition window. Estimated effort: 1.5 days.)"),
    ]

    for sender, msg_type, content in messages:
        api("POST", f"/api/warrooms/{room_id}/messages", {
            "sender": sender,
            "content": content,
            "message_type": msg_type,
        })
        print(f"   [{sender}] {msg_type}: {content[:60]}...")
        time.sleep(0.1)  # slight delay for sequencing

    # Transition to discussing
    api("POST", f"/api/warrooms/{room_id}/transition", {"new_state": "discussing"})
    print("   State → discussing")

    # ── 6. Create a second war room (empty, for testing creation) ──
    print("\n6. Creating second war room...")
    room2 = api("POST", "/api/warrooms", {
        "topic": "Database migration strategy",
        "agenda": "Plan the migration from SQLite to PostgreSQL for production deployment.",
        "created_by": "user",
    })
    if room2:
        print(f"   Created: {room2['wr_topic']}")

    # ── 7. Add a memory entry ──
    print("\n7. Adding memory via chat...")
    # This just ensures the memory tab has something fresh
    # (memories are already populated from previous sessions)

    # ── Done ──
    print("\n" + "=" * 50)
    print("Demo data seeded successfully!")
    print(f"\nOpen {BASE} in your browser:")
    print(f"  Board:    {BASE}/board     — 5 new tasks")
    print(f"  Agents:   {BASE}/coordination — tasks ready to assign")
    print(f"  War Room: {BASE}/warroom   — 2 rooms, 1 with full conversation")
    print(f"  Settings: Click gear icon  — set default agent & supervisor")
    print("=" * 50)


if __name__ == "__main__":
    main()
