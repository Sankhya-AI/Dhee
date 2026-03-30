# engram-bus

Lightweight real-time agent-to-agent communication bus for [Engram](../README.md). Zero external dependencies — stdlib only.

## Install

```bash
pip install engram-bus
```

## What it does

- **Key/value store** — TTL-based ephemeral state with namespaces and agent ownership
- **Pub/sub** — real-time topic-based messaging between agents
- **Agent registry** — auto-tracks agents on first interaction
- **Handoff sessions** — SQLite-backed durable session state (task summary, decisions, files touched, TODOs)
- **Handoff checkpoints** — periodic state snapshots within a session (survives rate limits)
- **Handoff lanes** — directed agent-to-agent coordination channels

## Quick Start

```python
from engram_bus import Bus

bus = Bus()

# Key/value with TTL
bus.put("status", "refactoring auth", agent="planner", ttl=300)
bus.get("status")  # "refactoring auth"

# Pub/sub (callback receives topic, data, agent_id)
bus.subscribe("progress", lambda topic, data, agent_id: print(data))
bus.publish("progress", {"step": 3, "total": 5}, agent="worker")

# Handoff sessions (persisted to SQLite)
bus = Bus(db_path="~/.engram/handoff.db")
sid = bus.save_session("claude-code", task_summary="Migrate to v2 API", repo="/my/project")
bus.checkpoint(sid, "claude-code", {"files": ["api.py"], "progress": "50%"})
session = bus.get_session(agent_id="claude-code")
```

## TCP Server

```bash
engram-bus                                # starts on port 9470
```

```python
# Connect from another process
bus = Bus(connect="127.0.0.1:9470")
bus.put("key", "value")
```

Wire protocol: newline-delimited JSON (`{"op": "put", "key": "...", "value": ...}`).

## Architecture

```
engram_bus/
├── bus.py          # Main Bus class — hybrid local/remote with lazy SQLite handoff
├── store.py        # HandoffStore — SQLite CRUD for sessions, checkpoints, lanes
├── pubsub.py       # In-process pub/sub with topic subscriptions
├── server.py       # TCP server (newline-delimited JSON)
└── workspace.py    # Workspace identity and path management
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
