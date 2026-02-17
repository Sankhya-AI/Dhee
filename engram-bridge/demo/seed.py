#!/usr/bin/env python3
"""Demo seed script — sets up a project, statuses, and compelling todos.

Usage:
    python3 demo/seed.py              # seed only
    python3 demo/seed.py --enable     # also enable auto_execute in bridge.json
    python3 demo/seed.py --reset      # clear and re-seed

Requires the bridge to be running at http://127.0.0.1:8200.
"""

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8200"
CONFIG_PATH = Path("~/.engram/bridge.json").expanduser()

# Colors for terminal output
G = "\033[32m"  # green
B = "\033[34m"  # blue
Y = "\033[33m"  # yellow
R = "\033[0m"   # reset
BOLD = "\033[1m"


def api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"\n  {Y}Error: Could not connect to bridge at {BASE}{R}")
        print(f"  Start it first: engram-bridge --channel web\n")
        sys.exit(1)


def enable_auto_execute():
    """Enable auto_execute in ~/.engram/bridge.json."""
    if not CONFIG_PATH.exists():
        print(f"  {Y}Config not found at {CONFIG_PATH}{R}")
        return False

    config = json.loads(CONFIG_PATH.read_text())
    coord = config.setdefault("coordination", {})

    if coord.get("auto_execute"):
        print(f"  {G}auto_execute already enabled{R}")
        return True

    coord["auto_execute"] = True
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  {G}Enabled auto_execute in {CONFIG_PATH}{R}")
    print(f"  {Y}Restart the bridge for this to take effect{R}")
    return True


def seed():
    """Create a demo project with todos designed for the demo video."""
    print(f"\n{BOLD}Seeding demo data...{R}\n")

    # 1. Create project
    print(f"  {B}Creating project...{R}")
    project = api("POST", "/api/projects", {
        "name": "Demo App",
        "color": "#6366f1",
        "description": "User service API — demo project for auto-execute showcase",
    })
    pid = project["id"]
    print(f"  {G}Project created: {pid}{R}")

    # 2. Get statuses (default ones were auto-created)
    statuses = api("GET", f"/api/projects/{pid}/statuses")
    inbox_id = None
    for s in statuses:
        if s.get("name", "").lower() in ("inbox", "backlog", "todo"):
            inbox_id = s["id"]
            break
    if not inbox_id and statuses:
        inbox_id = statuses[0]["id"]
    print(f"  {G}Statuses ready ({len(statuses)} found){R}")

    # 3. Create tags
    print(f"  {B}Creating tags...{R}")
    tag_security = api("POST", f"/api/projects/{pid}/tags", {"name": "security", "color": "#ef4444"})
    tag_validation = api("POST", f"/api/projects/{pid}/tags", {"name": "validation", "color": "#f59e0b"})
    tag_testing = api("POST", f"/api/projects/{pid}/tags", {"name": "testing", "color": "#22c55e"})
    print(f"  {G}Tags created{R}")

    # 4. Create demo issues
    demo_app_path = str(Path(__file__).parent / "demo-app")

    # The star of the demo — this is the one to push during recording
    issues = [
        {
            "title": "Add input validation to user registration",
            "description": (
                f"The create_user endpoint in {demo_app_path}/main.py has no validation.\n\n"
                "Add validation for:\n"
                "- Username: 3-20 chars, alphanumeric + underscore only\n"
                "- Email: valid email format\n"
                "- Password: minimum 8 chars, at least one uppercase and one digit\n"
                "- Age: must be between 13 and 150\n\n"
                "Use Pydantic field validators. Return clear error messages."
            ),
            "priority": "high",
            "tags": [tag_validation["id"], tag_security["id"]],
        },
        {
            "title": "Fix password leak in GET /users endpoint",
            "description": (
                f"The get_user endpoint in {demo_app_path}/main.py returns the password in the response.\n\n"
                "Create a separate response model that excludes the password field."
            ),
            "priority": "urgent",
            "tags": [tag_security["id"]],
        },
        {
            "title": "Add validation tests for user registration",
            "description": (
                f"Add tests to {demo_app_path}/test_main.py that verify:\n"
                "- Short username is rejected\n"
                "- Invalid email is rejected\n"
                "- Weak password is rejected\n"
                "- Underage user is rejected\n"
                "- Valid user is accepted"
            ),
            "priority": "medium",
            "tags": [tag_testing["id"], tag_validation["id"]],
        },
    ]

    print(f"  {B}Creating issues...{R}")
    created = []
    for issue_data in issues:
        tag_ids = issue_data.pop("tags", [])
        issue_data["project_id"] = pid
        if inbox_id:
            issue_data["status_id"] = inbox_id
        issue = api("POST", "/api/issues", issue_data)
        iid = issue.get("id", "")
        # Add tags
        for tid in tag_ids:
            api("POST", f"/api/issues/{iid}/tags", {"tag_id": tid})
        created.append(issue)
        print(f"    {G}#{issue.get('issue_number', '?')} {issue.get('title', '')[:50]}...{R}")

    print(f"\n{BOLD}{G}Demo seeded successfully!{R}\n")
    print(f"  Project: {BOLD}Demo App{R} ({pid})")
    print(f"  Issues:  {len(created)} created in inbox")
    print(f"  Demo app: {demo_app_path}")
    print()

    return project, created


def check_health():
    """Verify the bridge is running."""
    try:
        health = api("GET", "/health")
        return health.get("status") == "ok"
    except SystemExit:
        return False


def main():
    parser = argparse.ArgumentParser(description="Seed demo data for auto-execute showcase")
    parser.add_argument("--enable", action="store_true", help="Enable auto_execute in bridge.json")
    parser.add_argument("--reset", action="store_true", help="Clear existing data and re-seed")
    args = parser.parse_args()

    print(f"\n{BOLD}engram-bridge Demo Setup{R}")
    print("=" * 40)

    # Step 1: Optionally enable auto_execute
    if args.enable:
        print(f"\n{B}[1/3] Enabling auto_execute...{R}")
        enable_auto_execute()
    else:
        print(f"\n{B}[1/3] Config check...{R}")
        if CONFIG_PATH.exists():
            config = json.loads(CONFIG_PATH.read_text())
            ae = config.get("coordination", {}).get("auto_execute", False)
            if ae:
                print(f"  {G}auto_execute is enabled{R}")
            else:
                print(f"  {Y}auto_execute is OFF — run with --enable to turn it on{R}")

    # Step 2: Check bridge is running
    print(f"\n{B}[2/3] Checking bridge...{R}")
    if not check_health():
        return

    info = api("GET", "/api/info")
    print(f"  {G}Bridge is running (v{info.get('version', '?')}){R}")
    print(f"  Memory: {'yes' if info.get('has_memory') else 'no'}")
    print(f"  Projects: {'yes' if info.get('has_projects') else 'no'}")

    # Step 3: Seed data
    print(f"\n{B}[3/3] Seeding demo data...{R}")
    seed()

    # Print demo instructions
    print(f"{BOLD}Demo instructions:{R}")
    print(f"  1. Open {B}http://127.0.0.1:8200{R}")
    print(f"  2. Go to the {BOLD}Board{R} view — you'll see 3 issues in inbox")
    print(f"  3. The star issue: {Y}\"Add input validation to user registration\"{R}")
    print(f"  4. It will auto-route to claude-code and start executing")
    print(f"  5. Watch the toast + click View to see live agent conversation")
    print(f"  6. Send a follow-up: {Y}\"also hash the password before storing it\"{R}")
    print()


if __name__ == "__main__":
    main()
