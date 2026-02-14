#!/usr/bin/env python3
"""Engram UserPromptSubmit hook — stdlib-only proactive memory injector.

Reads the user prompt, ensures a capability token, queries Engram search,
and emits a systemMessage for Claude Code context injection.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    sys.stdout.write("{}")
    sys.exit(0)

API_BASE = os.environ.get("ENGRAM_API_URL", "http://127.0.0.1:8100")
HEALTH_TIMEOUT = 3
SEARCH_TIMEOUT = 6
SESSION_TIMEOUT = 4
MAX_QUERY_CHARS = 120
SENTINEL = "[Engram \u2014 relevant memories from previous sessions]"

USER_ID = os.environ.get("ENGRAM_USER_ID", "default")
AGENT_ID = os.environ.get("ENGRAM_AGENT_ID", "claude-code")
TOKEN_CACHE = Path(os.environ.get("ENGRAM_TOKEN_CACHE", str(Path.home() / ".engram" / "session_token.json")))
ADMIN_KEY = os.environ.get("ENGRAM_ADMIN_KEY", "").strip()

# Phase 0: periodic background checkpoint
CHECKPOINT_THROTTLE_SECS = 60
CHECKPOINT_TIMEOUT = 3
CHECKPOINT_FILE = Path(os.environ.get(
    "ENGRAM_CHECKPOINT_THROTTLE",
    str(Path.home() / ".engram" / ".last_hook_checkpoint"),
))


def _should_checkpoint() -> bool:
    """Return True if enough time has elapsed since the last checkpoint."""
    try:
        if CHECKPOINT_FILE.exists():
            age = time.time() - CHECKPOINT_FILE.stat().st_mtime
            if age < CHECKPOINT_THROTTLE_SECS:
                return False
    except Exception:
        pass
    return True


def _touch_checkpoint_file() -> None:
    """Update the throttle file mtime."""
    try:
        CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_FILE.touch()
    except Exception:
        pass


def _background_checkpoint(prompt_text: str) -> None:
    """POST a lightweight checkpoint to the API (fire-and-forget)."""
    try:
        cwd = os.getcwd()
        payload = json.dumps({
            "task_summary": prompt_text[:200],
            "event_type": "hook_checkpoint",
            "agent_id": AGENT_ID,
            "context_snapshot": prompt_text[-500:],
            "repo_path": cwd,
        }).encode("utf-8")
        req = Request(
            f"{API_BASE}/v1/handoff/checkpoint",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=CHECKPOINT_TIMEOUT)
        _touch_checkpoint_file()
    except Exception:
        pass  # fire-and-forget — never block the hook


def _derive_query(raw: str) -> str:
    raw = raw.strip()
    for i, ch in enumerate(raw):
        if ch in ".!?" and i > 0:
            candidate = raw[: i + 1].strip()
            if candidate:
                return candidate[:MAX_QUERY_CHARS]
    return raw[:MAX_QUERY_CHARS]


def _health_check() -> bool:
    try:
        req = Request(f"{API_BASE}/health")
        resp = urlopen(req, timeout=HEALTH_TIMEOUT)
        return resp.status == 200
    except Exception:
        return False


def _parse_time(value: str):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _read_cached_token() -> str:
    if not TOKEN_CACHE.exists():
        return ""
    try:
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        token = data.get("token", "")
        expires_at = data.get("expires_at", "")
        if not token or not expires_at:
            return ""
        exp = _parse_time(expires_at)
        if exp is None:
            return ""
        if datetime.utcnow() + timedelta(minutes=2) >= exp:
            return ""
        return token
    except Exception:
        return ""


def _write_cached_token(token: str, expires_at: str) -> None:
    try:
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(
            json.dumps({"token": token, "expires_at": expires_at}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _create_session_token() -> str:
    payload = json.dumps(
        {
            "user_id": USER_ID,
            "agent_id": AGENT_ID,
            "allowed_confidentiality_scopes": ["work", "personal", "finance", "health", "private"],
            "capabilities": ["search", "propose_write"],
            "ttl_minutes": 24 * 60,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if ADMIN_KEY:
        headers["X-Engram-Admin-Key"] = ADMIN_KEY
    req = Request(
        f"{API_BASE}/v1/sessions",
        data=payload,
        headers=headers,
        method="POST",
    )
    resp = urlopen(req, timeout=SESSION_TIMEOUT)
    body = json.loads(resp.read().decode("utf-8"))
    token = body.get("token", "")
    expires_at = body.get("expires_at", "")
    if token and expires_at:
        _write_cached_token(token, expires_at)
    return token


def _get_token() -> str:
    env_token = os.environ.get("ENGRAM_API_TOKEN", "").strip()
    if env_token:
        return env_token

    cached = _read_cached_token()
    if cached:
        return cached

    try:
        return _create_session_token()
    except Exception:
        return ""


def _search(query: str, token: str) -> list:
    payload = json.dumps(
        {
            "query": query,
            "limit": 5,
            "user_id": USER_ID,
            "agent_id": AGENT_ID,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(
        f"{API_BASE}/v1/search",
        data=payload,
        headers=headers,
        method="POST",
    )
    resp = urlopen(req, timeout=SEARCH_TIMEOUT)
    body = json.loads(resp.read().decode("utf-8"))
    return body.get("results", [])


def _format_memories(results: list) -> str:
    lines = [SENTINEL]
    for idx, mem in enumerate(results, 1):
        layer = mem.get("layer", "sml")
        score = mem.get("composite_score", mem.get("score", 0.0))
        content = mem.get("memory", mem.get("content", mem.get("details", ""))).strip()
        lines.append(f"{idx}. [{layer}, relevance {score:.2f}] {content}")
    return "\n".join(lines)


def main() -> None:
    raw_prompt = os.environ.get("USER_PROMPT", "")
    if not raw_prompt:
        try:
            raw_prompt = sys.stdin.read()
        except Exception:
            raw_prompt = ""

    if not raw_prompt.strip():
        sys.stdout.write("{}")
        return

    # Phase 0: fire-and-forget background checkpoint (non-blocking)
    if _should_checkpoint():
        t = threading.Thread(
            target=_background_checkpoint, args=(raw_prompt,), daemon=True
        )
        t.start()

    if not _health_check():
        sys.stdout.write("{}")
        return

    token = _get_token()
    if not token:
        sys.stdout.write("{}")
        return

    query = _derive_query(raw_prompt)
    results = _search(query, token)

    if not results:
        sys.stdout.write("{}")
        return

    output = {"systemMessage": _format_memories(results)}
    sys.stdout.write(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stdout.write("{}")
