# gstack adapter

[gstack](https://github.com/garrytan/gstack) is a 23-skill Claude Code
skill pack by Garry Tan. gstack writes its memory to
`${GSTACK_HOME:-~/.gstack}/projects/<slug>/` as four surfaces:

- `learnings.jsonl` — one validated learning per line
- `timeline.jsonl` — one skill-fire event per line
- `<branch>-reviews.jsonl` — one review finding per line
- `checkpoints/<ts>-<slug>.md` — YAML frontmatter plus four H3 sections
  (Summary / Decisions / Remaining / Notes)

Retrieval inside gstack is substring-only on learnings, has no
consolidation of near-duplicate keys, no correction loop, and rehydrates
checkpoints with `ls -t | head -3`. That works for a 3-month horizon;
past that, it loses signal.

Dhee already has the substrates that fix all six gaps. This adapter just
wires gstack's files into Dhee's existing `remember` pipeline so every
ingested atom flows through the same embedding, engram extraction,
conflict, and forgetting machinery as every other Dhee memory.

## Install

```bash
dhee install gstack
```

Feature-detected: the command is a clean no-op if gstack is not
installed (no `~/.claude/skills/gstack/VERSION`). gstack continues to
work standalone; Dhee never mutates gstack's files.

Disable:

```bash
dhee harness disable --harness gstack
```

## Status and re-ingest

```bash
dhee adapters gstack status
dhee adapters gstack reingest           # ingest deltas since last run
dhee adapters gstack reingest --reset   # clear cursor manifest, reread everything
```

## How gstack failure modes map to Dhee components

| gstack failure mode | Dhee component that fixes it |
|---|---|
| Substring-only learnings search | `dhee/memory/search_pipeline.py` + `dhee/memory/reranker.py` |
| No consolidation of near-duplicate keys | `dhee/core/engram.py` + the write pipeline |
| No correction / invalidation loop | `dhee/core/conflict.py` + `dhee/core/forgetting.py` |
| Checkpoint rehydration is `ls -t \| head -3` | `dhee/memory/episodic.py` + retrieval helpers |
| No code world-model | `dhee/hooks/claude_code/ingest.py` builds one from tool I/O |
| Cross-project learnings honor-system | `dhee/memory/projects.py` scopes atoms by slug |

## Contract

1. **Zero mutation of gstack files.** Read-only ingest.
2. **Idempotent.** Per-project cursors at
   `$DHEE_DATA_DIR/gstack_manifest.json` (JSONL cursors in bytes,
   checkpoints keyed by filename + mtime + size).
3. **Respects `$GSTACK_HOME`.** Matches gstack's own override.
4. **Injection-safe.** Atoms that match gstack's own prompt-injection
   denylist are dropped before `remember`.
5. **Session-safe.** Claude Code hooks call `tail_ingest()` on
   `SessionStart` and `Stop`. All errors swallowed; the hook never
   blocks the session.

## Uninstall

`dhee harness disable --harness gstack` clears the cursor manifest and
flips the config flag. gstack's own files under `~/.gstack/` are never
touched.
