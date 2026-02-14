# engram-memory — Claude Code Plugin

Gives Claude Code **proactive persistent memory** and **session continuity** powered by
[Engram](https://github.com/Ashish-dwi99/Engram).

## What it does

* **UserPromptSubmit hook** — before Claude sees your message, a lightweight
  script queries Engram and injects relevant memories into the system context.
  Zero latency impact on your typing; the hook runs in the background.
* **Handoff checkpoints** — the hook fires a background checkpoint on every
  prompt (throttled to 1/min). Even if the LLM hits a rate limit, there's a
  recent checkpoint the next agent can pick up from.
* **Session continuity** — `get_last_session` / `save_session_digest` MCP tools
  let agents resume from where the previous session left off.
* **Log fallback** — if no stored session exists, Engram parses Claude Code's
  `.jsonl` conversation logs to reconstruct context automatically.
* **Slash commands** — `/engram:remember`, `/engram:search`, `/engram:forget`,
  `/engram:status` for on-demand memory operations.
* **Skill (standing instructions)** — tells Claude *when* and *how* to use the
  memory tools automatically.

## Installation

```bash
engram install                           # deploys plugin to ~/.engram/claude-plugin/
```

## Requirements

* Python 3.9+ (hook script uses only the standard library)
* A running Engram API (`engram-api`) — defaults to `http://127.0.0.1:8100`
* Set `ENGRAM_API_URL` if your API lives elsewhere
