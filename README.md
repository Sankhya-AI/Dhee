<p align="center">
  <img src="docs/dhee-logo.png" alt="Dhee" width="80">
</p>

<h1 align="center">Dhee — the Developer Brain for AI coding agents</h1>

<h3 align="center">Local memory + context router for Claude Code, Codex, Cursor, Gemini CLI, Aider, Cline, and any MCP client.</h3>

<p align="center">
  Give your agent a brain that <b>remembers what it learned</b>, <b>shares context across your team via git</b>, and <b>cuts LLM tokens by 90%</b> — without a hosted service.
</p>

<p align="center">
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/pypi/v/dhee?style=flat-square&color=orange" alt="PyPI"></a>
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg?style=flat-square" alt="Python 3.9+"></a>
  <a href="https://github.com/Sankhya-AI/Dhee/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="MIT License"></a>
  <a href="benchmarks/longmemeval/"><img src="https://img.shields.io/badge/LongMemEval-%231%20recall%20%E2%80%94%20R%401%2094.8%25-brightgreen.svg?style=flat-square" alt="#1 on LongMemEval recall"></a>
</p>

<p align="center">
  <b>#1 on LongMemEval retrieval</b> — R@1 <b>94.8%</b> · R@5 <b>99.4%</b> · R@10 <b>99.8%</b> on the full 500-question set. <a href="#benchmarks">Reproduce it →</a>
</p>

<p align="center">
  <img src="docs/demo/demo.gif" alt="Dhee demo — fat skills, thin tokens, self-evolving retrieval" width="900">
</p>

<p align="center">
  <a href="#what-is-dhee">What is Dhee</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#repo-shared-context">Repo-Shared Context</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="#how-it-works">How It Works</a> ·
  <a href="#vs-alternatives">vs Alternatives</a> ·
  <a href="#integrations">Integrations</a>
</p>

---

## What is Dhee?

**Dhee is the developer brain that lives next to your AI coding agent.** It runs locally, uses SQLite, plugs into any MCP client, and does three jobs the model can't do for itself:

1. **🧠 Remembers.** Doc chunks, decisions, what worked, what failed, user preferences. Ebbinghaus decay pushes stale knowledge out of the hot path; frequently-used memory gets promoted. Five years in, your per-turn injection is still ~300 tokens of the *right* stuff.

2. **🔁 Routes.** A 10 MB `git log` becomes a 40-token digest with a pointer. Raw output only re-enters context when the model explicitly expands it. Over a session that's a 90%+ token cut with zero information loss.

3. **🌱 Self-evolves.** Dhee watches which digests the model expands, which rules it ignores, which retrievals it actually uses — and tunes its own depth per tool, per intent, per file type. No config to hand-maintain. The longer your team uses it, the better it fits your workflow.

### Who it's for

- **Every Claude Code / Cursor / Codex / Gemini CLI / Aider / Cline user** who has ever hit a context limit or a $200 token bill.
- **Any team** with a 2,000-line `CLAUDE.md`, a Skills library, an `AGENTS.md`, or a prompt library that's "too big for context." Stop pruning. Dhee handles delivery.
- **Anyone who wants their team to share context through git** — the same way they share code.

---

## Quick Start

**One command. No venv. No config. No pasting into `settings.json`.**

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

The installer creates `~/.dhee/`, installs the `dhee` package, and auto-wires Claude Code and Codex hooks. Open your agent in any project — cognition is on.

<details>
<summary><b>Other install paths</b></summary>

```bash
# Via pip
pip install dhee
dhee install                      # configure supported agent harnesses

# From source
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee && ./scripts/bootstrap_dev_env.sh
source .venv-dhee/bin/activate
dhee install
```

</details>

After install, Dhee auto-ingests project docs (`CLAUDE.md`, `AGENTS.md`, `SKILL.md`, etc.) on the first session. Run `dhee ingest` any time to re-chunk.

```bash
dhee install                  # configure local agent harnesses
dhee link /path/to/repo       # share context with teammates through this repo
dhee context refresh          # refresh repo context after pull/checkout
dhee handoff                  # compact continuity for current repo/session
dhee key set openai           # store a provider key locally (encrypted)
dhee router report            # token-savings stats + replay projection
dhee router tune              # re-tune retrieval policy from usage
```

---

## <span id="repo-shared-context">Repo-Shared Context — git is the sync layer</span>

Most "team memory" tools need a server. Dhee uses the one your team already trusts: **git**.

```bash
dhee link /path/to/repo
```

Dhee creates a tracked folder inside your repo:

```text
<repo>/.dhee/
  config.json
  context/manifest.json
  context/entries.jsonl
```

Commit it. Teammates who pull the repo and have Dhee installed get the **same shared context** — decisions, conventions, what-not-to-do — surfaced into their agent automatically.

Shared context is **append-only and git-friendly**. If two developers edit overlapping context concurrently, Dhee keeps both versions and reports a conflict instead of silently dropping one developer's work. The installed `pre-push` hook blocks unresolved conflicts from leaving the laptop:

```bash
dhee context check --repo /path/to/repo
```

**No hosted service. No org account. Your repo is the team brain.**

---

## Benchmarks

> **#1 on LongMemEval recall.** R@1 **94.8%**, R@5 **99.4%**, R@10 **99.8%** — full 500 questions, no held-out split, no cherry-picking.

| System | R@1 | R@3 | R@5 | R@10 |
|:-------|:----|:----|:----|:-----|
| **Dhee** | **94.8%** | **99.0%** | **99.4%** | **99.8%** |
| [MemPalace](https://github.com/MemPalace/mempalace#benchmarks) (raw) | — | — | 96.6% | — |
| [MemPalace](https://github.com/MemPalace/mempalace#benchmarks) (hybrid v4, held-out 450q) | — | — | 98.4% | — |
| [agentmemory](https://github.com/rohitg00/agentmemory#benchmarks) | — | — | 95.2% | 98.6% |

Stack: NVIDIA `llama-nemotron-embed-vl-1b-v2` embedder + `llama-3.2-nv-rerankqa-1b-v2` reranker, top-k 10.

**Proof is in-tree, not screenshots.** Exact command, metrics, and per-question output live under [`benchmarks/longmemeval/`](benchmarks/longmemeval/). Recompute R@k yourself — any mismatch is a bug you can open.

---

## How It Works

```
                  ┌──────────────────────────────┐
                  │   Your fat context             │
                  │   CLAUDE.md · AGENTS.md ·      │
                  │   SKILL.md · prompts · docs ·  │
                  │   sessions · tool output       │
                  └──────────────┬─────────────────┘
                                 │ ingest once
                                 ▼
       ┌────────────────────────────────────────────────────┐
       │             Dhee · local SQLite brain               │
       │                                                     │
       │  doc chunks · short-term · long-term · insights ·   │
       │  beliefs · policies · intentions · episodes · edits │
       └─────────────────────┬───────────────────────────────┘
                             │
              ┌──────────────┴───────────────┐
              ▼                              ▼
       Session start                    Each user prompt
       (full assembly)                  (matching slice only)
              │                              │
              └──────────────┬───────────────┘
                             ▼
              ┌────────────────────────────┐
              │  Token-budgeted XML         │
              │  <dhee v="1">               │
              │    <doc src="CLAUDE.md"…/>  │
              │    <i>What worked last…</i> │
              │  </dhee>                    │
              └────────────────────────────┘
                             │
                  Model sees only what it
                  needs, when it needs it.
```

On the tool-use side, the **router** digests raw output **at source** — never letting raw `Read`, `Bash`, or subagent results into context unless the model asks.

### The four-operation API

Every interface — hooks, MCP, Python, CLI — exposes the same four operations.

```python
from dhee import Dhee
d = Dhee()
d.remember("User prefers FastAPI over Flask")
d.recall("what framework does this project use?")
d.context("fixing the auth bug")
d.checkpoint("Fixed auth bug", what_worked="git blame first", outcome_score=1.0)
```

| Operation | LLM calls | Cost |
|:----------|:---------:|:----:|
| `remember` / `recall` / `context` | 0 | ~$0.0002 |
| `checkpoint` | 1 per ~10 memories | ~$0.001 |
| **Typical 20-turn Opus session** | **~1** | **~$0.004** |

Dhee overhead: ~$0.004/session. Token savings on the same 20-turn session: **~$0.50+**. **>100× ROI.**

### The router — digest at source

Four MCP tools replace `Read` / `Bash` / `Agent` on heavy calls:

- `dhee_read(file_path, offset?, limit?)` — symbols, head, tail, kind, token estimate + pointer.
- `dhee_bash(command)` — output digested by class (git log, pytest, grep, listing, generic).
- `dhee_agent(text)` — file refs, headings, bullets, error signals from any subagent return.
- `dhee_expand_result(ptr)` — only called when the digest genuinely isn't enough.

A 10 MB `git log --oneline -50000` becomes a ~200-token digest. This is where the serious savings live.

### Self-evolution — the part nobody else does

Most memory layers are static: you write rules, they retrieve. Dhee watches what happens and tunes itself.

- **Intent classification.** Every `Read`/`Bash`/`Agent` call is bucketed (source, test, config, doc, data, build). Each bucket gets its own retrieval depth.
- **Expansion ledger.** Every `dhee_expand_result(ptr)` is logged with `(tool, intent, depth)`.
- **Policy tuning.** `dhee router tune` reads the ledger and atomically rewrites `~/.dhee/router_policy.json` — deeper for what gets expanded, shallower for what doesn't.

Frontend-heavy teams get deeper JS/TS digests. Data teams get richer CSV/JSONL summaries. **You don't pick — Dhee picks, based on what you actually expand.**

---

## <span id="vs-alternatives">vs alternatives</span>

|  | **Dhee** | CLAUDE.md | Mem0 | Letta | MemPalace | agentmemory |
|:--|:-:|:-:|:-:|:-:|:-:|:-:|
| **Tokens / turn** | **~300** | 2,000+ | varies | ~1K+ | varies | ~1,900 |
| **LongMemEval R@5** | **99.4%** | — | — | — | 96.6% | 95.2% |
| **Self-evolving retrieval** | **Yes** | No | No | No | No | No |
| **Auto-digest tool output** | **Yes** | No | No | No | No | No |
| **Git-shared team context** | **Yes** | Manual | No | No | No | No |
| **Works across MCP agents** | **Yes** | No | Partial | No | Yes | Yes |
| **External DB required** | No (SQLite) | No | Qdrant/pgvector | Postgres+vector | No | No |
| **License** | MIT | — | Apache-2 | Apache-2 | MIT | MIT |

Dhee is the only one that **reduces tokens, leads on recall, self-evolves its retrieval policy, and shares team context through git.**

---

## Integrations

### Claude Code — native hooks

```bash
pip install dhee && dhee install
```

Six lifecycle hooks fire at the right moments. No SKILL.md, no plugin directory. The agent doesn't even know Dhee is there — it just gets better context.

### MCP server — Cursor, Codex, Gemini CLI, Cline, Goose, anything MCP

```json
{
  "mcpServers": {
    "dhee": { "command": "dhee-mcp" }
  }
}
```

### Python SDK / CLI / Docker

```bash
dhee remember "User prefers Python"
dhee recall  "programming language"
dhee ingest CLAUDE.md AGENTS.md
dhee checkpoint "Fixed auth" --what-worked "checked logs"
```

### Provider options

```bash
pip install dhee[openai,mcp]    # cheapest embeddings
pip install dhee[nvidia,mcp]    # current SOTA stack
pip install dhee[gemini,mcp]
pip install dhee[ollama,mcp]    # local, no API costs
```

---

## Public vs Enterprise

| | **Public Dhee** (this repo, MIT) | **Dhee Enterprise** (private) |
|:--|:--|:--|
| Local memory + router | ✅ | ✅ |
| Self-evolving retrieval | ✅ | ✅ |
| Git-shared repo context | ✅ | ✅ |
| Claude Code / Codex / MCP | ✅ | ✅ |
| Org / team management | — | ✅ |
| Repo Brain code-intelligence | — | ✅ |
| Owner dashboard, billing, licensing | — | ✅ |
| Sentry-derived security telemetry | — | ✅ |

Public Dhee is the developer brain — lightweight, trustworthy, and complete on its own. The commercial layer is closed-source and lives in `Sankhya-AI/dhee-enterprise`.

---

## FAQ

**What problem does Dhee solve?**
Large agent projects accumulate a fat `CLAUDE.md`, `AGENTS.md`, skills library, and tool output that get re-injected every turn. Dhee chunks, indexes, and decays that knowledge, and digests fat tool output at the source — so only the relevant ~300 tokens reach the model.

**How is Dhee different from Mem0, Letta, MemPalace, agentmemory?**
Dhee is the only memory layer that (a) leads [LongMemEval](https://github.com/xiaowu0162/LongMemEval) at R@5 99.4% on the full 500-question set, (b) self-evolves its retrieval policy per tool and per intent, (c) ships a **router** that digests `Read`/`Bash`/subagent output at source, and (d) shares team context through git instead of a server.

**Does Dhee work with Claude Code, Cursor, Codex, Gemini CLI, Aider?**
Yes. Native Claude Code hooks, an MCP server for every other host, plus a Python SDK and CLI. One install, every agent.

**How does the team-context sharing actually work?**
`dhee link /path/to/repo` writes a `.dhee/` directory inside your repo. Commit it. Teammates pull, install Dhee, and their agent surfaces the same shared decisions and conventions. Append-only with conflict detection — no overwrites, no server, no account.

**Is Dhee production-ready? What storage?**
SQLite by default. No Postgres, no Qdrant, no pgvector, no infra. 1000+ tests, reproducible benchmarks in-tree, MIT, works offline with Ollama or online with OpenAI / NVIDIA NIM / Gemini.

**Where are the benchmarks and can I reproduce them?**
[`benchmarks/longmemeval/`](benchmarks/longmemeval/) — full command, per-question JSONL, `metrics.json`. Clone, run, recompute R@k. Any mismatch is an issue you can open.

---

## Contributing

```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee && ./scripts/bootstrap_dev_env.sh
source .venv-dhee/bin/activate
pytest
```

---

<p align="center">
  <b>Your fat skills stay fat. Your token bill stays thin. Your agent gets smarter every session.</b>
  <br><br>
  <a href="https://github.com/Sankhya-AI/Dhee">GitHub</a> ·
  <a href="https://pypi.org/project/dhee">PyPI</a> ·
  <a href="https://github.com/Sankhya-AI/Dhee/issues">Issues</a> ·
  <a href="https://sankhyaailabs.com">Sankhya AI</a>
</p>

<p align="center">MIT License — built by Sankhya AI Labs.</p>

<p align="center"><sub>
<b>Topics:</b> ai-agents · agent-memory · llm-memory · developer-brain · claude-code · claude-code-hooks · claudemd · agentsmd · mcp · mcp-server · model-context-protocol · context-router · context-engineering · context-compression · token-optimization · llm-tools · vector-memory · sqlite · longmemeval · retrieval-augmented-generation · rag · mem0-alternative · letta-alternative · mempalace-alternative · cursor · codex · gemini-cli · aider · cline · goose
</sub></p>
