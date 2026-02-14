<h1 align="center">
  <br>
  <img src="https://img.shields.io/badge/engram-PMK-black?style=for-the-badge" alt="Engram" height="32">
  <br>
  Engram
  <br>
</h1>

<h3 align="center">
  The Personal Memory Kernel for AI Agents
</h3>

<p align="center">
  Hit a rate limit in Claude Code? Open Codex — it already knows what you were doing.<br>
  One memory kernel. Shared across every agent. Bio-inspired forgetting. Zero cold starts.
</p>

<p align="center">
  <a href="https://pypi.org/project/engram-memory"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg" alt="Python 3.9+"></a>
  <a href="https://github.com/Ashish-dwi99/Engram/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
  <a href="https://github.com/Ashish-dwi99/Engram/actions"><img src="https://github.com/Ashish-dwi99/Engram/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/Ashish-dwi99/Engram"><img src="https://img.shields.io/github/stars/Ashish-dwi99/Engram?style=social" alt="GitHub Stars"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &middot;
  <a href="#why-engram">Why Engram</a> &middot;
  <a href="#how-it-works">How It Works</a> &middot;
  <a href="#packages">Packages</a> &middot;
  <a href="https://github.com/Ashish-dwi99/Engram/blob/main/CHANGELOG.md">Changelog</a> &middot;
  <a href="https://github.com/Ashish-dwi99/Engram/wiki">Docs</a>
</p>

---

### Research Highlights

<p align="center">
  <b>~45% less storage</b> &nbsp;&nbsp;|&nbsp;&nbsp; <b>+26% retrieval accuracy</b> &nbsp;&nbsp;|&nbsp;&nbsp; <b>+12% multi-hop reasoning</b>
</p>

<p align="center">
  Based on <a href="https://arxiv.org/abs/2601.18642"><b>FadeMem</b> (arXiv:2601.18642)</a> — biologically-inspired forgetting for efficient agent memory.
</p>

---

## Quick Start

```bash
pip install engram-memory          # 1. Install
export GEMINI_API_KEY="your-key"   # 2. Set one key (or OPENAI_API_KEY, NVIDIA_API_KEY)
engram install                     # 3. Auto-configure Claude Code, Cursor, Codex
```

Restart your agent. Done — it now has persistent memory across sessions and agents.

---

## Why Engram

Every AI agent you use starts with amnesia. But the real pain isn't just forgetting — it's what happens when you **switch agents**.

You're 40 minutes into a refactor with Claude Code. You've touched six files, picked a migration strategy, mapped out the remaining TODOs. Then you hit a rate limit. Or your terminal crashes. Or you just need Codex for the next part. So you switch — and the new agent has **zero context**. You re-paste file paths, re-explain decisions, re-describe the plan. Half the time the new agent contradicts something you'd already decided.

**Engram fixes this.** It's a Personal Memory Kernel — one memory store shared across all your agents. When Claude Code pauses, it saves a session digest. When Codex picks up, it loads that digest and continues where you left off. No re-explanation. No cold starts.

But Engram isn't just a handoff bus. It models memory the way brains do:

| Problem | Typical approach | Engram |
|:--------|:-----------------|:-------|
| **Switch agents = cold start** | Manual copy-paste | Handoff bus — auto session digests + resume |
| **Nobody forgets** | Store everything forever | Ebbinghaus decay — ~45% less storage |
| **Single retrieval path** | One embedding per memory | 5 retrieval paths per memory (EchoMem) |
| **No episodic memory** | Vector search only | CAST scenes — time/place/topic clustering |
| **No consolidation** | Store everything as-is | CLS sleep cycles — episodic to semantic distillation |
| **Single decay rate** | One exponential curve | Multi-trace Benna-Fusi model (fast/mid/slow) |
| **No real-time coordination** | Polling or nothing | Active memory signal bus — agents see each other instantly |
| **Concurrent access** | Single-process locks | sqlite-vec WAL — multiple agents, one DB |

---

## How It Works

Engram has two distinct memory systems — like the brain's conscious and subconscious:

**Active Memory** — a real-time signal bus. Agents post ephemeral state ("editing auth.py", "build failing") that other agents see instantly. Signals auto-expire. Important ones get consolidated into long-term storage.

**Passive Memory** — the long-term store. Memories fade via Ebbinghaus decay, get promoted from short-term to long-term through repeated access, and are encoded through multiple retrieval paths (paraphrase, keywords, implications, question-form). Sleep cycles distill episodic conversations into durable semantic facts.

**Handoff** — when an agent pauses (rate limit, crash, tool switch), it saves a session digest: task summary, decisions made, files touched, TODOs remaining. The next agent loads it and continues. If no digest was saved, Engram falls back to parsing the conversation logs automatically.

<details>
<summary><b>The memory stack at a glance</b></summary>

| Layer | What it does |
|:------|:-------------|
| **FadeMem** | Ebbinghaus-curve decay, SML/LML dual layers, promotion on access |
| **EchoMem** | 5 retrieval paths per memory (paraphrase, keywords, implications, Q-form) |
| **CategoryMem** | Auto-discovered hierarchical categories with retrieval boosting |
| **CAST Scenes** | Episodic narrative memory — time, place, topic clustering |
| **CLS Distillation** | Sleep-cycle replay: episodic to semantic fact extraction |
| **Multi-trace** | Benna-Fusi model — fast/mid/slow decay traces per memory |
| **Intent routing** | Episodic vs semantic query classification |
| **Handoff bus** | Session digests, checkpoints, JSONL log fallback |
| **Active Memory** | Real-time signal bus with TTL tiers |
</details>

---

## Packages

Engram is three pip-installable packages:

```
engram-memory ← engram-bus ← engram-enterprise
   (core)        (comms)       (governance)
```

### [`engram-memory`](./engram/) — Core Memory Engine

The main package. Memory CRUD, semantic search, decay, echo encoding, categories, episodic scenes, MCP server, CLI.

```bash
pip install engram-memory
pip install "engram-memory[openai]"     # OpenAI provider
pip install "engram-memory[ollama]"     # Ollama (local, no key needed)
pip install "engram-memory[all]"        # everything
```

```python
from engram import Engram

memory = Engram()
memory.add("User prefers Python over TypeScript", user_id="u1")
results = memory.search("programming preferences", user_id="u1")
```

**18 MCP tools** — memory CRUD, semantic search, episodic scenes, profiles, decay, session handoff. One command configures Claude Code, Cursor, and Codex:

```bash
engram install
```

### [`engram-bus`](./engram-bus/) — Agent Communication Bus

Real-time agent-to-agent coordination. Key/value with TTL, pub/sub messaging, handoff sessions and checkpoints. Zero external dependencies — stdlib only.

```bash
pip install engram-bus
```

```python
from engram_bus import Bus

bus = Bus()
bus.put("status", "refactoring auth", agent="planner", ttl=300)
bus.publish("progress", {"step": 3, "total": 5}, agent="worker")
```

[Full documentation →](./engram-bus/README.md)

### [`engram-enterprise`](./engram-enterprise/) — Governance Layer

Policy enforcement, provenance tracking, acceptance gates, async operations, and authenticated REST API. Built on top of engram-memory and engram-bus.

```bash
pip install engram-enterprise
```

```python
from engram_enterprise import PersonalMemoryKernel

kernel = PersonalMemoryKernel()
```

[Full documentation →](./engram-enterprise/README.md)

---

## Integrations

```bash
engram install    # auto-configures everything
```

One command sets up MCP config for Claude Code, Cursor, and Codex. It also deploys the **Claude Code plugin** — a hook that proactively injects relevant memories before every prompt, plus periodic background checkpoints that survive rate limits.

Works with any tool-calling agent via REST: `engram-api` starts a server at `http://127.0.0.1:8100`.

---

## Repo Structure

```
├── engram/                  # engram-memory — core Python package
│   ├── core/                #   decay, echo, category, scenes, distillation, traces
│   ├── memory/              #   Memory class (orchestrates all layers)
│   ├── llms/                #   LLM providers (gemini, openai, nvidia, ollama)
│   ├── embeddings/          #   embedding providers
│   ├── vector_stores/       #   sqlite-vec, in-memory
│   ├── db/                  #   SQLite persistence
│   ├── api/                 #   REST API endpoints
│   ├── mcp_server.py        #   MCP server (18 tools)
│   └── cli.py               #   CLI interface
├── engram-bus/              # engram-bus — agent communication
│   └── engram_bus/          #   bus, pub/sub, handoff store, TCP server
├── engram-enterprise/       # engram-enterprise — governance layer
│   └── engram_enterprise/   #   kernel, policy, provenance, async, API + auth
├── plugins/                 # Claude Code plugin (hooks, commands, skill)
├── dashboard/               # Next.js memory visualizer
├── tests/                   # Test suite (300+ tests)
├── pyproject.toml           # engram-memory package config
└── install.sh               # One-line installer
```

---

## Contributing

```bash
git clone https://github.com/Ashish-dwi99/Engram.git
cd Engram

# Core
pip install -e ".[dev]"
pytest

# Bus
pip install -e "./engram-bus[dev]"
cd engram-bus && pytest

# Enterprise
pip install -e "./engram-enterprise[dev]"
cd engram-enterprise && pytest
```

---

## License

MIT — see [LICENSE](LICENSE).

---

<p align="center">
  <b>One memory. Every agent. Zero cold starts.</b>
  <br><br>
  <a href="https://github.com/Ashish-dwi99/Engram">GitHub</a> &middot;
  <a href="https://github.com/Ashish-dwi99/Engram/issues">Issues</a> &middot;
  <a href="https://github.com/Ashish-dwi99/Engram/blob/main/CHANGELOG.md">Changelog</a> &middot;
  <a href="https://github.com/Ashish-dwi99/Engram/wiki">Docs</a>
</p>
