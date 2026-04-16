<p align="center">
  <img src="docs/dhee-logo.png" alt="Dhee" width="80"> <h1 align="center">Dhee</h1>
</p>

<h3 align="center">Stop burning tokens on context your agent doesn't need this turn.</h3>

<p align="center">
  Keep your CLAUDE.md, your skills, your AGENTS.md — exactly as they are.<br>
  Dhee selects what's relevant per prompt and injects only that.
</p>

<p align="center">
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg" alt="Python 3.9+"></a>
  <a href="https://github.com/Sankhya-AI/Dhee/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
</p>

---

## The Problem

Every AI coding agent today dumps your entire CLAUDE.md into every LLM call. 200 lines of project rules, coding conventions, testing guidelines — loaded into the prompt whether you're running tests or writing a docstring. Every turn. Full price.

Over a 20-turn session on Opus, that's **40,000+ tokens of mostly-irrelevant context**. You're paying for the model to read your git commit conventions while it's fixing an auth bug.

And it gets worse over time. After 6 months, your CLAUDE.md is 500 lines. Your skills directory has 12 files. Your AGENTS.md has grown. But the agent still loads all of it, every turn, at full token cost. The markdown files that were supposed to make your agent smarter are now your biggest line item.

## How Dhee Fixes It

```
Before Dhee:  CLAUDE.md (2000 tokens) → loaded every turn → 40K tokens/session
With Dhee:    CLAUDE.md → chunked + vectorized → ~300 tokens of relevant rules per turn
```

Dhee sits between your documentation and the LLM. It chunks your markdown files into heading-scoped pieces, embeds them once, and on each prompt selects only the chunks that match what the user is actually asking about.

**"How do I run the tests?"** → Dhee injects your Testing Guidelines section (292 tokens), not your entire CLAUDE.md (2000 tokens). **67% reduction, zero information loss.**

**"Explain dark matter to me"** → Dhee injects nothing. No project docs are relevant. **100% reduction.**

Your files stay exactly where they are. You maintain them the same way. Dhee just makes the delivery intelligent.

---

## Quick Start

**One command. No venv. No config. No pasting into settings.json.**

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

That's it. The installer creates `~/.dhee` with a hidden venv, installs the `dhee` package, and wires Claude Code hooks automatically. Next time you open Claude Code in any project, cognition is on.

<details>
<summary>Other install options</summary>

**Via pip (if you manage your own venv):**
```bash
pip install dhee
dhee install       # configure Claude Code hooks
```

**From source (contributors):**
```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee
./scripts/bootstrap_dev_env.sh
source .venv-dhee/bin/activate
dhee install
```
</details>

After install, Dhee auto-ingests project docs (CLAUDE.md, AGENTS.md, etc.) on the first session. Run `dhee ingest` manually any time to re-chunk.

---

## The Lifecycle

Dhee manages information through a complete lifecycle — not just storage and retrieval, but learning, decay, and promotion.

```
                        ┌─────────────────────────┐
                        │   Your Documentation     │
                        │   CLAUDE.md, AGENTS.md   │
                        │   SKILL.md, etc.         │
                        └──────────┬──────────────┘
                                   │
                            dhee ingest
                          (chunk + embed)
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────┐
│                     Dhee Vector Store                         │
│                                                              │
│  Doc Chunks (high strength, heading-scoped)                  │
│  Short-term memories (facts, file edits, failures)           │
│  Long-term memories (promoted by access + strength)          │
│  Typed cognition (insights, beliefs, policies, intentions)   │
└──────────────────────────────────────────────────────────────┘
                                   │
                        ┌──────────┴──────────┐
                        │                     │
                   Session Start         Each Prompt
                   (full assembly)     (doc chunks only)
                        │                     │
                        ▼                     ▼
              ┌─────────────────┐   ┌─────────────────┐
              │ Relevant docs   │   │ Matching rules   │
              │ + insights      │   │ above threshold  │
              │ + performance   │   │ or nothing       │
              │ + warnings      │   │                  │
              └────────┬────────┘   └────────┬────────┘
                       │                     │
                       ▼                     ▼
              ┌─────────────────────────────────────┐
              │  Token-budgeted XML injection        │
              │  <dhee>                              │
              │    <r s="0.83">Always run pytest...│
              │    </r>                              │
              │  </dhee>                             │
              └─────────────────────────────────────┘
                                   │
                            LLM sees only
                         what it needs this turn
```

### During the session

| Event | What Dhee does | Token cost |
|:------|:---------------|:-----------|
| **Session opens** | Auto-ingests stale docs, assembles relevant doc chunks + typed cognition | ~300-900 tokens (vs 2000+ for raw files) |
| **Each user prompt** | Searches doc chunks for THIS specific question. Injects matching rules above threshold. | 0-300 tokens (0 when nothing matches) |
| **Tool use (Edit/Write)** | Records which files were touched (for session context) | 0 tokens (storage only) |
| **Tool failure (Bash)** | Stores the failure + error message as a learnable signal | 0 tokens (storage only) |
| **Session ends** | Checkpoints outcomes, what worked/failed → becomes insights for next session | 0 tokens (storage only) |

### Between sessions

| Phase | What happens |
|:------|:-------------|
| **Short-term memory** | Facts from the session sit in SML with natural decay |
| **Promotion** | Frequently-accessed memories promote to long-term (LML) automatically |
| **Decay** | Unused memories fade on an Ebbinghaus curve. Your 50th memory costs the same as your 50,000th. |
| **Insight synthesis** | `what_worked` / `what_failed` from checkpoints become transferable learnings |
| **Intentions** | "Remember to run auth tests after login.py changes" fires when the trigger matches |

### The result

Your documentation stays fat and thorough — that's your team's knowledge base. But the LLM only sees the slice it needs, when it needs it. After a year, you have 50 files and 10,000 memories. The per-turn injection is still ~300 tokens.

---

## Why Not Just CLAUDE.md?

Markdown files work great at first. 50 lines, manually curated, loaded fresh every session. But they don't scale:

| | Markdown files | Dhee |
|:--|:--------------|:-----|
| **Token cost** | Linear with file size. 500 lines = 5000 tokens every turn. | Constant ~300 tokens regardless of total knowledge. |
| **Relevance** | Everything loaded, always. Git commit rules injected while fixing auth. | Only matching chunks. Off-topic turns cost 0 tokens. |
| **Staleness** | Equal weight forever. A rule from 6 months ago sits next to today's. | Natural decay. Unused knowledge fades. Fresh knowledge surfaces. |
| **Scale** | Hits context limits. You start deleting old rules to make room. | 50,000 memories, same injection cost as 50. |
| **Learning** | Static. Agent makes the same mistakes next session. | Captures what worked/failed. Synthesizes transferable insights. |
| **Cross-session** | Cold start every time unless you manually update the file. | Session handoff, performance trends, prospective memory. |

**Dhee doesn't replace your markdown files. It makes them work at scale.** Keep writing CLAUDE.md the way you always have. Dhee handles the delivery.

---

## The 4-Operation API

Every interface — hooks, MCP, Python, CLI — exposes the same 4 operations.

### `remember(content)` — Store a fact
0 LLM calls, 1 embedding (~$0.0002). Stored immediately. Echo enrichment (paraphrases, keywords for better recall) runs at checkpoint.

```python
d.remember("User prefers FastAPI over Flask")
```

### `recall(query)` — Search memory
0 LLM calls, 1 embedding. Pure vector search with echo-boosted re-ranking.

```python
results = d.recall("what framework does the project use?")
# [{"memory": "User prefers FastAPI over Flask", "score": 0.94}]
```

### `context(task_description)` — Session bootstrap
Returns everything the agent needs: last session state, performance trends, insights, intentions, warnings, and relevant memories.

```python
ctx = d.context("fixing the auth bug")
# ctx["insights"] → [{"content": "What worked: git blame → found breaking commit"}]
# ctx["warnings"] → ["Performance on 'bug_fix' declining"]
```

### `checkpoint(summary, ...)` — End-of-session cognition
Where the learning happens. 1 LLM call per ~10 memories.

```python
d.checkpoint(
    "Fixed auth bug",
    what_worked="git blame showed the exact breaking commit",
    what_failed="grep was too slow on the monorepo",
    outcome_score=1.0,
)
```

---

## Integration

### Claude Code — Native Hooks

```bash
pip install dhee
dhee install    # installs lifecycle hooks
dhee ingest     # chunks project docs into vector memory
```

That's it. Six hooks fire automatically at the right moments. No SKILL.md, no plugin directories. The agent doesn't even know Dhee is there — it just gets better context.

### MCP Server (Claude Code, Cursor, any MCP client)

```json
{
  "mcpServers": {
    "dhee": { "command": "dhee-mcp" }
  }
}
```

4 tools exposed. The agent uses them as needed.

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
dhee ingest CLAUDE.md AGENTS.md    # chunk specific files
dhee ingest                        # auto-scan project
dhee docs                          # show ingested manifest
dhee checkpoint "Fixed auth bug" --what-worked "checked logs"
dhee install                       # install Claude Code hooks
dhee uninstall-hooks               # remove them
```

### Docker

```bash
docker compose up -d   # uses OPENAI_API_KEY from env
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

The Dhee overhead per session is ~$0.004. The token savings from selective injection on a 20-turn Opus session are ~$0.50+. **>100x ROI.**

---

## Under the Hood

### Memory Store (Engram)

SQLite + vector index. On the hot path (`remember`/`recall`), zero LLM calls — just embedding. At `checkpoint`, unified enrichment runs in one batched LLM call:

- **Echo encoding** — paraphrases, keywords, question-forms so "User likes dark mode" matches "what theme?"
- **Category inference** — auto-tags for filtering
- **Strength-based decay** — Ebbinghaus curve. Frequently accessed → promoted to long-term. Unused → fades.

### Cognition Engine (Buddhi)

Parallel intelligence layer that builds meta-knowledge from the memory pipeline:

- **Performance tracking** — outcomes per task type, trend detection, regression warnings
- **Insight synthesis** — causal hypotheses from what worked/failed, with confidence scores
- **Prospective memory** — future triggers with keyword matching
- **Belief store** — confidence-tracked facts with contradiction detection (experimental)
- **Policy store** — condition→action rules from task completions (experimental)

Zero LLM calls on hot path. Pure pattern matching + statistics.

### Doc Pipeline (v3.3.1)

- **Chunker** — heading-scoped splits that respect code fences, paragraph boundaries, size limits
- **Ingest** — SHA-tracked. Re-ingesting unchanged files is a no-op. Changed files get atomic chunk replacement.
- **Assembler** — vector similarity search filtered by `kind=doc_chunk`, score threshold, token budget
- **Renderer** — Caveman-compressed XML: `<dhee><r s="0.83">...</r></dhee>`. No header, no wrapper tags, no indentation — every byte earns its place. ~40% fewer structural tokens vs v3.3.

---

## Provider Options

```bash
pip install dhee[openai,mcp]     # OpenAI (recommended, cheapest embeddings)
pip install dhee[gemini,mcp]     # Google Gemini
pip install dhee[ollama,mcp]     # Ollama (local, no API costs)
```

---

## Contributing

```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee
./scripts/bootstrap_dev_env.sh
source .venv-dhee/bin/activate
pytest    # 978 tests
```

---

<p align="center">
  <b>Your docs stay fat. Your token bill stays thin.</b>
  <br><br>
  <a href="https://github.com/Sankhya-AI/Dhee">GitHub</a> &middot;
  <a href="https://pypi.org/project/dhee">PyPI</a> &middot;
  <a href="https://github.com/Sankhya-AI/Dhee/issues">Issues</a>
</p>

<p align="center">MIT License &mdash; <a href="https://sankhyaailabs.com">Sankhya AI</a></p>
