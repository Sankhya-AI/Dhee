"""OpenClaw skill deployer for Engram persistent memory.

Writes a SKILL.md into ~/.openclaw/workspace/skills/engram-memory/ so that
an OpenClaw agent automatically uses the Engram REST API for memory
operations.  Call ``deploy()`` from the CLI installer or standalone.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Skill template
# ---------------------------------------------------------------------------
# JSON braces are doubled so Python str.format() leaves them intact.
# The single placeholder {engram_api_url} is filled at deploy time.

_SKILL_TEMPLATE = """\
---
name: engram-memory
description: Persistent memory layer powered by Engram
version: "1.0"
provider: engram
metadata: {{"version": "1.0", "provider": "engram"}}
---

# Engram Memory – Standing Instructions

You have access to a persistent memory store backed by the Engram API at
**{engram_api_url}**.  Follow the rules below on every turn.

---

## Quick-reference

| Action | Method | Endpoint | Key fields |
|--------|--------|----------|------------|
| Session | POST | /v1/sessions | `user_id`, `agent_id`, `allowed_confidentiality_scopes` |
| Search | POST | /v1/search | `query`, `user_id`, `agent_id`, `limit` |
| Store  | POST | /v1/memories | `content`, `mode`, `scope`, `source_app` |
| Health | GET  | /health | — |

---

## Rules

### 1 – Create session token, then search each user message
Before the first memory call in a session, create a capability token via
`{engram_api_url}/v1/sessions` and include it in all subsequent requests:
`Authorization: Bearer <token>`.

Session request body:
```json
{{
  "user_id": "default",
  "agent_id": "openclaw",
  "allowed_confidentiality_scopes": ["work", "personal", "finance", "health", "private"],
  "capabilities": ["search", "propose_write"],
  "ttl_minutes": 1440
}}
```

Then before composing a reply, silently POST to
`{engram_api_url}/v1/search` with a `query` derived from the user's
message.  Inject any returned snippets into your reasoning context.
Do **not** surface the raw search payload to the user.

Example request body:
```json
{{
  "query": "<short summary of user intent>",
  "user_id": "default",
  "agent_id": "openclaw",
  "limit": 5
}}
```

### 2 – Store when you learn something worth remembering
POST to `{engram_api_url}/v1/memories` when you observe any of:
* an explicit user preference or correction
* a recurring pattern across conversations
* a fact the user expects you to retain

Always tag the memory so it is filterable:

```json
{{
  "content": "<what was learned>",
  "mode": "staging",
  "scope": "work",
  "user_id": "default",
  "agent_id": "openclaw",
  "source_app": "openclaw",
  "infer": false,
  "categories": ["<relevant-category>"]
}}
```

Set `infer: false` because you have already distilled the fact yourself.

### 3 – Health-check on failure
If any API call returns a network error or a non-2xx status:
1. GET `{engram_api_url}/health`
2. If that also fails, let the user know:
   *"Engram memory is unreachable. Please start it with `engram-api`."*
3. Continue the conversation without memory for this turn.

### 4 – Stay silent about the plumbing
Never mention the Engram API, skill files, or internal URLs to the user
unless they explicitly ask how memory works.
"""

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_SKILLS_ROOT = Path.home() / ".openclaw" / "workspace" / "skills"
_SKILL_DIR_NAME = "engram-memory"


def deploy(engram_api_url: str | None = None, skills_root: Path | None = None) -> bool:
    """Write the Engram skill into the OpenClaw skills directory.

    Parameters
    ----------
    engram_api_url : str | None
        Base URL of the running Engram API.  Defaults to the
        ``ENGRAM_API_URL`` environment variable, then ``http://127.0.0.1:8100``.
    skills_root : Path | None
        Root skills directory.  Defaults to ``~/.openclaw/workspace/skills/``.
        Passing a custom value is useful for testing.

    Returns
    -------
    bool
        ``True`` if the skill was written (or was already up-to-date).
        ``False`` if the skills root does not exist and was not created by
        OpenClaw (i.e. OpenClaw is not installed).
    """
    if skills_root is None:
        skills_root = _DEFAULT_SKILLS_ROOT
        # If the default root doesn't exist, OpenClaw isn't installed — bail
        # silently so non-OpenClaw users see no noise.
        if not skills_root.exists():
            return False
    else:
        # Caller supplied an explicit root (e.g. a temp dir for testing);
        # create it if needed.
        skills_root.mkdir(parents=True, exist_ok=True)

    if engram_api_url is None:
        engram_api_url = os.environ.get("ENGRAM_API_URL", "http://127.0.0.1:8100")

    skill_dir = skills_root / _SKILL_DIR_NAME
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    rendered = _SKILL_TEMPLATE.format(engram_api_url=engram_api_url)

    # Backup existing file before overwriting (mirrors cli._update_config)
    if skill_path.exists():
        backup_path = skill_path.with_suffix(".md.bak")
        try:
            shutil.copy2(skill_path, backup_path)
        except Exception as e:  # pragma: no cover
            print(f"  ⚠️  Could not create backup: {e}")

    try:
        skill_path.write_text(rendered, encoding="utf-8")
    except Exception as e:  # pragma: no cover
        print(f"  ❌ Error writing OpenClaw skill: {e}")
        return False

    print(f"  ✓ [engram-memory] Deployed to {skill_path}")
    return True
