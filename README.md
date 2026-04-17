<p align="center">
  <img src="docs/dhee-logo.png" alt="Dhee" width="80">
</p>

<h1 align="center">Dhee</h1>

<h3 align="center">Self-evolving memory for every AI agent.</h3>

<p align="center">
  Fat skills, thin tokens. Perfect recall, even years in.<br>
  Future-proof your CLAUDE.md, your AGENTS.md, your GBrain, your skills library.
</p>

<p align="center">
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg?style=flat-square" alt="Python 3.9+"></a>
  <a href="https://github.com/Sankhya-AI/Dhee/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="MIT License"></a>
  <a href="benchmarks/longmemeval/"><img src="https://img.shields.io/badge/LongMemEval-%231%20recall%20%E2%80%94%20R%401%2094.8%25-brightgreen.svg?style=flat-square" alt="#1 on LongMemEval recall"></a>
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/pypi/v/dhee?style=flat-square&color=orange" alt="PyPI"></a>
</p>

<p align="center">
  <b>#1 on LongMemEval retrieval</b> — R@1 <b>94.8%</b> / R@5 <b>99.4%</b> / R@10 <b>99.8%</b> on 500 questions, no held-out split. <a href="#benchmarks">Beats MemPalace and agentmemory →</a>
</p>

<p align="center">
  <img src="docs/demo/demo.gif" alt="Dhee demo — fat skills, thin tokens, self-evolving retrieval" width="900">
</p>

<p align="center">
  <a href="#what-dhee-is">What is Dhee</a> &middot;
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#benchmarks">Benchmarks</a> &middot;
  <a href="#vs-alternatives">vs Alternatives</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#integrations">Integrations</a>
</p>

---

## What Dhee is

Dhee is a **self-evolving memory and cognition layer** that sits between your AI agent and the LLM. It does three things, well:

1. **Reduces tokens.** The agent only sees the slice of context it needs this turn. Your 2,000-line CLAUDE.md becomes ~300 tokens. A 10 MB tool-result becomes a 40-token digest with a pointer. Over a session, that's a 90%+ token reduction with zero information loss.

2. **Remembers everything — forever.** Doc chunks, session outcomes, failures, decisions, user preferences. Ebbinghaus decay pushes unused knowledge out of the hot path. Frequently-referenced memories are promoted. After five years you have 50,000 memories and the per-turn injection is still ~300 tokens.

3. **Self-evolves.** Dhee watches which memories the model actually expands, which digests are deep enough, which rules it ignores. It tunes its own retrieval depth per tool, per intent, per file type. No config file to hand-maintain. The longer you use it, the better it gets.

### Who it's for

- **Every Claude Code / Cursor / Codex / Gemini CLI / Aider / Cline user** who has ever hit a context limit or a $200 token bill.
- **Anyone maintaining a fat agent** — a 5,000-line CLAUDE.md, a Skills library, a GBrain-style agent framework, a library of prompts. Dhee future-proofs it. Keep writing. Dhee handles the delivery.
- **Teams building AI products** who want one memory layer that works across every model, every host, every language. SQLite + MCP. No infra to run.

---

## Quick Start

**One command. No venv. No config. No pasting into `settings.json`.**

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

The installer creates `~/.dhee`, installs the `dhee` package, and auto-wires Claude Code hooks. Next time you open Claude Code in any project, cognition is on.

<details>
<summary><b>Other install options</b></summary>

**Via pip:**
```bash
pip install dhee
dhee install       # configure Claude Code hooks
```

**From source:**
```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee
./scripts/bootstrap_dev_env.sh
source .venv-dhee/bin/activate
dhee install
```

**Via Docker:**
```bash
docker compose up -d   # uses OPENAI_API_KEY from env
```
</details>

After install, Dhee auto-ingests project docs (CLAUDE.md, AGENTS.md, SKILL.md, etc.) on the first session. Run `dhee ingest` manually any time to re-chunk.

---

## Benchmarks

> **#1 on LongMemEval recall.** R@1 94.8%, R@5 99.4%, R@10 99.8% — full 500 questions, no held-out split, no cherry-picking.

| System | R@1 | R@3 | R@5 | R@10 |
|:-------|:----|:----|:----|:-----|
| **Dhee v3.4.0** | **94.8%** | **99.0%** | **99.4%** | **99.8%** |
| [MemPalace](https://github.com/MemPalace/mempalace#benchmarks) (raw) | — | — | 96.6% | — |
| [MemPalace](https://github.com/MemPalace/mempalace#benchmarks) (hybrid v4, held-out 450q) | — | — | 98.4% | — |
| [agentmemory](https://github.com/rohitg00/agentmemory#benchmarks) | — | — | 95.2% | 98.6% |

Stack: NVIDIA `llama-nemotron-embed-vl-1b-v2` embedder + `llama-3.2-nv-rerankqa-1b-v2` reranker, top-k 10.

**Proof is in-tree, not screenshots.** Exact command, metrics, and per-question output are committed under [`benchmarks/longmemeval/`](benchmarks/longmemeval/). Recompute R@k yourself — any mismatch is a bug you can open.

---

## What Dhee does on every turn

```
                      ┌──────────────────────────┐
                      │   Your fat context        │
                      │   CLAUDE.md, AGENTS.md,   │
                      │   SKILL.md, GBrain, docs  │
                      │   session history, tools  │
                      └──────────┬───────────────┘
                                 │
                         Dhee ingests once
                      (chunk + embed + index)
                                 │
                                 ▼
  ┌───────────────────────────────────────────────────────────────┐
  │                   Dhee memory + cognition                      │
  │                                                                │
  │  Doc chunks · Short-term · Long-term · Insights · Beliefs     │
  │  Policies · Intentions · Performance · Episodes · Edits       │
  └────────────────────────────┬──────────────────────────────────┘
                                │
           ┌────────────────────┴────────────────────┐
           │                                          │
     Session start                              Each user prompt
     (full assembly)                            (doc chunks only)
           │                                          │
           ▼                                          ▼
   ┌───────────────┐                         ┌───────────────┐
   │ Relevant docs  │                         │ Matching rules │
   │ + insights     │                         │ above threshold│
   │ + performance  │                         │ or nothing     │
   │ + warnings     │                         │                │
   └───────┬───────┘                         └───────┬───────┘
           │                                          │
           └────────────────────┬────────────────────┘
                                │
                                ▼
                ┌──────────────────────────────┐
                │  Token-budgeted XML            │
                │  <dhee v="1">                  │
                │    <doc src="CLAUDE.md"...>   │
                │    <i>What worked last time</i>│
                │  </dhee>                       │
                └──────────────────────────────┘
                                │
                     LLM sees only what it
                     needs, when it needs it.
```

And on the tool-use side, the **router** digests raw output at source — a 10 MB `git log` becomes a 40-token summary + a pointer. The model expands the pointer only when the digest isn't enough.

---

## <span id="vs-alternatives">vs alternatives</span>

| | **Dhee** | CLAUDE.md | Mem0 | Letta | MemPalace | agentmemory |
|:--|:-:|:-:|:-:|:-:|:-:|:-:|
| **Token cost per turn** | **~300** | 2,000+ | varies | ~1K+ | varies | ~1,900 |
| **LongMemEval R@5** | **99.4%** | N/A | N/A | N/A | 96.6% | 95.2% |
| **Self-evolving retrieval policy** | **Yes** | No | No | No | No | No |
| **Auto-digest tool output** | **Yes (router)** | No | No | No | No | No |
| **Works across every MCP agent** | **Yes** | No | Partial | No | Yes | Yes |
| **Typed cognition (insights/beliefs/policies)** | **Yes** | No | No | Partial | No | No |
| **Proof in-tree (reproducible)** | **Yes** | — | — | — | Yes | Partial |
| **External DB required** | No (SQLite) | No | Qdrant/pgvector | Postgres+vector | No | No |
| **License** | MIT | — | Apache-2 | Apache-2 | MIT | MIT |

Dhee is the only one that **reduces tokens *and* self-evolves its own retrieval policy *and* leads on recall.**

---

## Self-evolution — the part nobody else does

Most memory layers are static: you write rules, they retrieve. Dhee watches what happens and tunes itself.

- **Intent classification**: Every `Read`/`Bash`/`Agent` call is bucketed by intent (source code, test, config, doc, data, build). Each intent gets its own retrieval depth.
- **Expansion tracking**: When the model calls `dhee_expand_result(ptr)`, Dhee logs which tool / intent / depth required expansion. A digest that's always expanded is too shallow. A digest that's never expanded might be too deep.
- **Policy tuning**: `dhee router tune` reads the expansion ledger, applies thresholds (>30% expansion → deepen; <5% expansion → make shallower), and persists the new policy atomically to `~/.dhee/router_policy.json`.
- **No config file to maintain.** The policy is the behavior. The behavior is learned.

This means: **the longer a team uses Dhee, the better it gets at serving that specific team's workflow.** Frontend-heavy teams get deeper JS/TS digests. Data teams get richer CSV/JSONL summaries. You don't have to pick — Dhee picks for you, based on what you actually expand.

---

## Perfect recall, five years in

Doc bloat is every AI team's silent tax. Your CLAUDE.md grew from 50 lines to 500 to 2,000 in 18 months. Your skills directory has 30 files. Your prompt library has a thousand entries. Every new agent session loads all of it, every turn, at full token cost.

Dhee turns that library into searchable, decay-aware, self-promoting memory:

| Phase | What Dhee does |
|:------|:---------------|
| **Ingest** | Heading-scoped chunking with SHA tracking. Re-ingest is a no-op if unchanged. |
| **Hot path** | Vector search + heading-breadcrumb matching. Filters by threshold + token budget. |
| **Promotion** | Frequently-referenced memories auto-promote from short-term to long-term. |
| **Decay** | Unused memories fade on an Ebbinghaus curve. Your 50th memory costs the same as your 50,000th. |
| **Insight synthesis** | What-worked / what-failed from session checkpoints becomes transferable learnings. |
| **Prospective** | `"Remember to run auth tests after login.py changes"` fires when the trigger matches — days, weeks, or months later. |

**Result:** after a year, you have 50 doc files, 10,000 memories, and 200 insights. The per-turn injection is still ~300 tokens of the *right* stuff. The 500-line CLAUDE.md your team grew into is an asset, not a liability.

---

## Future-proof your fat skills

Got a GBrain? An AGENTS.md with 15 sections? A Skills library that your team reluctantly prunes every sprint because it "got too big for context"? Stop pruning. Dhee was built for this.

```bash
# Point Dhee at your skills directory
dhee ingest ~/my-agent/skills/        # 50 files, 200K tokens
dhee ingest ~/my-agent/CLAUDE.md      # 5K tokens
dhee ingest ~/my-agent/AGENTS.md      # 3K tokens
```

Dhee chunks them heading-by-heading, embeds them once, and on every turn only the *relevant* slice is injected. Your fat skills stay fat where they should be — in your repo, authored by humans, reviewable, version-controlled. They just stop paying rent in your model's context window.

---

## How It Works

### The 4-operation API

Every interface — hooks, MCP, Python, CLI — exposes the same 4 operations.

```python
from dhee import Dhee

d = Dhee()
d.remember("User prefers FastAPI over Flask")
d.recall("what framework does the project use?")
d.context("fixing the auth bug")
d.checkpoint("Fixed auth bug", what_worked="git blame first", outcome_score=1.0)
```

| Operation | LLM calls | Cost |
|:----------|:----------|:-----|
| `remember` | 0 | ~$0.0002 |
| `recall` | 0 | ~$0.0002 |
| `context` | 0 | ~$0.0002 |
| `checkpoint` | 1 per ~10 memories | ~$0.001 |
| **Typical 20-turn Opus session** | **1** | **~$0.004** |

Dhee overhead: ~$0.004 per session. Token savings on a 20-turn Opus session: **~$0.50+**. That's a **>100× ROI**.

### The router — digest at source

The part that saves the most tokens isn't doc injection. It's keeping raw tool output out of context in the first place.

Four MCP tools replace `Read`/`Bash`/`Agent` on heavy calls:

- `mcp__dhee__dhee_read(file_path, offset?, limit?)` — returns a digest (symbols, head, tail, kind, token estimate) + pointer. Raw content stored, not injected.
- `mcp__dhee__dhee_bash(command)` — runs the command, digests output by class (git log, pytest, grep, listing, generic), returns summary + pointer.
- `mcp__dhee__dhee_agent(text)` — digests any long subagent return: file refs, headings, bullets, error signals, head/tail.
- `mcp__dhee__dhee_expand_result(ptr)` — only called when the digest genuinely isn't enough. Raw re-enters context on demand.

A 10 MB `git log --oneline -50000` becomes a ~200-token digest. Nothing else gets into context. This is where the serious savings live.

### The cognition engine

Parallel intelligence layer — zero LLM calls on the hot path.

- **Performance tracking** — outcomes per task type, trend detection, regression warnings.
- **Insight synthesis** — causal hypotheses from what worked/failed, with confidence scores.
- **Prospective memory** — future triggers with keyword matching.
- **Belief store** — confidence-tracked facts with contradiction detection.
- **Policy store** — condition→action rules mined from task completions.
- **Edit ledger** — dedup of repeated Edit/Write so compaction keeps the *unique* changes, not 50 iterations.

---

## Integrations

### Claude Code — Native Hooks

```bash
pip install dhee
dhee install    # installs lifecycle hooks
dhee ingest     # chunks project docs into memory
```

Six hooks fire at the right moments. No SKILL.md, no plugin directory. The agent doesn't even know Dhee is there — it just gets better context.

### MCP Server (Claude Code, Cursor, Codex, Gemini CLI, Cline, Goose, any MCP client)

```json
{
  "mcpServers": {
    "dhee": { "command": "dhee-mcp" }
  }
}
```

28 tools exposed. The agent uses them as needed.

### Python SDK / CLI / Docker

```bash
dhee remember "User prefers Python"
dhee recall "programming language"
dhee ingest CLAUDE.md AGENTS.md
dhee docs                          # show ingested manifest
dhee router report                 # router stats + replay projection
dhee router tune                   # re-tune retrieval policy
dhee checkpoint "Fixed auth" --what-worked "checked logs"
```

---

## Provider Options

```bash
pip install dhee[openai,mcp]     # OpenAI (recommended, cheapest embeddings)
pip install dhee[nvidia,mcp]     # NVIDIA NIM (current SOTA stack)
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
pytest    # 1098 tests, 10 skipped
```

Benchmarks are reproducible from [`benchmarks/longmemeval/`](benchmarks/longmemeval/). Any improvement to R@k with full per-question output is welcome.

---

<p align="center">
  <b>Your fat skills stay fat. Your token bill stays thin. Your agent gets smarter every session.</b>
  <br><br>
  <a href="https://github.com/Sankhya-AI/Dhee">GitHub</a> &middot;
  <a href="https://pypi.org/project/dhee">PyPI</a> &middot;
  <a href="https://github.com/Sankhya-AI/Dhee/issues">Issues</a>
</p>

<p align="center">MIT License &mdash; <a href="https://sankhyaailabs.com">Sankhya AI</a></p>
