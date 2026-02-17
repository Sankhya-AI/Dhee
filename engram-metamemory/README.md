# engram-metamemory — The Oracle

Confidence scoring, feeling-of-knowing, and knowledge gap tracking for Engram.

## What it does

Metamemory is "knowing what you know." This package adds:

- **Confidence scoring** on every memory (0.0-1.0)
- **Feeling of Knowing** queries — "do I know about X?"
- **Knowledge gap registry** — "what don't I know?"
- **Retrieval calibration** — tracking accuracy over time

## Install

```bash
pip install engram-metamemory
# or from source
pip install -e engram-metamemory/
```

## Usage

```python
from engram.memory.main import Memory
from engram_metamemory import Metamemory

memory = Memory(config=...)
mm = Metamemory(memory, user_id="default")

# Feeling of Knowing
fok = mm.feeling_of_knowing("quantum computing")
print(fok["verdict"])  # "confident", "uncertain", or "unknown"

# Knowledge gaps
gaps = mm.list_knowledge_gaps()

# Calibration
stats = mm.get_calibration_stats()
```

## MCP Tools (6)

| Tool | Description |
|------|-------------|
| `feeling_of_knowing` | "Do I know about X?" |
| `list_knowledge_gaps` | List things the system doesn't know |
| `resolve_knowledge_gap` | Mark a gap as resolved |
| `log_retrieval_outcome` | Record if retrieval was useful/wrong/irrelevant |
| `get_calibration_stats` | Accuracy stats over rolling window |
| `get_memory_confidence` | Confidence details for a specific memory |

## How confidence works

Confidence is computed from 5 weighted signals:

1. **Strength** (30%) — FadeMem decay-adjusted strength
2. **Echo depth** (20%) — deeper encoding = more confident
3. **Access count** (15%) — frequently retrieved = validated
4. **Recency** (15%) — newer = slightly more confident
5. **Source reliability** (20%) — explicit user statements > inferences
