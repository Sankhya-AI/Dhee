<p align="center">
  <img src="docs/dhee-logo.png" alt="Dhee" width="80">
</p>

<h1 align="center">Dhee — the context compiler for AI coding agents</h1>

<h3 align="center">Local context infrastructure that compiles working state, routes tool output, and shares audited learnings across Hermes, Claude Code, Codex, Cursor, Gemini CLI, Aider, Cline, and any MCP client.</h3>

<p align="center">
  Dhee is not another coding agent, IDE, or vector database. It is the control plane that decides what context reaches the next turn: goal, facts, decisions, plan, active files, tests, and pointer-backed evidence.
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
  <img src="docs/demo/demo.gif" alt="Dhee demo — fat skills, thin tokens, self-tuning retrieval" width="900">
</p>

<p align="center">
  <a href="#what-is-dhee">What is Dhee</a> ·
  <a href="#compiled-state">Compiled State</a> ·
  <a href="#shared-agent-learning">Shared Agent Learning</a> ·
  <a href="#dheefs">DheeFS</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#repo-shared-context">Repo-Shared Context</a> ·
  <a href="#benchmarks">Benchmarks</a> ·
  <a href="#how-it-works">How It Works</a> ·
  <a href="#vs-alternatives">vs Alternatives</a> ·
  <a href="#integrations">Integrations</a>
</p>

---

## What is Dhee?

**Dhee is the local context compiler for agentic development.** It runs on your machine, uses SQLite and local state files, plugs into Hermes, Claude Code, Codex, and any MCP client, and turns scattered transcripts, tool output, repo docs, memories, and subagent digests into a small auditable working set.

This is the category Dhee is built to own: **context governance for AI coding agents**. As models get cheaper and more capable, the scarce resource is not the prompt box; it is the quality, stability, provenance, and cost of the context that gets admitted into every agent turn. Dhee sits between agents and their workspace as the compiler for that context.

It does five jobs the model can't reliably do for itself:

1. **Maintains compiled state.** Every turn gets a small regenerated state card instead of the whole journey: current goal, canonical facts, active decisions, next action, active files, test status, and pointer-backed evidence.

2. **Remembers.** Doc chunks, decisions, what worked, what failed, user preferences. Decay and promotion keep stale knowledge out of the hot path while preserving what should transfer.

3. **Routes.** A 10 MB `git log` becomes a compact digest with a pointer. Raw output only re-enters context when the model explicitly expands it.

4. **Shares learnings.** Hermes memory, session traces, and agent-created skills flow into Dhee as auditable learning candidates. Only promoted learnings appear as Learned Playbooks for Claude Code, Codex, Hermes, and any Dhee-enabled agent.

5. **Self-tunes.** Dhee watches which digests the model expands and which retrieval depths are useful, then tunes router policy per tool, per intent, per file type. The goal is not a bigger prompt; it is a smaller, better working set.

### Who it's for

- **AI-native engineering teams** whose agents are now expensive, forgetful, repetitive, or hard to audit.
- **Claude Code / Cursor / Codex / Gemini CLI / Aider / Cline users** who have hit context limits, compaction loops, or runaway tool-output bills.
- **Teams standardizing on `AGENTS.md`, `CLAUDE.md`, Skills, MCP tools, and subagents** who need governed delivery instead of bigger prompts.
- **Hermes users** who already have a self-evolving agent and want those learnings to make Claude Code and Codex smarter too.
- **Founders building agentic development workflows** who need a local, inspectable context layer before they can trust agents with more of the work.

---

## <span id="compiled-state">Compiled State — the transcript is audit, state is truth</span>

Long coding sessions get expensive and less reliable when old tool output, repeated reads, failed attempts, and superseded plans keep influencing the next token. Dhee's answer is not to trim the transcript. Dhee keeps a canonical working state and regenerates a small state card for each turn.

```bash
dhee context status
dhee context state --card
dhee context provision "fix expired-token KeyError"
dhee context checkpoint --reason "before compaction"
dhee context rollover --reason "context debt crossed threshold"
```

The state card contains only current signal:

```xml
<dhee_state v="1" epoch="3" revision="42" debt="healthy">
  <goal>Fix expired-token KeyError in login</goal>
  <facts><f src="pytest">middleware.py line 47 raises KeyError iat</f></facts>
  <decisions><d id="D-...">Use python-jose validation path</d></decisions>
  <next>Patch middleware and run the narrow auth test.</next>
  <files><file>middleware.py</file></files>
  <evidence><ptr ptr="R-...">failing pytest digest</ptr></evidence>
</dhee_state>
```

Task pivots start a new epoch: stale facts, repeated reads, old plans, and superseded decisions are tombstoned instead of carried into the next state card. The raw evidence remains local behind pointers, and state writes are guarded so CLI, MCP, Codex sync, and Claude hooks do not trample each other.

Quality is the gate. Dhee suppresses duplicate and stale context only when the pointer store, expansion SLO, and outcome signals keep the next step safe. If expansion rises, Dhee deepens that digest class instead of hiding more evidence.

---

## <span id="shared-agent-learning">Shared Agent Learning — one promoted learning, every agent benefits</span>

Hermes can evolve its own skills and memories. Claude Code has native hooks. Codex has MCP config, `AGENTS.md`, and a persisted session stream. Dhee is the information layer underneath them: it turns separate agent histories into shared, gated context.

```text
Hermes MemoryProvider
  ├─ MEMORY.md / USER.md writes
  ├─ agent-created skills
  ├─ session summaries and outcomes
  └─ self-evolution traces
          │
          ▼
      Dhee Learning Exchange
          │
          ├─ candidate  -> review / evidence / score
          ├─ promoted   -> injected as Learned Playbooks
          └─ rejected   -> auditable, never injected
          │
          ▼
Claude Code · Codex · Hermes · any MCP client
```

What this means in practice:

- Your existing Hermes progress is not stranded inside Hermes. `dhee install` detects Hermes when present, installs Dhee as a Hermes `MemoryProvider` at `~/.hermes/plugins/memory/dhee`, and imports local Hermes memory files, session summaries, and agent-created skills into Dhee.
- Claude Code and Codex do not need to launch Hermes to benefit. They receive promoted Hermes/Dhee learnings through normal Dhee context and MCP tools.
- New Claude Code and Codex outcomes can become Dhee learning candidates too. After promotion, Hermes can read them back through the same provider.
- Candidate learnings are never auto-injected. Trusted Hermes `MEMORY.md` / `USER.md` imports may be promoted during install; Hermes `SOUL.md`, session traces, and agent-created skills stay candidates until explicitly approved or promoted by policy.

This is the product contract: **with Dhee, a learning proven in one agent can become a promoted playbook for every connected agent.**

### Reality check

- **Hermes native:** Dhee integrates as a Hermes `MemoryProvider`, the first-class Hermes memory-plugin surface. Hermes allows one active external memory provider, so V1 replaces Honcho/Mem0/etc. while `memory.provider: dhee` is active.
- **Claude Code native:** Dhee uses Claude Code hooks, MCP, and router enforcement. This is the strongest integration surface.
- **Codex native:** Codex does not expose Claude-style pre-tool hooks here. Dhee uses the closest native Codex surfaces: `~/.codex/config.toml`, global `~/.codex/AGENTS.md`, MCP server instructions, and Codex session-stream auto-sync.
- **Promotion gate:** Imported Hermes skills and session traces are candidates by default. Rejected or archived learnings remain auditable but are excluded from retrieval.
- **Continuity hygiene:** Handoffs filter fixture memories, artifact chunks, and placeholder test rows by default. Shared tool results carry provenance, salience, TTL, and evidence pointers so another agent can inherit the useful state without inheriting every live mirror.

---

## <span id="dheefs">DheeFS — one local learning space every agent already understands</span>

Agents already understand files and shell verbs. DheeFS exposes Dhee's memory, router, handoff, artifacts, shared tasks, and learning exchange as one virtual context space:

```bash
dhee shell "ls /learnings"
dhee shell "cat /handoff/latest.md"
dhee shell "grep parser /learnings/promoted"
dhee shell "cat /router/ptr/R-abc123"
```

The first version is a virtual shell, not FUSE. It intentionally supports a small approved command set: `ls`, `cat`, `grep`, `why`, `promote`, `reject`, `broadcast`, `provision`, and `snapshot`. The same surface is available through MCP as `dhee_shell(command)` and through Python:

```python
from dhee import ContextWorkspace

result = ContextWorkspace(repo=".").execute("provision 'fix parser bug'")
print(result.stdout)
```

External systems such as Slack, Gmail, and Notion are future **context sources** under `/sources`, not generic remote action backends. They can sync and search evidence into Dhee artifacts, learnings, and handoffs without making the core install depend on SaaS SDKs.

```text
/learnings   candidates, promoted, rejected, archived
/state       current compiled state, state card, decisions, epoch history
/context     debt, status, checkpoints, rollover evidence
/handoff     latest repo/session continuity
/router/ptr  raw pointer lookup when explicitly requested
/artifacts   host-parsed files and chunks
/repo        .dhee/context decisions and conventions
/agents      Hermes, Claude Code, Codex views
/shared      inbox, broadcasts, shared task results
/sources     optional future Slack/Gmail/Notion context mounts
```

---

## Quick Start

**One command. No venv. No config. No pasting into `settings.json`.**

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

The installer creates `~/.dhee/`, installs the `dhee` package, and auto-wires Claude Code, Codex, and Hermes when detected. Open your agent in any project — cognition is on.

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
dhee hermes status            # see whether Hermes is detected and Dhee-backed
dhee hermes sync --dry-run    # preview Hermes memories/skills before import
dhee learn search --include-candidates  # inspect candidates and promotions
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

- `dhee_read(file_path, offset?, limit?, query?, task_intent?)` — symbols, focus slices, head/tail, kind, token estimate + pointer. When no query is passed, Dhee infers one from compiled state.
- `dhee_bash(command, preview_only?)` — preflight risk, output class, stderr/stdout landmarks, and command-specific reducers for git diffs, pytest/build failures, grep, listings, and generic logs.
- `dhee_agent(text)` — file refs, headings, bullets, error signals from any subagent return.
- `dhee_expand_result(ptr, range?, symbol?, reason?, expected?)` — only called when the digest genuinely isn't enough; expansion reasons feed router tuning.

A 10 MB `git log --oneline -50000` becomes a ~200-token digest. This is where the serious savings live.

### Self-tuning retrieval

Most memory layers are static: you write rules, they retrieve. Dhee watches what happens and tunes itself.

- **Intent classification.** Every `Read`/`Bash`/`Agent` call is bucketed (source, test, config, doc, data, build). Reads also inherit the live compiled-state task intent, so a debug session gets failure landmarks without the agent remembering to pass a query.
- **Stable duplicate suppression.** Admission hashes the underlying evidence, not the fresh pointer string, so unchanged repeated reads stop adding debt.
- **Expansion ledger.** Every `dhee_expand_result(ptr)` is logged with `(tool, intent, depth, slice mode, reason, expected signal)`.
- **Policy tuning.** `dhee router tune` reads the ledger and atomically rewrites `~/.dhee/router_policy.json` — deeper for what gets expanded, shallower for what doesn't.

Frontend-heavy teams get deeper JS/TS digests. Data teams get richer CSV/JSONL summaries. **You don't pick — Dhee picks, based on what you actually expand.**

---

## <span id="vs-alternatives">vs alternatives</span>

|  | **Dhee** | CLAUDE.md | Mem0 | Letta | MemPalace | agentmemory |
|:--|:-:|:-:|:-:|:-:|:-:|:-:|
| **Tokens / turn** | **~300** | 2,000+ | varies | ~1K+ | varies | ~1,900 |
| **LongMemEval R@5** | **99.4%** | — | — | — | 96.6% | 95.2% |
| **Self-tuning retrieval** | **Yes** | No | No | No | No | No |
| **Hermes → Claude/Codex learning exchange** | **Yes** | No | No | No | No | No |
| **Auto-digest tool output** | **Yes** | No | No | No | No | No |
| **Git-shared team context** | **Yes** | Manual | No | No | No | No |
| **Works across MCP agents** | **Yes** | No | Partial | No | Yes | Yes |
| **External DB required** | No (SQLite) | No | Qdrant/pgvector | Postgres+vector | No | No |
| **License** | MIT | — | Apache-2 | Apache-2 | MIT | MIT |

Dhee is not trying to be the agent, the IDE, or the memory SaaS. It is the **context governance layer** those systems need underneath them: token reduction, reproducible recall, self-tuning retrieval policy, git-shared team context, and promoted cross-agent learning in one local-first control plane.

---

## Integrations

### Hermes Agent — native MemoryProvider

```bash
dhee install                  # detects Hermes and enables Dhee when present
dhee hermes status
dhee hermes sync --dry-run
```

Dhee installs as the Hermes memory provider, mirrors Hermes memory writes, imports local Hermes memory files, and checkpoints Hermes sessions into Dhee learning candidates. Curated `MEMORY.md` / `USER.md` imports can be promoted on install; `SOUL.md`, session traces, and agent-created skills stay gated. Promoted playbooks flow back into Hermes through the provider and out to Claude Code/Codex through Dhee context.

### Claude Code — native hooks

```bash
pip install dhee && dhee install
```

Six lifecycle hooks fire at the right moments. Claude Code gets Dhee handoff, shared tasks, inbox broadcasts, learned playbooks, and router enforcement for heavy `Read`/`Bash`/`Grep` calls.

### Codex — closest native surface

```bash
pip install dhee && dhee install --harness codex
dhee harness status --harness codex
```

Dhee writes `~/.codex/config.toml`, manages a global `~/.codex/AGENTS.md` block, advertises context-first MCP instructions, and tails Codex session logs on Dhee calls. Codex does not currently expose Claude-style pre-tool hooks, so this is the strongest truthful native integration available.

### MCP server — Cursor, Gemini CLI, Cline, Goose, anything MCP

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
| Self-tuning retrieval | ✅ | ✅ |
| Hermes → Claude Code/Codex learning exchange | ✅ | ✅ |
| Git-shared repo context | ✅ | ✅ |
| Claude Code / Codex / MCP | ✅ | ✅ |
| Org / team management | — | ✅ |
| Repo Brain code-intelligence | — | ✅ |
| Owner dashboard, billing, licensing | — | ✅ |
| Sentry-derived security telemetry | — | ✅ |

Public Dhee is the local collaboration layer — lightweight, trustworthy, and complete on its own. The commercial layer is closed-source and lives in `Sankhya-AI/dhee-enterprise`.

---

## FAQ

**What problem does Dhee solve?**
Large agent projects accumulate a fat `CLAUDE.md`, `AGENTS.md`, skills library, and tool output that get re-injected every turn. Dhee chunks, indexes, and decays that knowledge, and digests fat tool output at the source — so only the relevant ~300 tokens reach the model.

**How is Dhee different from Mem0, Letta, MemPalace, agentmemory?**
Dhee is built around four pieces most tools treat separately: reproducible LongMemEval results, a self-tuning retrieval/router policy, source-side digests for heavy `Read`/`Bash`/subagent output, and git-shared team context instead of a server.

**Does Dhee work with Claude Code, Cursor, Codex, Gemini CLI, Aider?**
Yes. Native Claude Code hooks, closest-native Codex config/AGENTS/session-stream sync, a Hermes MemoryProvider, an MCP server for every other host, plus a Python SDK and CLI. One install, every agent.

**Does Hermes make Claude Code and Codex smarter?**
Yes, through Dhee's learning exchange after promotion. Dhee can install as Hermes' memory provider, import Hermes memory/session/skill artifacts, and expose promoted learnings to Claude Code, Codex, and any MCP client as Learned Playbooks. Claude/Codex do not have to run Hermes to benefit.

**Does Claude Code or Codex evolve Hermes back?**
Yes, after promotion. Claude Code hooks, Codex session-stream sync, MCP memory tools, and learning submissions create Dhee learning candidates. Promoted personal/repo/workspace playbooks are retrieved by Hermes through the Dhee provider.

**How does the team-context sharing actually work?**
`dhee link /path/to/repo` writes a `.dhee/` directory inside your repo. Commit it. Teammates pull, install Dhee, and their agent surfaces the same shared decisions and conventions. Append-only with conflict detection — no overwrites, no server, no account.

**Is Dhee production-ready? What storage?**
SQLite by default. No Postgres, no Qdrant, no pgvector, no infra. The regression suite and reproducible benchmarks live in-tree. MIT, works offline with Ollama or online with OpenAI / NVIDIA NIM / Gemini.

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

For the same full-suite path CI expects, including the local Rust acceleration
extension and async test plugin:

```bash
./scripts/verify_full_suite.sh
```

---

<p align="center">
  <b>Your fat skills stay fat. Your token bill stays thin. Promoted learnings travel with every agent.</b>
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
