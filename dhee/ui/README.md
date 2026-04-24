# Sankhya — Dhee's web UI

Sankhya is the pixel-faithful implementation of the
`Sankhya v2.html` handoff from Claude Design, wired to the real Dhee
substrate.

## Architecture

```
┌─────────────┐      HTTP/JSON      ┌──────────────────┐
│  Sankhya    │ ──────────────────▶ │  FastAPI bridge  │
│  (Vite+RTS) │ ◀────────────────── │  dhee.ui.server  │
└─────────────┘                     └───────┬──────────┘
                                            │
                                            ▼
                                    ┌─────────────────┐
                                    │   Dhee core     │
                                    │  (FullMemory,   │
                                    │   router,       │
                                    │   MetaBuddhi,   │
                                    │   shared tasks) │
                                    └─────────────────┘
```

- `dhee/ui/server.py` — FastAPI app, endpoints under `/api/*`.
- `dhee/ui/web/` — Vite + React + TypeScript SPA.
- `dhee/ui/cli.py` — `dhee ui` + `dhee ui-build` subcommands.

## Run it

First build (once):

```bash
dhee ui-build                         # npm install + npm run build
# or manually:
cd dhee/ui/web && npm install && npm run build
```

Then:

```bash
dhee ui                               # http://127.0.0.1:8787
```

### Development loop (hot reload)

```bash
# terminal 1 — API bridge only
dhee ui --dev

# terminal 2 — Vite dev server, proxies /api to :8787
cd dhee/ui/web && npm run dev         # http://127.0.0.1:5173
```

## Endpoints (wire-level)

| Endpoint                             | Source                                       |
|--------------------------------------|----------------------------------------------|
| `GET  /api/memories`                 | `FullMemory.get_all` → engram shape          |
| `POST /api/memories`                 | `FullMemory.add`                             |
| `DEL  /api/memories/{id}`            | `FullMemory.delete`                          |
| `GET  /api/router/stats`             | `dhee.router.stats.compute_stats`            |
| `GET  /api/router/policy`            | `router.policy.load` + `tune.build_report`   |
| `POST /api/router/tune`              | `router.tune.build_report` + `apply`         |
| `GET  /api/meta-buddhi`              | `core.meta_buddhi.latest_snapshot` (if any)  |
| `GET  /api/evolution`                | `~/.dhee/evolution/*.jsonl`                  |
| `GET  /api/conflicts`                | `FullMemory.get_conflicts` (if present)      |
| `POST /api/conflicts/{id}/resolve`   | native runtime resolver only; `501` otherwise |
| `GET  /api/tasks`                    | `core.shared_tasks.shared_task_snapshot`     |
| `POST /api/tasks`                    | in-memory only for now                       |
| `POST /api/capture/session/start`    | starts a pointer-capture session             |
| `POST /api/capture/session/end`      | ends session + distills durable memory       |
| `POST /api/capture/action`           | stores pointer action + surface context      |
| `POST /api/capture/observation`      | stores DOM/AX observation                    |
| `POST /api/capture/artifact`         | stores temporary screenshot artifact         |
| `GET  /api/capture/session/{id}`     | reads the temp JSONL session graph           |
| `GET  /api/capture/timeline`         | mixed capture/action/memory timeline         |
| `GET  /api/capture/preferences`      | per-app capture policy                       |
| `POST /api/capture/preferences`      | updates per-app capture policy               |
| `GET  /api/memory/now`               | active capture + recent durable memory       |
| `POST /api/memory/ask`               | search durable memory + live session graph   |
| `POST /api/agents/context-pack`      | combined agent retrieval pack                |
| `POST /api/launch`                   | returns the install command                  |
| `GET  /api/status`                   | router stats + data dir                      |

## Honesty: what's live vs stubbed

**Live**: memory list/add/archive, router stats & policy, router tune,
tasks view (shared_task_snapshot → cards), 7-day savings (from ptr_store
mtimes), evolution events (if `~/.dhee/evolution/*.jsonl` exists),
pointer-capture sessions, temp JSONL session graph, surface memory cards,
world-memory transition mirroring, and localhost APIs for the native orb
shell and Chrome extension.

**Stubbed**: conflict resolution (read is best-effort; writing is a
TODO); task.create writes to memory-only state (shared-task write API
not yet wired); Launch flow echoes the install command — it does not
yet spawn Claude Code or Codex on its own.

UI components display `NOT LIVE` / `stub` badges when the backend
couldn't honestly populate a section, rather than silently serving
fake data.

## Pixel fidelity

The CSS variables (`--bg`, `--ink`, `--accent`, etc.) and all inline
styles are copied verbatim from the `Sankhya v2.html` prototype.
Component trees, spacing, borders, oklch colour values — all
preserved. The one deviation: data comes from real endpoints, so
numbers will differ from the prototype's hard-coded samples.
