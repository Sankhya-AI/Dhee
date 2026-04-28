# Dhee Developer Brain

Dhee is a local memory layer for AI coding agents.

It gives Claude Code, Codex, and MCP-compatible tools a durable developer brain:
personal memory, repo-shared context, session handoff, and git-backed team
knowledge without requiring a hosted service.

The public Dhee project stays simple:

- one-command install
- local encrypted key storage
- automatic Claude Code/Codex harness setup
- folder and git-repo context linking
- repo-shared context through `.dhee/context/`
- MCP tools for memory, handoff, and shared context

The enterprise dashboard, org controls, code-intelligence Repo Brain,
commercial licensing, Sentry telemetry, and paid team workflows live in the
private `dhee-enterprise` repository.

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh
```

Then restart your agent session.

Useful commands:

```bash
dhee install                  # configure supported local agent harnesses
dhee link /path/to/repo       # share context through this git repo
dhee links                    # list linked repos
dhee context refresh          # refresh repo context after pull/checkout
dhee context check            # detect unresolved shared-context conflicts
dhee handoff                  # compact continuity for the current repo/session
dhee key set openai           # store a provider key locally
dhee update                   # update the local install
```

## Repo-Shared Context

When you run:

```bash
dhee link /path/to/repo
```

Dhee creates:

```text
<repo>/.dhee/config.json
<repo>/.dhee/context/manifest.json
<repo>/.dhee/context/entries.jsonl
```

Those files can be committed with the repo. Teammates who pull the repo get the
same shared context after installing Dhee.

Shared context is append-only and git-friendly. If two developers edit the same
context at the same time, Dhee keeps both versions and reports a conflict
instead of silently overwriting one developer's work.

```bash
dhee context check --repo /path/to/repo
```

The installed `pre-push` hook blocks unresolved Dhee context conflicts.

## Public vs Enterprise

Public Dhee is the developer brain: memory, handoff, local configuration, and
git-backed context.

Dhee Enterprise is closed source: dashboard, team/org management, Repo Brain
summaries, telemetry, billing, license enforcement, and security scanning.

This separation keeps the open-source package lightweight and trustworthy while
letting the commercial product move faster.

## Development

```bash
git clone https://github.com/Sankhya-AI/Dhee.git
cd Dhee
pip install -e ".[dev]"
pytest
```

## Configuration

Gemini uses `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
OpenAI uses `OPENAI_API_KEY`.

Never commit secrets. Dhee stores keys locally under `~/.dhee/`.

## License

MIT. Built by Sankhya AI Labs.
