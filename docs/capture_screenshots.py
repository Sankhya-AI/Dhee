"""Capture dashboard screenshots using Playwright headless browser.

Starts engram-bridge, seeds sample data, captures each view, then shuts down.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Seed data for realistic screenshots ──

SAMPLE_PROJECTS = [
    {"name": "Engram Core", "color": "#6366f1", "description": "Core memory engine"},
]

SAMPLE_ISSUES = [
    {
        "title": "Fix Python import error in auth module",
        "description": "ImportError when running auth.py with new decorator pattern",
        "priority": "high",
        "tags": ["python", "auth", "bug"],
    },
    {
        "title": "Add retry logic to embedding API calls",
        "description": "Embedding calls fail silently on timeout — add exponential backoff",
        "priority": "medium",
        "tags": ["embeddings", "reliability"],
    },
    {
        "title": "Scaffold React dashboard for memory inspector",
        "description": "New page in web-ui to browse and search memory items visually",
        "priority": "normal",
        "tags": ["frontend", "react", "dashboard"],
    },
    {
        "title": "Optimize vector search for large collections",
        "description": "Search slows down past 10k memories — profile and fix",
        "priority": "high",
        "tags": ["performance", "vector-search"],
    },
    {
        "title": "Write tests for CLS distillation sleep cycle",
        "description": "Coverage for episodic-to-semantic fact extraction pipeline",
        "priority": "low",
        "tags": ["testing", "cls"],
    },
]

BASE = "http://127.0.0.1:8200"


async def seed_data():
    """Seed the bridge with sample projects and tasks."""
    import httpx

    async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
        # Create project
        for proj in SAMPLE_PROJECTS:
            try:
                await c.post("/api/projects", json=proj)
            except Exception:
                pass

        # Get project list to find project_id
        try:
            resp = await c.get("/api/projects")
            projects = resp.json()
            project_id = projects[0]["id"] if projects else "default"
        except Exception:
            project_id = "default"

        # Create issues
        for issue in SAMPLE_ISSUES:
            try:
                await c.post("/api/issues", json={**issue, "project_id": project_id})
            except Exception:
                pass


async def capture_screenshots():
    """Capture screenshots of each dashboard view."""
    from playwright.async_api import async_playwright

    screenshots_dir = Path(__file__).parent / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
        )
        page = await ctx.new_page()

        # Wait for the app to be ready
        await page.goto(f"{BASE}/", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 1. Board view (Kanban)
        await page.goto(f"{BASE}/board", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        await page.screenshot(
            path=str(screenshots_dir / "board-kanban.png"),
            full_page=False,
        )
        print("[ok] board-kanban.png")

        # 2. Todos view
        await page.goto(f"{BASE}/todos", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        await page.screenshot(
            path=str(screenshots_dir / "todos-view.png"),
            full_page=False,
        )
        print("[ok] todos-view.png")

        # 3. Memory view
        await page.goto(f"{BASE}/memory", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        await page.screenshot(
            path=str(screenshots_dir / "memory-view.png"),
            full_page=False,
        )
        print("[ok] memory-view.png")

        # 4. Coordination / Agents view
        await page.goto(f"{BASE}/coordination", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        await page.screenshot(
            path=str(screenshots_dir / "coordination-agents.png"),
            full_page=False,
        )
        print("[ok] coordination-agents.png")

        # 5. Chat view
        await page.goto(f"{BASE}/", wait_until="networkidle")
        await page.wait_for_timeout(1000)
        await page.screenshot(
            path=str(screenshots_dir / "chat-view.png"),
            full_page=False,
        )
        print("[ok] chat-view.png")

        await browser.close()


async def main():
    # Seed data first
    print("Seeding sample data...")
    try:
        await seed_data()
        print("Sample data seeded.")
    except Exception as e:
        print(f"Seed warning (non-fatal): {e}")

    # Capture screenshots
    print("Capturing screenshots...")
    await capture_screenshots()
    print("Done! Screenshots saved to docs/screenshots/")


if __name__ == "__main__":
    asyncio.run(main())
