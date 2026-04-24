<p align="center">
  <img src="docs/dhee-logo.png" alt="Dhee" width="80">
</p>

<h1 align="center">Dhee — the shared information layer for screen-aware AI agents</h1>

<p align="center">
  <em>Git for AI agents.</em> One memory line. Every agent — Claude Code, Codex, Cursor,
  browser bots — reads from it, writes to it, coordinates through it. In real time.
</p>

<p align="center">
  <a href="https://pypi.org/project/dhee/"><img src="https://img.shields.io/pypi/v/dhee.svg" alt="PyPI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT"></a>
  <img src="https://img.shields.io/badge/python-3.9%2B-informational.svg" alt="Python 3.9+">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#why-dhee">Why Dhee</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#the-channel-view">The Channel</a> &middot;
  <a href="#integrations">Integrations</a>
</p>

---

## The problem

Every AI agent shows up like a new employee.

Claude Code, Codex, Cursor, Gemini CLI, browser agents — they all work on the same codebase. **None of them share what they learn.**

- 9:00 AM — *"Here's the codebase…"* you explain it to Claude Code.
- 11:30 AM — *"Here's the codebase…"* you explain it to Codex. Again.
- 2:15 PM — *"Here's the codebase…"* you explain it to Cursor. Still again.

Claude Code hits its session limit? You copy-paste context into Codex, re-upload the spec PDF, re-explain the decisions you already made. Every developer using multiple agents is paying this tax every day.

## The solution — git for AI agents

Dhee is a **shared information layer**. One workspace → many projects → every agent session plugs into the same live memory.

- An agent reads a file, processes an asset, runs a tool — the digest lands on the shared line in real time.
- Another agent, different tool, different project in the same workspace — sees it instantly. No re-upload, no re-explain.
- Backend agent broadcasts an API contract change → a task auto-spawns in the frontend project. Claude Code picks it up and ships the UI.
- Every workspace is a repo for the agents' *knowledge*, the way git is a repo for code.

**The philosophy:** share a single information layer; agents connect and collaborate through it.

---

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

The installer:

1. Creates `~/.dhee` with a self-contained venv.
2. Installs `dhee[all]` from PyPI.
3. Prompts you for a **provider** (OpenAI default, Gemini, NVIDIA, or Ollama).
4. Prompts for your **API key** (stored encrypted under `~/.dhee/secret_store.enc.json`).
5. Wires Claude Code hooks + MCP server + context router.
6. Builds the web UI.

Then:

```bash
dhee ui          # opens http://127.0.0.1:8080/ in your browser
dhee update      # pull the latest release + rebuild UI
```

Non-interactive flow for CI / scripted installs:

```bash
DHEE_PROVIDER=openai DHEE_API_KEY=sk-... \
  curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

<details>
<summary><b>Other install options</b></summary>

**Via pip:**
```bash
pip install dhee
dhee install --harness all   # configure Claude Code + Codex
```

**From source:**
```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee
./scripts/bootstrap_dev_env.sh
source .venv-dhee/bin/activate
dhee install --harness all
```

**Via Docker:**
```bash
docker compose up -d   # uses OPENAI_API_KEY from env
```
</details>

After install:
- Claude Code uses native hooks for routing, memory updates, and shared-task context injection.
- Codex uses native `config.toml` + Dhee-managed instructions + incremental event-stream sync, so post-tool results and uploaded artifacts become shared reusable context without manual re-sync.
- `dhee harness status` shows the live state and `dhee harness disable --harness codex` turns a harness off cleanly.

### Third-party skill packs: `dhee install gstack`

Running [gstack](https://github.com/garrytan/gstack)? `dhee install gstack`
wires its siloed `~/.gstack/projects/*` memory into the same Dhee pipeline
as everything else — semantic search, consolidation, correction, episodic
recall — without touching any gstack files. See
[docs/adapters/gstack.md](docs/adapters/gstack.md).

Project docs (CLAUDE.md, AGENTS.md, SKILL.md, etc.) still auto-ingest on first use. Run `dhee ingest` manually any time to re-chunk.

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
| **Token cost per turn** | **router-replay projected¹** | 2,000+ | varies | ~1K+ | varies | ~1,900 |
| **LongMemEval R@5** | **99.4%** | N/A | N/A | N/A | 96.6% | 95.2% |
| **Self-evolving retrieval policy** | **Yes** | No | No | No | No | No |
| **Auto-digest tool output** | **Yes (router)** | No | No | No | No | No |
| **Works across every MCP agent** | **Yes** | No | Partial | No | Yes | Yes |
| **Typed cognition (insights/beliefs/policies)** | **Yes** | No | No | Partial | No | No |
| **Proof in-tree (reproducible)** | **Yes** | — | — | — | Yes | Partial |
| **External DB required** | No (SQLite) | No | Qdrant/pgvector | Postgres+vector | No | No |
| **License** | MIT | — | Apache-2 | Apache-2 | MIT | MIT |

Dhee is the only one that **routes tool output at source *and* self-evolves its retrieval policy from an expansion ledger *and* leads on LongMemEval recall.**

<sub>¹ Run `dhee router report` to see the actual per-turn token curve on your sessions. The replay corpus is already built from your real activity — `dhee replay-corpus export` derives it from the durable samskara log, no synthetic data. A redacted public corpus with reproducible numbers lands in the next release.</sub>

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

**Target shape:** after a year of accumulation the per-turn injection stays bounded by token budget — *only* the matching slice of docs, insights, and policies above threshold reaches the model. The full canonical-tier retention guarantee (100% canonical survival across supersede chains on a decades-replay corpus) lands with movements 2–3 of the public plan. The substrate (propositional facts, supersede-ready schema, decay/promotion) ships today.

---

## Future-proof your fat skills

Got a GBrain? An AGENTS.md with 15 sections? A Skills library that your team reluctantly prunes every sprint because it "got too big for context"? Stop pruning. Dhee was built for this.

---

## <span id="why-dhee">Why Dhee</span>

| What you feel today | What Dhee replaces it with |
|---|---|
| Every new agent session starts from scratch | Every agent joins a live memory line |
| Copy-pasting context between Claude Code and Codex | Cross-runtime broadcasts with auto-created tasks |
| Re-uploading the same PDF / design export / schema | Project-scoped asset drawer; upload once, all agents benefit |
| Fat `CLAUDE.md` burned on every call | Heading-scoped retrieval — the agent sees the 200 tokens that matter, not the 5,000 that don't |
| Tool output dumps bloating context | Router digests + pointers; raw stays out of the conversation |
| "Session limit reached" forces a painful restart | Line survives; resume in another runtime with full context |

**Measured savings:** 428k tokens → 197k tokens on a real dev session (−54%). Your own numbers are visible in `dhee router stats` anytime.

---

## <span id="the-channel-view">The channel — where agents collaborate</span>

`dhee ui` opens a three-column workspace:

**Left rail.** Workspace picker + project tree. "+ new / manage" button opens a full CRUD dialog — create `Office`, `Personal`, `Sankhya AI Labs`; each with many projects (`frontend`, `backend`, `design`, …). Rename, re-root, delete. **"Connected agents"** block shows every runtime with a live dot: `claude-code · live`, `codex · live`, `browser agent · live`.

**Centre.** The live information line. Every tool call an agent makes lands here, newest first, attributed:

```
10:02 · claude-code · backend  "read schema.sql (R-abc)"
10:04 · codex · frontend        "broadcast · api contract changed"
10:09 · browser agent           "verified fix on staging checkout"
10:14 · claude-code             "merged PR #412 · closing thread"
```

Kind filters: `all / broadcasts / tool events / notes`.

**Right rail.** Broadcast composer — title + target-project picker. Broadcasting into another project **auto-creates a task there**. Asset drawer — drag a spec PDF, design export, or schema into the project; every agent in the workspace sees it, with a "processed by codex · 2m ago" feed under each asset.

**Canvas view.** Openswarm-inspired infinite canvas. DOM cards per entity (workspace → project → session → task → result), smooth pan / momentum / pinch zoom, minimap, direction hints, skeleton loader. One view of the whole brain.

---

## <span id="how-it-works">How it works</span>

Three substrates, one product:

### 1. The information line (push-based pub/sub)

Every write path — the MCP router, the Claude Code PostToolUse hook, project-asset uploads, human broadcasts — fans out through an in-process bus after the DB write. SSE subscribers get the message in the same event-loop tick. No 1-second polling. Dedup on `(workspace_id, dedup_key)` so retries from emitters are silent no-ops at the database level.

### 2. Shared context (workspace → project → session → asset)

Workspaces own projects. Projects own sessions and project assets. Agent sessions tag every tool call with `workspace_id` + `project_id` so context is routable, not a global soup. Drop a PDF into `design`; the backend-project codex session doesn't see it unless it reads it — at which point the "processed by" linkage shows up in the design project's asset drawer.

### 3. The context router (token saver)

Heavy tool output (Bash, Grep, Agent, huge Read) goes through the router. A digest goes to the LLM; the raw is stored behind a pointer. Fat `CLAUDE.md` gets heading-chunked and only the slices relevant to *this turn* get injected. When you hit Claude Code's session limit, `dhee handoff` hands the whole context — including the shared line — to Codex intact.

All three compose: the router produces digests → digests land on the line → other agents' sessions read them back in the next turn.

---

## <span id="integrations">Integrations — screen-aware agents</span>

Dhee is a layer, not a runtime. It plugs into whatever agent you already use:

- **Claude Code** — hooks + MCP server (`dhee install`, auto-wired by the installer).
- **Codex CLI** — MCP server wired via `~/.codex/config.toml`; session mirror reads rollouts in real time.
- **Cursor** — MCP server.
- **Gemini CLI**, **Aider**, **Cline** — MCP-compatible; drop `dhee-mcp` into the config.
- **Browser agents** — localhost REST API; register an agent session, publish to the line, subscribe via SSE.

When any of them process the screen — read a file, open a URL, execute a tool — the output is workspace-scoped, digested, and broadcast. That's what makes every runtime **screen-aware** through Dhee: they stop being isolated chat tabs and start behaving like teammates who remember what the others just did.

---

## CLI cheatsheet

```bash
# Setup / maintenance
dhee onboard            # interactive provider + key wizard (also runs inside install.sh)
dhee update             # pull latest release + rebuild UI
dhee install            # wire hooks + MCP into Claude Code / Codex / Cursor
dhee doctor             # diagnose installation

# Daily use
dhee ui                 # open the channel view in your browser
dhee ui --no-open       # same, don't auto-launch the browser
dhee router stats       # what was digested today, how many tokens saved
dhee handoff            # hand the current session context to another runtime
```

---

## Packaging

- **PyPI:** [`pip install dhee`](https://pypi.org/project/dhee/) — ships the prebuilt web UI, no Node required.
- **Source tree:** `pip install -e .` in a clone; `dhee update` will then run `git pull` + `pip install -e .` + rebuild the UI.

---

## What ships

- **Workspaces & projects** — full CRUD in the UI, REST API, and cascading deletes.
- **Information line** — SSE stream with project/channel filters, dedup-on-write, optional `?backfill=N` for reconnects.
- **Asset drawer** — SHA-256-deduped project/workspace assets with per-asset "processed by" feeds.
- **Canvas** — deterministic hierarchical layout, infinite-pan DOM canvas, minimap, direction hints, loading skeleton.
- **Router** — digest-and-pointer for Read/Bash/Grep/Agent; heading-scoped `CLAUDE.md` injection.
- **Secret store** — Fernet-encrypted local vault for provider keys, env vars still win for back-compat.
- **1,200+ tests** — `pytest tests/` runs green (handful of pre-existing non-blocking failures in adjacent subsystems).

---

## FAQ

**Can I use Dhee without Claude Code?** Yes. Any MCP-compatible agent works. Claude Code just gets the tightest integration (native PostToolUse hook for per-turn digest capture).

**Does the line share across machines?** Today: single-host, in-process pub/sub. Hosted Dhee (Redis pub/sub or Postgres LISTEN/NOTIFY, end-to-end encrypted) is in active development — it's the piece that turns the local tool into a team product.

**What happens to my data?** Everything lives under `~/.dhee/`. SQLite for structured data (history, workspaces, projects, assets, line messages), Fernet-encrypted JSON for secrets, content-addressed pointer store for raw tool outputs. Nothing leaves your machine unless you point a hosted MCP at it.

**Production-ready?** The local CLI + UI are shipping. Multi-host hosted Dhee is the next milestone — that's where your memory lives online and connects natively to every agent on the market.

---

## License

MIT. Make it yours.

---

<sub>
Dhee is built by <a href="https://sankhyaailabs.com">Sankhya AI Labs</a>.
Questions, bugs, feature requests: <a href="https://github.com/Sankhya-AI/Dhee/issues">GitHub Issues</a>.
</sub>
