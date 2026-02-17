# engram-procedural — The Craftsman

Procedural memory for AI agents. Learn, refine, and recall step-by-step procedures from experience.

## Features

- **Extract procedures** from episode memories using LLM analysis
- **Track execution** success/failure rates per procedure
- **Automaticity boost** — well-practiced procedures rank higher in search
- **Cross-domain abstraction** — strip domain details for transferable patterns
- **Version history** — all refinements logged via memory_history

## Installation

```bash
pip install engram-procedural
```

## Quick Start

```python
from engram.memory.main import Memory
from engram_procedural import Procedural

memory = Memory(config=...)
proc = Procedural(memory, user_id="default")

# Extract a procedure from episodes
result = proc.extract_procedure(
    episode_ids=["ep1", "ep2", "ep3"],
    name="debug_python_imports",
    domain="python",
)

# Log an execution
proc.log_execution(result["id"], success=True)

# Search for procedures
results = proc.search_procedures("how to debug imports")
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `extract_procedure` | Extract a reusable procedure from episode memories |
| `get_procedure` | Get a procedure's steps, stats, and execution history |
| `search_procedures` | Semantic search for relevant procedures |
| `log_procedure_execution` | Record success/failure of a procedure run |
| `refine_procedure` | Update procedure steps based on new experience |
| `list_procedures` | List procedures by status |
