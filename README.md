<p align="center">
  <img src="https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/docs/dhee-hero.svg" alt="Dhee turns messy coding-agent context into a grounded action loop" width="100%">
</p>

<h1 align="center">Dhee</h1>

<p align="center">
  <b>The local developer brain for AI coding agents.</b><br>
  Persistent memory, repo cognition, handoff, routing, and proof for Codex, Claude Code, Cursor, Gemini CLI, Cline, and any MCP client.
</p>

<p align="center">
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/pypi/v/dhee?style=flat-square&color=orange" alt="PyPI"></a>
  <a href="https://pypi.org/project/dhee"><img src="https://img.shields.io/badge/python-3.9%2B-blue.svg?style=flat-square" alt="Python 3.9+"></a>
  <a href="https://github.com/Sankhya-AI/Dhee/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="MIT License"></a>
  <a href="benchmarks/longmemeval/"><img src="https://img.shields.io/badge/LongMemEval-R%405%2099.4%25-brightgreen.svg?style=flat-square" alt="LongMemEval R@5 99.4%"></a>
</p>

---

## What Dhee Is

Dhee is a production-ready, local-first context and memory layer for coding agents.

It is not a model, not an autocomplete tool, and not a hosted vector database. It sits beside your agent and keeps the work grounded:

- **Memory that does not rot into noise:** canonical facts, preferences, decisions, project rules, passive evidence, test fixtures, and operational events are separated.
- **Repo cognition that survives sessions:** symbol graph, imports, calls, route/component map, test map, ownership, historical failures, and impact analysis.
- **Context routing:** large files, grep output, logs, test runs, and agent handoffs become compact digests with evidence pointers.
- **Action contracts:** before edits, Dhee can compile the task into files, constraints, tests, risk, and proof obligations.
- **Handoff:** another agent can continue with the current repo state, decisions, blockers, and next action.

The promise is simple: **less prompt sludge, fewer repeated mistakes, better grounded code edits.**

## Install

```bash
pip install dhee
dhee install
```

Or use the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

Then wire a repo:

```bash
cd /path/to/repo
dhee init
dhee status
dhee ui
```

MCP clients can use:

```json
{
  "mcpServers": {
    "dhee": { "command": "dhee-mcp" }
  }
}
```

## How It Helps A Coding Agent

<p align="center">
  <img src="https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/docs/dhee-flow.svg" alt="Dhee coding agent flow" width="100%">
</p>

Without Dhee, every turn is a loose pile of files, logs, stale memory, and guesses.

With Dhee, the agent gets a compact working packet:

1. **Recall:** relevant user/project memory, current handoff, and repo facts.
2. **Understand:** repo brain localizes likely files, symbols, routes, tests, and owners.
3. **Act:** task contract controls allowed writes and risky paths.
4. **Verify:** tests, diffs, proof bundles, and contamination checks.
5. **Learn:** only durable lessons are promoted; junk stays suppressed.

## Deep Repo Cognition

<p align="center">
  <img src="https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/docs/dhee-impact.svg" alt="Dhee repo impact map" width="100%">
</p>

Ask: **"If I touch this file, what breaks?"**

Dhee's repo brain answers with grounded graph evidence:

- impacted files and symbols
- impacted routes and React components
- likely tests to run
- owners from git history
- related failure signatures
- source windows with line numbers, not raw file dumps

The repo brain is git-SHA scoped and persisted under `.dhee/context/repo_brain/`, so agents do not rebuild understanding from scratch every session.

## Memory Quality

Dhee separates memory into classes instead of letting everything compete:

| Memory kind | What happens |
| --- | --- |
| Canonical personal/project facts | Durable, high-confidence, slow decay |
| Passive screen/context observations | Raw evidence, not personal truth |
| Test fixtures and probes | Suppressed from normal recall |
| Operational events | Useful for diagnostics, not identity |
| Repo handoff/session state | Scoped to repo and current work |

This is what keeps a Chotu/Codex/Claude-style agent from sounding clever one minute and strangely blind the next.

## Useful Commands

```bash
dhee handoff --repo . --json
dhee context task create "fix flaky auth tests" --repo .
dhee context repo-brain index --repo .
dhee context repo-brain impact dhee/auth.py --repo .
dhee shell "cat /handoff/latest.md"
dhee memory-quality audit --user-id default --json
dhee release check --repo .
```

## Provider Defaults

Dhee can run model-free for repo tooling and handoff. For high-quality semantic memory, the default provider map points to the NVIDIA-compatible OpenAI API stack used in our LongMemEval runs:

```bash
dhee key set nvidia
pip install "dhee[nvidia,zvec,mcp]"
```

Current high-quality stack:

- Embedder: `nvidia/llama-nemotron-embed-vl-1b-v2`
- Reranker: `nvidia/llama-3.2-nv-rerankqa-1b-v2`
- Vector backend: `zvec` through `dhee-accel`

## Benchmarks

On LongMemEval full 500-question recall:

| System | R@1 | R@3 | R@5 | R@10 |
| --- | ---: | ---: | ---: | ---: |
| Dhee | 94.8% | 99.0% | 99.4% | 99.8% |

Reproduction notes and outputs live in [`benchmarks/longmemeval/`](benchmarks/longmemeval/).

## What Is In The Open Source Package

You get the local developer brain: memory OS, repo brain, DheeFS, MCP server, CLI, UI, runtime daemon, handoff bus, update capsules, and release/proof tooling.

Enterprise/team governance, hosted dashboards, org policy, and managed source connectors can build on top of these local primitives. The OSS package is useful by itself and does not require a hosted account.

## Develop

```bash
pip install -e ".[dev,nvidia,zvec,mcp]"
pytest
```

## License

MIT. Built by Sankhya AI Labs.
