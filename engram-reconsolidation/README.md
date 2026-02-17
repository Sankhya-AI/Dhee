# engram-reconsolidation — The Updater

Memory reconsolidation for AI agents. When memories are retrieved, new context can propose updates — preventing stale or incorrect memories from poisoning the system.

## Features

- **Propose updates** via LLM evaluation of new context against existing memories
- **Apply or reject** proposals with conflict checking
- **Version history** tracked automatically via memory_history table
- **Auto-apply** high-confidence proposals
- **Cooldown periods** prevent thrashing on the same memory

## Installation

```bash
pip install engram-reconsolidation
```

## Quick Start

```python
from engram.memory.main import Memory
from engram_reconsolidation import Reconsolidation

memory = Memory(config=...)
rc = Reconsolidation(memory, user_id="default")

# Propose an update
proposal = rc.propose_update(
    memory_id="abc123",
    new_context="Actually, the API uses v2 endpoints now, not v1",
)

# Apply it
if proposal.get("confidence", 0) > 0.8:
    rc.apply_update(proposal["id"])

# Check version history
history = rc.get_version_history("abc123")
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `propose_memory_update` | Propose an update to a memory based on new context |
| `apply_memory_update` | Apply a pending reconsolidation proposal |
| `reject_memory_update` | Reject a pending proposal |
| `get_memory_versions` | Get full version/edit history of a memory |
| `list_pending_updates` | List proposals awaiting approval |
| `get_reconsolidation_stats` | Stats on reconsolidation activity |
