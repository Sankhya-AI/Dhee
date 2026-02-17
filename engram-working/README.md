# engram-working — The Blackboard

Working memory for AI agents. Volatile short-term buffer with capacity limits (Miller's Law) and activation decay.

## Features

- **Capacity-limited buffer** — default 7 items (Miller's Law)
- **Activation decay** — items decay over minutes, not hours
- **Automatic eviction** — least-active items pushed to long-term memory
- **Refresh on access** — peeking at items boosts their activation
- **Volatile by design** — primary store is in-process, not database

## Installation

```bash
pip install engram-working
```

## Quick Start

```python
from engram.memory.main import Memory
from engram_working import WorkingMemory

memory = Memory(config=...)
wm = WorkingMemory(memory, user_id="default")

# Push items
wm.push("Current task: fix login bug", tag="task")
wm.push("API endpoint: /auth/login", tag="context")

# List what's in working memory
items = wm.list()

# Flush everything to long-term when done
wm.flush_to_longterm()
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `wm_push` | Push item into working memory |
| `wm_peek` | Look at an item (refreshes activation) |
| `wm_list` | List all items by activation |
| `wm_pop` | Remove an item |
| `wm_flush_to_longterm` | Flush all to long-term memory |
