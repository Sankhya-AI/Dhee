"""Local-first Dhee dashboard.

The public UI intentionally uses the same small HTTP/static-file shape as the
team dashboard, but its data model is local developer context: router savings,
runtime health, handoff state, repo context, integrations, and portability.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dhee.context_state import ContextStateStore
from dhee.core.kernel import get_last_session
from dhee.demo import token_router_demo


STATIC_DIR = Path(__file__).with_name("static")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _repo_root(repo: str | None = None) -> Path:
    return Path(repo or os.environ.get("DHEE_UI_REPO") or Path.cwd()).expanduser().resolve()


def _git_value(repo: Path, args: list[str], default: str = "") -> str:
    try:
        out = subprocess.check_output(["git", *args], cwd=str(repo), stderr=subprocess.DEVNULL, text=True)
        return out.strip() or default
    except Exception:
        return default


def _read_repo_context(repo: Path, *, limit: int = 30) -> dict[str, Any]:
    path = repo / ".dhee" / "context" / "entries.jsonl"
    entries: list[dict[str, Any]] = []
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entries.append(item)
        except OSError:
            pass

    kind_counts: dict[str, int] = {}
    for item in entries:
        kind = str(item.get("kind") or item.get("type") or "note")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    return {
        "path": str(path),
        "exists": path.exists(),
        "count": len(entries),
        "kind_counts": kind_counts,
        "entries": entries[-limit:],
    }


def _runtime_status() -> dict[str, Any]:
    try:
        from dhee import runtime

        return runtime.status(timeout=0.25)
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


def _context_status(repo: Path) -> dict[str, Any]:
    store = ContextStateStore(repo=str(repo), workspace_id=str(repo), user_id="default", agent_id="dhee-ui")
    try:
        status = store.status()
        card = store.render_state_card()
    except Exception as exc:
        return {"status": {"level": "unknown", "error": str(exc)}, "card": ""}
    return {"status": status, "card": card}


def _handoff(repo: Path) -> dict[str, Any]:
    session = get_last_session(agent_id="codex", repo=str(repo), requester_agent_id="dhee-ui")
    if not session:
        return {"available": False}
    return {
        "available": True,
        "id": session.get("id"),
        "agent_id": session.get("agent_id"),
        "status": session.get("status"),
        "task_summary": session.get("task_summary") or session.get("summary"),
        "updated": session.get("updated") or session.get("updated_at"),
        "decisions": session.get("decisions") or [],
        "todos": session.get("todos") or [],
        "files_touched": session.get("files_touched") or [],
    }


def _integrations() -> list[dict[str, Any]]:
    return [
        {
            "name": "Claude Code",
            "level": "deep",
            "status": "hooks + MCP",
            "detail": "Best routing surface for read, bash, grep, handoff, and learned playbooks.",
        },
        {
            "name": "Codex",
            "level": "native",
            "status": "MCP + AGENTS + session sync",
            "detail": "Uses the strongest truthful Codex surfaces available without claiming pre-tool hooks.",
        },
        {
            "name": "Cursor / Gemini / Cline / Goose",
            "level": "mcp",
            "status": "dhee-mcp",
            "detail": "One local MCP server exposes the same context firewall primitives.",
        },
        {
            "name": "Hermes",
            "level": "provider",
            "status": "MemoryProvider",
            "detail": "Promoted learnings can flow between Hermes, Claude Code, Codex, and MCP clients.",
        },
    ]


def build_dashboard_payload(*, repo: str | None = None) -> dict[str, Any]:
    root = _repo_root(repo)
    firewall = token_router_demo()
    aggregate = firewall.get("aggregate") or {}
    repo_context = _read_repo_context(root)
    context = _context_status(root)
    runtime_status = _runtime_status()
    handoff = _handoff(root)
    integrations = _integrations()
    branch = _git_value(root, ["branch", "--show-current"], "unknown")
    remote = _git_value(root, ["remote", "get-url", "origin"], "")

    runtime_running = bool((runtime_status.get("daemon") or {}).get("running"))
    context_status = context.get("status") or {}
    totals = {
        "router_saved_pct": aggregate.get("saved_pct", 0),
        "raw_tokens": aggregate.get("raw_tokens", 0),
        "digest_tokens": aggregate.get("digest_tokens", 0),
        "state_level": context_status.get("level") or "unknown",
        "runtime": 1 if runtime_running else 0,
        "repo_context": repo_context.get("count", 0),
        "integrations": len(integrations),
        "portable": 1,
    }

    return {
        "format": "dhee_public_dashboard",
        "version": 1,
        "workspace": {
            "name": root.name or "Dhee Local Brain",
            "root_path": str(root),
            "branch": branch,
            "remote": remote,
        },
        "totals": totals,
        "context_firewall": firewall,
        "runtime": runtime_status,
        "context_state": context,
        "handoff": handoff,
        "repo_context": repo_context,
        "integrations": integrations,
        "portability": {
            "export": "dhee export --format dheemem --output backup.dheemem",
            "dry_run_import": "dhee import backup.dheemem --format dheemem --strategy dry-run",
            "uninstall": "dhee uninstall --yes",
        },
    }


class DheeDashboardHandler(BaseHTTPRequestHandler):
    server_version = "DheeUI/0.1"

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _repo(self) -> str | None:
        return (self._query().get("repo") or [None])[0]

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/dashboard":
            self._send_json(build_dashboard_payload(repo=self._repo()))
            return
        if parsed.path == "/api/context-firewall":
            self._send_json(token_router_demo())
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._send_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/demo":
            self._send_json({"seeded": True, "dashboard": build_dashboard_payload(repo=self._repo())})
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    repo: str | None = None,
    open_browser: bool = False,
) -> ThreadingHTTPServer:
    if host not in _LOOPBACK_HOSTS and os.environ.get("DHEE_UI_ALLOW_PUBLIC") != "1":
        raise ValueError(
            "Refusing to expose Dhee UI on a non-loopback host. "
            "Set DHEE_UI_ALLOW_PUBLIC=1 only behind a trusted auth proxy."
        )
    if repo:
        os.environ["DHEE_UI_REPO"] = str(_repo_root(repo))
    httpd = ThreadingHTTPServer((host, port), DheeDashboardHandler)
    url = f"http://{host}:{port}"
    print(f"Dhee UI running at {url}")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dhee-ui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, repo=args.repo, open_browser=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
