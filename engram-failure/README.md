# engram-failure — The Debugger

Failure learning for AI agents. Log failures, extract anti-patterns, and discover recovery strategies.

## Features

- **Log failures** with action, error, context, and severity
- **Search past failures** for similar situations
- **Extract anti-patterns** — learn what NOT to do from failure clusters
- **Recovery strategies** — discover what worked to fix similar failures
- **Feeds into procedural memory** — successful recoveries become new procedures

## Installation

```bash
pip install engram-failure
```

## Quick Start

```python
from engram.memory.main import Memory
from engram_failure import FailureLearning

memory = Memory(config=...)
fl = FailureLearning(memory, user_id="default")

# Log a failure
fl.log_failure(
    action="deploy_to_prod",
    error="Connection timeout after 30s",
    context="Deploying at 2am during maintenance window",
    severity="high",
)

# Search for similar past failures
results = fl.search_failures("deployment timeout")

# Extract anti-patterns from failure cluster
fl.extract_antipattern(failure_ids=["id1", "id2", "id3"])
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `log_failure` | Log a failure with context |
| `search_failures` | Search past failures |
| `extract_antipattern` | Extract what NOT to do |
| `list_antipatterns` | List anti-patterns |
| `search_recovery_strategies` | Find recovery strategies |
