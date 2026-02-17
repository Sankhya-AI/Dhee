# engram-prospective — The Planner

Prospective memory: remembering the future. Store intentions with triggers that fire when conditions are met.

## What it does

Prospective memory is "remembering to remember." This package adds:

- **Intention store** with time, event, and condition triggers
- **Trigger evaluation** — periodic (via heartbeat) or manual
- **Intention lifecycle**: pending -> triggered -> completed/expired/cancelled
- **Priority + decay** for abandoned intentions

## Install

```bash
pip install engram-prospective
# or from source
pip install -e engram-prospective/
```

## Usage

```python
from engram.memory.main import Memory
from engram_prospective import Prospective

memory = Memory(config=...)
pm = Prospective(memory, user_id="default")

# Add a time-triggered intention
pm.add_intention(
    description="Send weekly report",
    trigger_type="time",
    trigger_value="2025-01-20T09:00:00Z",
    action="remind user to send weekly report",
)

# Add an event-triggered intention
pm.add_intention(
    description="Run tests after deploy",
    trigger_type="event",
    trigger_value="deploy_complete",
    action="run full test suite",
)

# Check what's due
triggered = pm.check_triggers()
for t in triggered:
    print(f"Triggered: {t['description']}")
    pm.complete_intention(t["id"])
```

## MCP Tools (6)

| Tool | Description |
|------|-------------|
| `add_intention` | Remember something to do later |
| `list_intentions` | List intentions by status |
| `check_intention_triggers` | Evaluate pending intentions, return triggered ones |
| `complete_intention` | Mark intention as done |
| `cancel_intention` | Cancel a pending intention |
| `get_due_intentions` | Get all time-triggered intentions past due |

## Trigger types

- **time**: ISO datetime — fires when current time passes the trigger time
- **event**: Event name — fires when a matching event is provided
- **condition**: Key=value expression — fires when context contains matching key/value
