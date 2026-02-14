# engram-enterprise

Enterprise governance layer for [Engram](../README.md) — adds policy enforcement, provenance tracking, async operations, and an authenticated REST API on top of the core memory engine.

## Install

```bash
pip install engram-enterprise              # core
pip install "engram-enterprise[api]"       # + REST API server
pip install "engram-enterprise[all]"       # everything
```

Requires `engram-memory` and `engram-bus` (installed automatically).

## What it adds

| Feature | What it does |
|:--------|:-------------|
| **PersonalMemoryKernel** | Orchestrates acceptance gates, provenance, invariants, and sleep cycles |
| **Policy engine** | Feature flags and scoping — control what memory features are active per deployment |
| **Provenance tracking** | Full audit trail for every memory operation (who, when, what, why) |
| **Acceptance gates** | Validate and filter memories before they enter long-term storage |
| **Invariants** | Data integrity checks across the memory store |
| **Episodic store** | Structured episodic memory with scene management |
| **Staging store** | Write-ahead staging with policy gateway before committing to memory |
| **Reference counting** | Track memory references to prevent premature decay of strongly-linked memories |
| **Dual search** | Hybrid semantic + episodic retrieval with reranking |
| **Context packing** | Intelligent context assembly for LLM prompts |
| **Async wrappers** | Non-blocking memory, SQLite, LLM, and embedder operations |
| **REST API** | FastAPI server with authentication at `http://127.0.0.1:8100` |
| **CLI** | Extended command-line interface with governance commands |

## Quick Start

```python
from engram_enterprise import PersonalMemoryKernel

kernel = PersonalMemoryKernel()
```

## REST API

```bash
engram-api                  # starts server at http://127.0.0.1:8100
```

Endpoints include memory CRUD, handoff session management, and governance operations. Interactive docs at `/docs`.

## Integrations

- **Claude Code** — extended plugin with governance-aware hooks
- **OpenClaw** — integration for OpenClaw-based agents

## Architecture

```
engram_enterprise/
├── kernel.py           # PersonalMemoryKernel — main orchestrator
├── policy.py           # Feature flags and policy scoping
├── acceptance.py       # Memory acceptance gates
├── provenance.py       # Audit trail tracking
├── invariants.py       # Data integrity checks
├── schema.py           # Database schema extensions
├── dual_search.py      # Hybrid semantic + episodic search
├── reranker.py         # Result reranking
├── context_packer.py   # LLM context assembly
├── episodic_store.py   # Structured episodic memory
├── staging_store.py    # Write-ahead staging
├── refcounts.py        # Reference counting for decay
├── active_memory.py    # Active memory layer
├── client.py           # Client library
├── async_memory.py     # Async memory wrapper
├── async_sqlite.py     # Async SQLite wrapper
├── async_llm.py        # Async LLM wrapper
├── async_embedder.py   # Async embedder wrapper
├── cli.py              # CLI utilities
├── main_cli.py         # CLI entry point
├── api/
│   ├── app.py          # FastAPI application
│   ├── server.py       # Server runner
│   ├── auth.py         # Authentication
│   └── schemas.py      # Request/response models
└── integrations/
    ├── claude_code.py   # Claude Code integration
    └── openclaw.py      # OpenClaw integration
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT
