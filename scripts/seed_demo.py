"""Seed engram-bridge with demo data for the dashboard walkthrough video.

Starts engram-bridge --channel web, waits for /health, then seeds:
  - 1 project ("Engram Core", #e8722a)
  - 10 issues across 4 statuses (Backlog / In Progress / In Review / Done)
  - 3 agents (claude-code, codex, gemini-agent)

Usage:
    python3 scripts/seed_demo.py          # start bridge + seed
    python3 scripts/seed_demo.py --no-start  # seed only (bridge already running)
"""

import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8200"


def wait_for_health(timeout: int = 30) -> bool:
    """Poll /health until it returns 200 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = urllib.request.urlopen(f"{BASE}/health", timeout=3)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    return False


def api(method: str, path: str, body: dict | None = None) -> dict:
    """Simple JSON API helper using only stdlib."""
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def seed():
    """Seed all demo data."""
    # ── Project ──
    print("  Creating project...")
    project = api("POST", "/api/projects", {
        "name": "Engram Core",
        "color": "#e8722a",
        "description": "Core memory engine — forgetting, encoding, retrieval",
    })
    pid = project["id"]
    print(f"  Project: {pid}")

    # ── Get default statuses ──
    statuses = api("GET", f"/api/projects/{pid}/statuses")
    status_map = {s["name"].lower(): s["id"] for s in statuses}
    print(f"  Statuses: {list(status_map.keys())}")

    # Map our 4-column names to whatever defaults exist
    def resolve_status(col: str) -> str:
        col_lower = col.lower()
        # Try exact match first
        if col_lower in status_map:
            return status_map[col_lower]
        # Fuzzy match
        for name, sid in status_map.items():
            if col_lower in name or name in col_lower:
                return sid
        # Fall back to first status
        return statuses[0]["id"] if statuses else ""

    # ── Tags ──
    print("  Creating tags...")
    tag_defs = [
        ("bug", "#ef4444"),
        ("feature", "#3b82f6"),
        ("performance", "#f59e0b"),
        ("testing", "#10b981"),
        ("docs", "#8b5cf6"),
    ]
    tag_map = {}
    for tname, tcolor in tag_defs:
        tag = api("POST", f"/api/projects/{pid}/tags", {"name": tname, "color": tcolor})
        tag_map[tname] = tag["id"]

    # ── Issues ──
    print("  Creating issues...")
    issues_data = [
        # Backlog (3)
        {
            "title": "Implement adaptive decay rate tuning",
            "description": "Auto-tune lambda based on access frequency patterns",
            "priority": "normal",
            "status_id": resolve_status("backlog"),
            "tags": ["feature"],
        },
        {
            "title": "Add OpenTelemetry tracing spans",
            "description": "Instrument memory add/search/decay with OTel spans for observability",
            "priority": "low",
            "status_id": resolve_status("backlog"),
            "tags": ["feature"],
        },
        {
            "title": "Write integration tests for Qdrant backend",
            "description": "End-to-end tests with a real Qdrant container",
            "priority": "normal",
            "status_id": resolve_status("backlog"),
            "tags": ["testing"],
        },
        # In Progress (3)
        {
            "title": "Fix Python import error in auth module",
            "description": "ImportError when running auth.py with new decorator pattern",
            "priority": "high",
            "status_id": resolve_status("in progress"),
            "tags": ["bug"],
        },
        {
            "title": "Optimize vector search for large collections",
            "description": "Search slows past 10k memories — profile and optimize HNSW params",
            "priority": "high",
            "status_id": resolve_status("in progress"),
            "tags": ["performance"],
        },
        {
            "title": "Add retry logic to embedding API calls",
            "description": "Embedding calls fail silently on timeout — add exponential backoff",
            "priority": "medium",
            "status_id": resolve_status("in progress"),
            "tags": ["bug"],
        },
        # In Review (2)
        {
            "title": "Scaffold React dashboard memory inspector",
            "description": "New page in web-ui to browse and search memory items visually",
            "priority": "normal",
            "status_id": resolve_status("review"),
            "tags": ["feature"],
        },
        {
            "title": "Document CLS distillation sleep cycle",
            "description": "Add architecture docs explaining episodic→semantic pipeline",
            "priority": "low",
            "status_id": resolve_status("review"),
            "tags": ["docs"],
        },
        # Done (2)
        {
            "title": "Implement EchoMem multi-modal encoding",
            "description": "Paraphrase, keywords, implications, question-form encoding complete",
            "priority": "high",
            "status_id": resolve_status("done"),
            "tags": ["feature"],
        },
        {
            "title": "Set up CI with pytest + coverage",
            "description": "GitHub Actions workflow for test suite on every PR",
            "priority": "medium",
            "status_id": resolve_status("done"),
            "tags": ["testing"],
        },
    ]

    created_ids = []
    for issue in issues_data:
        tags = issue.pop("tags", [])
        payload = {**issue, "project_id": pid}
        result = api("POST", "/api/issues", payload)
        iid = result["id"]
        created_ids.append(iid)
        # Attach tags
        for tname in tags:
            if tname in tag_map:
                try:
                    api("POST", f"/api/issues/{iid}/tags", {"tag_id": tag_map[tname]})
                except Exception:
                    pass
        print(f"    Issue #{result.get('issue_number', '?')}: {issue['title']}")

    # ── Agents ──
    print("  Registering agents...")
    agents = [
        {
            "name": "claude-code",
            "capabilities": ["python", "debugging", "refactoring", "testing", "architecture"],
            "description": "Claude Code — full-stack coding agent",
            "agent_type": "claude-code",
            "model": "claude-sonnet-4-5-20250929",
            "max_concurrent": 3,
        },
        {
            "name": "codex",
            "capabilities": ["python", "javascript", "code-generation", "shell"],
            "description": "OpenAI Codex — fast code generation agent",
            "agent_type": "codex",
            "model": "codex-mini",
            "max_concurrent": 5,
        },
        {
            "name": "gemini-agent",
            "capabilities": ["research", "documentation", "analysis", "summarization"],
            "description": "Gemini — research and documentation agent",
            "agent_type": "gemini",
            "model": "gemini-2.5-pro",
            "max_concurrent": 2,
        },
    ]

    for agent in agents:
        name = agent.pop("name")
        result = api("POST", f"/api/coordination/agents/{name}/register", agent)
        print(f"    Agent: {name}")

    # ── Summary ──
    print(f"\n  Seeded: 1 project, {len(created_ids)} issues, {len(agents)} agents")
    print(f"  Issue IDs: {created_ids}")


def main():
    parser = argparse.ArgumentParser(description="Seed engram-bridge with demo data")
    parser.add_argument("--no-start", action="store_true",
                        help="Skip starting engram-bridge (assume it's already running)")
    args = parser.parse_args()

    proc = None
    if not args.no_start:
        print("Starting engram-bridge --channel web ...")
        proc = subprocess.Popen(
            ["engram-bridge", "--channel", "web"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        print("Waiting for health check...")
        if not wait_for_health(timeout=30):
            print("ERROR: engram-bridge did not become healthy in 30s", file=sys.stderr)
            sys.exit(1)
        print("Bridge is healthy!\n")

        print("Seeding demo data...")
        seed()
        print("\nDone!")

    finally:
        if proc:
            print("\nStopping engram-bridge...")
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
