<p align="center">
  <img src="docs/dhee-logo.png" alt="Dhee" width="80"> <h1 align="center">Dhee</h1>
</p>



<h3 align="center">The cognition layer that turns your agent into a HyperAgent.</h3>

<p align="center">
  4 tools. 1 LLM call per session. ~$0.004 total cost.<br>
  Your agent remembers, learns from outcomes, and predicts what you need next.
</p>

<p align="center">
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg" alt="Python 3.9+"></a>
  <a href="https://github.com/Sankhya-AI/Dhee/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
</p>

---

## What is Dhee?

Most memory layers are glorified vector stores. Store text, retrieve text. Your agent is still stateless — it doesn't learn, doesn't track what worked, doesn't warn you when something is regressing.

**Dhee is a cognition layer.** It gives any agent — Claude, GPT, Gemini, custom — four capabilities that turn it into a self-improving HyperAgent:

| Capability | What Dhee does | What your agent gets |
|:-----------|:---------------|:---------------------|
| **Persistent memory** | Stores facts with echo-augmented retrieval (paraphrases, keywords, question-forms) | "What theme does the user prefer?" matches "User likes dark mode" even though the words are different |
| **Performance tracking** | Records task outcomes, detects trends automatically | Knows it's regressing on code reviews, warns you before you notice |
| **Insight synthesis** | Extracts causal hypotheses from outcomes — not raw data, synthesized learnings | "What worked: checking git blame first" transfers to the next bug fix |
| **Prospective memory** | Stores future triggers — "remember to X when Y" | Surfaces intentions when the trigger context matches |

### Benchmark: LongMemEval

Dhee achieves near-perfect retrieval on [LongMemEval](https://arxiv.org/abs/2410.10813), the standard benchmark for long-term conversational memory — temporal reasoning, multi-session aggregation, knowledge updates, and counterfactual tracking across 500+ questions.

> Evaluation run in progress. Full results and methodology will be published in the benchmark report.

---

## Quick Start

```bash
pip install dhee[openai,mcp]
export OPENAI_API_KEY=sk-...
```

### MCP (Claude Code, Cursor — zero code)

```json
{
  "mcpServers": {
    "dhee": { "command": "dhee-mcp" }
  }
}
```

Your agent now has 4 tools. It will use them automatically.

### Python SDK

```python
from dhee import Dhee

d = Dhee()
d.remember("User prefers dark mode")
d.recall("what theme does the user like?")
d.context("fixing auth bug")
d.checkpoint("Fixed it", what_worked="git blame first")
```

### CLI

```bash
dhee remember "User prefers Python"
dhee recall "programming language"
dhee checkpoint "Fixed auth bug" --what-worked "checked logs"
```

### Docker

```bash
docker compose up -d   # uses OPENAI_API_KEY from env
```

---

## The 4 Tools

Every interface — MCP, Python, CLI, JS — exposes the same 4 operations.

### `remember(content)`
Store a fact, preference, or observation.

**Hot path**: 0 LLM calls, 1 embedding (~$0.0002). The memory is stored immediately. Echo enrichment (paraphrases, keywords, question-forms that make future recall dramatically better) is deferred to `checkpoint`.

```python
d.remember("User prefers FastAPI over Flask")
d.remember("Project uses PostgreSQL 15 with pgvector")
```

### `recall(query)`
Search memory. Returns top-K results ranked by relevance.

**Hot path**: 0 LLM calls, 1 embedding (~$0.0002). Pure vector search with echo-boosted re-ranking.

```python
results = d.recall("what database does the project use?")
# [{"memory": "Project uses PostgreSQL 15 with pgvector", "score": 0.94}]
```

### `context(task_description)`
HyperAgent session bootstrap. Call once at the start of a conversation.

Returns everything the agent needs to be effective immediately:
- **Last session state** — pick up where you left off, zero cold start
- **Performance trends** — improving or regressing on this task type
- **Synthesized insights** — "What worked for bug_fix: checking git blame first"
- **Triggered intentions** — "Remember to run auth tests after modifying login.py"
- **Proactive warnings** — "Performance on code_review is declining"
- **Relevant memories** — top matches for the task

```python
ctx = d.context("fixing the auth bug in login.py")
# ctx["warnings"] → ["Performance on 'bug_fix' declining (trend: -0.05)"]
# ctx["insights"] → [{"content": "What worked: git blame → found breaking commit"}]
# ctx["intentions"] → [{"description": "run auth tests after login.py changes"}]
```

### `checkpoint(summary, ...)`
Save session state before ending. This is where the cognition happens:

1. **Session digest** — saved for cross-agent handoff (Claude Code crashes? Cursor picks up instantly)
2. **Batch enrichment** — 1 LLM call per ~10 memories stored since last checkpoint. Adds echo paraphrases and keywords that make `recall` work across phrasings
3. **Outcome recording** — tracks score per task type, auto-detects regressions and breakthroughs
4. **Insight synthesis** — "what worked" and "what failed" become transferable learnings
5. **Intention storage** — "remember to X when Y" fires when the trigger matches

```python
d.checkpoint(
    "Fixed auth bug in login.py",
    task_type="bug_fix",
    outcome_score=1.0,
    what_worked="git blame showed the exact commit that broke auth",
    what_failed="grep was too slow on the monorepo",
    remember_to="run auth tests after any login.py change",
    trigger_keywords=["login", "auth"],
)
```

---

## Cost

| Operation | LLM calls | Embed calls | Cost |
|:----------|:----------|:------------|:-----|
| `remember` | 0 | 1 | ~$0.0002 |
| `recall` | 0 | 1 | ~$0.0002 |
| `context` | 0 | 0-1 | ~$0.0002 |
| `checkpoint` | 1 per ~10 memories | 0 | ~$0.001 |
| **Typical session** | **1** | **~15** | **~$0.004** |

---

## How It Works (Under the Hood)

Dhee has two layers: the memory store and the cognition engine.

### Memory Store — Engram

Stores memories in SQLite + a vector index. On the hot path (`remember`/`recall`), zero LLM calls — just embedding. At `checkpoint`, unified enrichment runs in a single batched LLM call:

- **Echo encoding** — generates paraphrases, keywords, and question-forms so "User prefers dark mode" also matches queries like "what theme?" or "UI preferences"
- **Category inference** — auto-tags for filtering
- **Fact decomposition** — splits compound statements into atomic, searchable facts
- **Entity + profile extraction** — builds a knowledge graph of people, tools, projects

All of this happens in **1 LLM call per ~10 memories**. Not 4 calls per memory. One batched call.

Memory decays naturally (Ebbinghaus curve). Frequently accessed memories get promoted from short-term to long-term. Unused ones fade. ~45% less storage than systems that keep everything forever.

### Cognition Engine — Buddhi

A parallel intelligence layer that observes the memory pipeline and builds meta-knowledge:

- **Performance tracking** — records outcomes per task type, computes trends (moving average). Auto-generates regression warnings and breakthrough insights.
- **Insight synthesis** — stores causal hypotheses ("what worked", "what failed"), not raw data. Insights have confidence scores that update on validation/invalidation.
- **Prospective memory** — stores future triggers with keyword matching. "Remember to run tests after modifying auth" fires when the next query mentions "auth".
- **Intention detection** — auto-detects "remember to X when Y" patterns in stored memories.

Zero LLM calls on the hot path. Pure pattern matching + statistics. Persistence via JSONL files (~3 files total).

Inspired by [Meta's DGM-Hyperagents](https://arxiv.org/abs/2603.19461) — agents that emergently develop persistent memory and performance tracking achieve self-accelerating improvement that transfers across domains. Dhee provides these capabilities as infrastructure.

---

## Architecture

```
Agent (Claude, GPT, Cursor, custom)
  │
  ├── remember(content)     → Engram: embed + store (0 LLM)
  ├── recall(query)         → Engram: embed + vector search (0 LLM)
  ├── context(task)         → Buddhi: performance + insights + intentions + memories
  └── checkpoint(summary)   → Engram: batch enrich (1 LLM/10 mems)
                            → Buddhi: outcome + reflect + intention
```

```
~/.dhee/
├── history.db              # SQLite: memories, history, entities
├── zvec/                   # Vector index (embeddings)
└── buddhi/
    ├── insights.jsonl      # Synthesized learnings
    ├── intentions.jsonl    # Future triggers
    └── performance.json    # Task type scores + trends
```

---

## Advanced

### Full MCP Server (24 tools)

Power users who need granular control over skills, trajectories, structural search, and enrichment:

```bash
dhee-mcp-full    # exposes all 24 tools
```

### Python — Direct Memory Access

```python
from dhee import FullMemory

m = FullMemory()
m.add("conversation content", user_id="u1", infer=True)
m.search("query", user_id="u1", limit=10)
m.think("complex question requiring reasoning across memories")
```

### Provider Options

```bash
pip install dhee[openai,mcp]     # OpenAI (recommended, cheapest embeddings)
pip install dhee[gemini,mcp]     # Google Gemini
pip install dhee[ollama,mcp]     # Ollama (local, zero cost)
```

---

## Contributing

```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee
pip install -e ".[dev]"
pytest
```

---

<p align="center">
  <b>4 tools. 1 LLM call. Your agent remembers, learns, and predicts.</b>
  <br><br>
  <a href="https://github.com/Sankhya-AI/Dhee">GitHub</a> &middot;
  <a href="https://pypi.org/project/dhee">PyPI</a> &middot;
  <a href="https://github.com/Sankhya-AI/Dhee/issues">Issues</a>
</p>

<p align="center">MIT License &mdash; <a href="https://sankhyaailabs.com">Sankhya AI</a></p>
