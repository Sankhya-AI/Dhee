"""Claude Code plugin deployer for Engram persistent memory.

Writes the full plugin tree into ~/.engram/claude-plugin/engram-memory/ so that
Claude Code's UserPromptSubmit hook auto-injects relevant memories on every
user message, and /engram slash commands + a standing-instruction skill are
available in-session.  Call ``deploy()`` from the CLI installer or standalone.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

# ---------------------------------------------------------------------------
# Plugin-tree file contents (all static — no dynamic generation needed)
# ---------------------------------------------------------------------------

_PLUGIN_JSON = """\
{
  "name": "engram-memory",
  "version": "1.0.0",
  "description": "Proactive persistent memory for Claude Code powered by Engram",
  "author": "Engram",
  "provider": "engram"
}
"""

_HOOKS_JSON = """\
{
  "description": "Engram proactive memory — injects context on every user prompt",
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/prompt_context.py",
        "timeout": 8
      }]
    }]
  }
}
"""

_PROMPT_CONTEXT_PY = '''\
#!/usr/bin/env python3
"""Engram UserPromptSubmit hook — stdlib-only proactive memory injector.

Reads the user prompt from STDIN (or falls back to the USER_PROMPT env var),
queries the running Engram API for relevant memories, and prints a JSON
object with a ``systemMessage`` key that Claude Code will inject into context.

Design constraints
------------------
* stdlib only — runs as a bare subprocess, no pip install
* Phase 1: GET /health with 3 s timeout  – fast-fail if API is down
* Phase 2: POST /v1/search with 6 s timeout
* Query derivation is pure string ops (no LLM call)
* Always exits 0; any failure prints ``{}``
"""

import json
import os
import sys

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:  # pragma: no cover – safety net
    sys.stdout.write("{}")
    sys.exit(0)

# ---------------------------------------------------------------------------
# Configuration (all env-overridable)
# ---------------------------------------------------------------------------
API_BASE = os.environ.get("ENGRAM_API_URL", "http://127.0.0.1:8100")
HEALTH_TIMEOUT = 3   # seconds
SEARCH_TIMEOUT = 6   # seconds
MAX_QUERY_CHARS = 120
SENTINEL = "[Engram \\u2014 relevant memories from previous sessions]"


def _derive_query(raw: str) -> str:
    """Extract a short query from the raw user prompt (no LLM).

    Takes the first sentence (split on .  !  ?) or the first MAX_QUERY_CHARS
    characters, whichever is shorter.
    """
    raw = raw.strip()
    # Find the end of the first sentence
    for i, ch in enumerate(raw):
        if ch in ".!?" and i > 0:
            candidate = raw[: i + 1].strip()
            if candidate:
                return candidate[:MAX_QUERY_CHARS]
    # No sentence-ending punctuation found — just truncate
    return raw[:MAX_QUERY_CHARS]


def _health_check() -> bool:
    """GET /health — returns True if the API is reachable and healthy."""
    try:
        req = Request(f"{API_BASE}/health")
        resp = urlopen(req, timeout=HEALTH_TIMEOUT)
        return resp.status == 200
    except Exception:
        return False


def _search(query: str) -> list:
    """POST /v1/search — returns the raw results list (may be empty)."""
    payload = json.dumps({"query": query, "limit": 5}).encode("utf-8")
    req = Request(
        f"{API_BASE}/v1/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urlopen(req, timeout=SEARCH_TIMEOUT)
    body = json.loads(resp.read().decode("utf-8"))
    return body.get("results", [])


def _format_memories(results: list) -> str:
    """Turn search results into the injected system-message block."""
    lines = [SENTINEL]
    for idx, mem in enumerate(results, 1):
        layer = mem.get("layer", "sml")
        score = mem.get("composite_score", mem.get("score", 0.0))
        content = mem.get("memory", mem.get("content", "")).strip()
        lines.append(f"{idx}. [{layer}, relevance {score:.2f}] {content}")
    return "\\n".join(lines)


def main() -> None:
    """Entry point — orchestrates health-check → search → output."""
    # Read the user prompt.  Claude Code may pass it via USER_PROMPT env var
    # or via STDIN depending on hook invocation mode.
    raw_prompt = os.environ.get("USER_PROMPT", "")
    if not raw_prompt:
        try:
            raw_prompt = sys.stdin.read()
        except Exception:
            raw_prompt = ""

    if not raw_prompt.strip():
        sys.stdout.write("{}")
        return

    # Phase 1 – health check (fast-fail)
    if not _health_check():
        sys.stdout.write("{}")
        return

    # Phase 2 – search
    query = _derive_query(raw_prompt)
    results = _search(query)

    if not results:
        sys.stdout.write("{}")
        return

    # Emit the hook response
    output = {"systemMessage": _format_memories(results)}
    sys.stdout.write(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Outermost safety net — never crash, never block the user
        sys.stdout.write("{}")
'''

# Prefer the tracked hook file so runtime deployments stay in sync with
# `plugins/engram-memory/hooks/prompt_context.py` updates.
_TRACKED_HOOK = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "engram-memory"
    / "hooks"
    / "prompt_context.py"
)
if _TRACKED_HOOK.exists():
    _PROMPT_CONTEXT_PY = _TRACKED_HOOK.read_text(encoding="utf-8")

_CMD_ENGRAM_MD = """\
---
name: engram
description: Engram memory — help and status overview
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# /engram — Engram Memory Commands

Engram gives Claude Code proactive persistent memory.  Context is injected
automatically on every message; the commands below let you manage memory
on demand.

| Command | What it does |
|---|---|
| `/engram:remember <text>` | Save a fact or preference right now |
| `/engram:search <query>` | Search memories by topic |
| `/engram:forget <id or query>` | Delete a memory (by ID or by searching first) |
| `/engram:status` | Show memory-store health and counts |

---

If `$ARGUMENTS` equals **status**, run `/engram:status` instead.
"""

_CMD_REMEMBER_MD = """\
---
name: remember
description: Save a fact or preference to Engram memory
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# /engram:remember

Saves the provided text directly to Engram's long-term memory store.

**Usage:** `/engram:remember <text to remember>`

Call the `remember` MCP tool with the following arguments:

```json
{
  "content": "$ARGUMENTS"
}
```

After the tool returns, confirm to the user that the memory was saved.
"""

_CMD_SEARCH_MD = """\
---
name: search
description: Search Engram memory by topic or keyword
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# /engram:search

Searches Engram for memories matching the given query and returns them
as a numbered list.

**Usage:** `/engram:search <query>`

Call the `search_memory` MCP tool with the following arguments:

```json
{
  "query": "$ARGUMENTS",
  "limit": 10
}
```

Format the results as a numbered list:
`1. [<layer>, relevance <score>] <memory content>`

If no results are returned, let the user know nothing matched.
"""

_CMD_FORGET_MD = """\
---
name: forget
description: Delete a memory from Engram by ID or by searching first
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# /engram:forget

Deletes a memory from Engram.

**Usage:** `/engram:forget <memory-id or search query>`

**Logic:**
1. If `$ARGUMENTS` looks like a UUID (contains hyphens and is 36 chars),
   call `delete_memory` directly with that ID.
2. Otherwise, call `search_memory` with `$ARGUMENTS` as the query.
   Present the results to the user and ask which one to delete.
   Once confirmed, call `delete_memory` with the chosen ID.

Always confirm the deletion with the user before proceeding in case (2).
"""

_CMD_STATUS_MD = """\
---
name: status
description: Show Engram memory-store health and statistics
allowed-tools:
  - Bash
  - Read
  - Glob
  - Grep
---

# /engram:status

Shows a summary of your Engram memory store.

Call the `get_memory_stats` MCP tool with no arguments, then render the
result as a simple markdown table:

| Metric | Value |
|---|---|
| Total memories | … |
| Short-term (SML) | … |
| Long-term (LML) | … |
| … | … |

If the tool returns an error, display it clearly.
"""

_SKILL_MD = """\
---
name: engram-memory
description: |
  Engram persistent-memory skill. Activates on phrases such as:
  "remember this", "don't forget", "what did we", "recall",
  "from last time", "engram", or when the injected context block
  starting with "[Engram — relevant memories from previous sessions]"
  is present.
version: "1.0"
provider: engram
---

# Engram Memory — Standing Instructions

You have access to a persistent memory store via Engram MCP tools.
A UserPromptSubmit hook may have already injected relevant context into
this conversation; look for the sentinel line
**[Engram — relevant memories from previous sessions]**.

Follow the five rules below on every turn.

---

## Rule 1 — Consume injected context silently

If the sentinel block is present, read and use it to inform your reply.
Do **not** paste or quote the raw injected block to the user.  Weave the
information naturally into your response.

## Rule 2 — Always do handoff bootstrap on new task threads

At the beginning of a new repo/task thread, call `get_last_session` before
deep implementation guidance:
* `user_id`: `"default"` unless user provides another
* `requester_agent_id`: `"claude-code"`
* `repo`: absolute workspace path when available
* Include `agent_id` only if the user explicitly asks to resume from a specific source agent.

If a handoff session exists, continue from it naturally without exposing raw
tool payloads.

## Rule 3 — Proactively save when the user signals intent

Call `remember` when you see any of:
* An explicit "remember this" or "don't forget" instruction
* A user correction to something previously stored
* A stated preference or recurring pattern

Do **not** spam the store — one clean save per signal is enough.

## Rule 4 — Search on explicit recall requests

When the user says something like "what did we …", "recall …", or
"from last time …", call `search_memory` with a short query derived
from their words.  Present results naturally; do not show raw JSON.

## Rule 5 — Save handoff digest before pausing/ending

When pausing, hitting tool limits, or ending a substantial work chunk, call
`save_session_digest` with:
* `task_summary`
* `repo`
* `status` (`paused`/`active`/`completed`)
* `decisions_made`, `files_touched`, `todos_remaining`
* `blockers`, `key_commands`, `test_results` when available
* `agent_id` and `requester_agent_id` as `"claude-code"`

## Rule 6 — Stay quiet about the plumbing

Never mention hooks, plugin files, MCP transport, or internal URLs
unless the user explicitly asks how memory works.

## Rule 7 — Tool selection guide

| User intent | Tool to call | Key params |
|---|---|---|
| Resume prior repo task | `get_last_session` | `user_id`, `repo` (`agent_id` only when explicitly requested) |
| Save cross-agent handoff | `save_session_digest` | `task_summary`, `status`, `files_touched`, `todos_remaining` |
| Quick save (no categories) | `remember` | `content` |
| Save with categories / scope | `add_memory` | `content`, `categories`, `scope` |
| Find something from before | `search_memory` | `query`, `limit` |
| Browse all memories | `get_all_memories` | `user_id`, `limit` |
| Load session context | `engram_context` | `limit` (default 15) |
| Fix something already stored | `update_memory` | `memory_id`, `content` |
| User wants to forget something | `delete_memory` | `memory_id` |
| Check memory health | `get_memory_stats` | — |
| Explicit maintenance only | `apply_memory_decay` | — |
"""

_README_MD = """\
# engram-memory — Claude Code Plugin

Gives Claude Code **proactive persistent memory** powered by
[Engram](https://github.com/Ashish-dwi99/engram).

## What it does

* **UserPromptSubmit hook** — before Claude sees your message, a lightweight
  script queries Engram and injects relevant memories into the system context.
  Zero latency impact on your typing; the hook runs in the background with an
  8-second ceiling.
* **Slash commands** — `/engram:remember`, `/engram:search`, `/engram:forget`,
  `/engram:status` for on-demand memory operations.
* **Skill (standing instructions)** — tells Claude *when* and *how* to use the
  memory tools automatically.

## Installation

Run `engram install` (requires the Engram package).  The plugin is deployed to
`~/.engram/claude-plugin/engram-memory/`.  Activate it in Claude Code:

```
/plugin install engram-memory --path ~/.engram/claude-plugin
```

## Requirements

* Python 3.8+ (hook script uses only the standard library)
* A running Engram API (`engram-api`) — defaults to `http://127.0.0.1:8100`
* Set `ENGRAM_API_URL` if your API lives elsewhere
"""

# ---------------------------------------------------------------------------
# Deployment map  (relative path → file content)
# ---------------------------------------------------------------------------

_PLUGIN_FILES: dict[str, str] = {
    ".claude-plugin/plugin.json": _PLUGIN_JSON,
    "hooks/hooks.json": _HOOKS_JSON,
    "hooks/prompt_context.py": _PROMPT_CONTEXT_PY,
    "commands/engram.md": _CMD_ENGRAM_MD,
    "commands/remember.md": _CMD_REMEMBER_MD,
    "commands/search.md": _CMD_SEARCH_MD,
    "commands/forget.md": _CMD_FORGET_MD,
    "commands/status.md": _CMD_STATUS_MD,
    "skills/engram-memory/SKILL.md": _SKILL_MD,
    "README.md": _README_MD,
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_PLUGINS_ROOT = Path.home() / ".engram" / "claude-plugin"
_PLUGIN_DIR_NAME = "engram-memory"


def deploy(plugins_root: Path | None = None) -> bool:
    """Write the full Claude Code plugin tree to disk.

    Parameters
    ----------
    plugins_root : Path | None
        Parent directory that will contain the ``engram-memory/`` folder.
        Defaults to ``~/.engram/claude-plugin/``.  Pass a custom path (e.g. a
        temp directory) for testing.

    Returns
    -------
    bool
        ``True`` if all files were written successfully, ``False`` otherwise.
    """
    if plugins_root is None:
        plugins_root = _DEFAULT_PLUGINS_ROOT

    # Always create the root — this is an Engram-owned directory, not a
    # third-party app directory, so we don't need an existence check.
    plugins_root.mkdir(parents=True, exist_ok=True)

    plugin_dir = plugins_root / _PLUGIN_DIR_NAME

    for rel_path, content in _PLUGIN_FILES.items():
        target = plugin_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)

        # Backup before overwrite (mirrors openclaw.py pattern)
        if target.exists():
            backup = target.with_name(target.name + ".bak")
            try:
                shutil.copy2(target, backup)
            except Exception as e:  # pragma: no cover
                print(f"  ⚠️  Could not back up {target}: {e}")

        try:
            target.write_text(content, encoding="utf-8")
        except Exception as e:  # pragma: no cover
            print(f"  ❌ Error writing {target}: {e}")
            return False

    # Make the hook script executable
    hook_script = plugin_dir / "hooks" / "prompt_context.py"
    try:
        hook_script.chmod(hook_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:  # pragma: no cover
        print(f"  ⚠️  Could not chmod {hook_script}: {e}")

    print(f"  ✓ [engram-memory] Claude Code plugin deployed to {plugin_dir}")
    print(f"  ℹ️  Activate with: /plugin install engram-memory --path {plugins_root}")
    return True
