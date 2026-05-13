"""Public Dhee dashboard using the same UI/API shape as the team dashboard."""

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

from dhee.demo import token_router_demo


STATIC_DIR = Path(__file__).with_name("static")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _org_from_env(default: str = "local") -> str:
    return os.environ.get("DHEE_UI_ORG_ID", default)


def _repo_root(root_path: str | None = None) -> Path:
    return Path(root_path or os.environ.get("DHEE_UI_ROOT") or Path.cwd()).expanduser().resolve()


def _git_value(repo: Path, args: list[str], default: str = "") -> str:
    try:
        out = subprocess.check_output(["git", *args], cwd=str(repo), stderr=subprocess.DEVNULL, text=True)
        return out.strip() or default
    except Exception:
        return default


def _team_health_from_findings(findings: list[dict[str, Any]]) -> str:
    if any(f.get("severity") == "high" for f in findings):
        return "needs_work"
    if findings:
        return "watch"
    return "healthy"


def _repo_context_entries(root: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    path = root / ".dhee" / "context" / "entries.jsonl"
    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return entries[-limit:]


def _indexed_files(root: Path, *, limit: int = 600) -> tuple[int, int]:
    ignored = {".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
    total_files = 0
    total_bytes = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ignored and not d.startswith(".tox")]
        for name in files:
            if name.endswith((".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".sqlite", ".db")):
                continue
            path = Path(base) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            total_files += 1
            total_bytes += size
            if total_files >= limit:
                return total_files, total_bytes
    return total_files, total_bytes


def _context_index(root: Path) -> list[dict[str, Any]]:
    entries = _repo_context_entries(root)
    if entries:
        out = []
        for item in entries:
            out.append(
                {
                    "context_id": item.get("id") or item.get("context_id"),
                    "title": item.get("title") or item.get("summary") or "Repo context",
                    "summary": item.get("summary") or item.get("content") or item.get("body") or item.get("reason") or "",
                    "scope": item.get("scope") or "repo",
                    "kind": item.get("kind") or item.get("type") or "note",
                    "project_id": item.get("project_id") or "local",
                    "team_id": item.get("team_id") or "local-dev",
                    "shares": item.get("shares") or [],
                }
            )
        return out
    return [
        {
            "context_id": "oss-policy",
            "title": "Context firewall baseline",
            "summary": "Agents should see compact truth first and expand raw evidence only when needed.",
            "scope": "company",
            "kind": "policy",
            "project_id": "developer-brain",
            "team_id": "local-dev",
            "shares": [],
        },
        {
            "context_id": "oss-runbook",
            "title": "Local handoff runbook",
            "summary": "Use `dhee handoff` before switching agents or resuming long-running work.",
            "scope": "team",
            "kind": "runbook",
            "project_id": "developer-brain",
            "team_id": "local-dev",
            "shares": [],
        },
    ]


def _code_brain_summary(root: Path, *, org_id: str, repo_mappings: list[dict[str, Any]]) -> dict[str, Any]:
    indexed_files, indexed_bytes = _indexed_files(root)
    mapping_status = []
    for mapping in repo_mappings:
        mapping_status.append(
            {
                "mapping_id": mapping.get("mapping_id"),
                "team_id": mapping.get("team_id"),
                "project_id": mapping.get("project_id"),
                "local_path": str(root),
                "repo_url": mapping.get("repo_url"),
                "indexed_files": indexed_files,
                "indexed_bytes": indexed_bytes,
                "updated_at": None,
                "last_sync": {"files_warmed": indexed_files, "mode": "oss-local"},
                "sync_status": "indexed" if indexed_files else "not_indexed",
            }
        )
    return {
        "indexed_files": indexed_files,
        "indexed_bytes": indexed_bytes,
        "telemetry": {"events": 0, "source": "oss-local"},
        "repo_paths": [{"repo_path": str(root), "indexed_files": indexed_files, "indexed_bytes": indexed_bytes}],
        "mapping_status": mapping_status,
    }


def build_dashboard_payload(*, org_id: str | None = None, root_path: str | None = None, repo: str | None = None) -> dict[str, Any]:
    root = _repo_root(root_path or repo)
    org = org_id or _org_from_env()
    branch = _git_value(root, ["branch", "--show-current"], "main")
    remote = _git_value(root, ["remote", "get-url", "origin"], str(root))
    context_index = _context_index(root)
    findings = [
        {
            "finding_id": "oss-router-proof",
            "team_id": "local-dev",
            "manager_id": "dhee-context-manager",
            "title": "Router demo available",
            "detail": "Use the Context Firewall tab to inspect digest-first routing and expansion pointers.",
            "severity": "low",
            "finding_type": "proof",
        }
    ]
    repo_mappings = [
        {
            "mapping_id": "local-repo",
            "team_id": "local-dev",
            "project_id": "developer-brain",
            "repo_url": remote,
            "local_path": str(root),
            "branch": branch,
            "provider": "git",
            "metadata": {"last_sync": {"mode": "oss-local"}},
        }
    ]
    code_brain = _code_brain_summary(root, org_id=org, repo_mappings=repo_mappings)
    kind_counts: dict[str, int] = {}
    scope_counts: dict[str, int] = {}
    for item in context_index:
        kind = str(item.get("kind") or "note")
        scope = str(item.get("scope") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        scope_counts[scope] = scope_counts.get(scope, 0) + 1

    managers_by_team = {
        "local-dev": {
            "manager_id": "dhee-context-manager",
            "display_name": "Dhee Context Manager",
            "team_id": "local-dev",
        }
    }
    team_rows = [
        {
            "team_id": "local-dev",
            "name": "Local Developer",
            "team_type": "project",
            "project_id": "developer-brain",
            "manager": managers_by_team["local-dev"],
            "repo_count": len(repo_mappings),
            "context_count": len(context_index),
            "open_findings": len(findings),
            "health": _team_health_from_findings(findings),
        }
    ]
    org_chart = {
        "workspace": {"name": root.name or "Dhee", "root_path": str(root), "default_branch": branch},
        "global_teams": [
            {
                "team_id": "global-context",
                "name": "Context Governance",
                "context_manager": {"manager_id": "dhee-context-manager"},
                "repo_mappings": [],
                "open_findings": [],
            }
        ],
        "projects": [
            {
                "project_id": "developer-brain",
                "name": "Developer Brain",
                "teams": [
                    {
                        "team_id": "local-dev",
                        "name": "Local Developer",
                        "context_manager": {"manager_id": "dhee-context-manager"},
                        "repo_mappings": repo_mappings,
                        "open_findings": findings,
                    }
                ],
                "repo_mappings": repo_mappings,
            }
        ],
    }
    totals = {
        "projects": 1,
        "teams": 2,
        "global_teams": 1,
        "repo_mappings": len(repo_mappings),
        "context_items": len(context_index),
        "context_managers": 1,
        "open_findings": len(findings),
        "shares": 0,
        "indexed_files": code_brain["indexed_files"],
    }
    context_firewall = token_router_demo()
    raw = {
        "org_id": org,
        "workspace": org_chart["workspace"],
        "projects": org_chart["projects"],
        "global_teams": org_chart["global_teams"],
        "repo_mappings": repo_mappings,
        "context_index": context_index,
        "context_managers": list(managers_by_team.values()),
        "context_manager_findings": findings,
        "context_manager_findings_by_team": {"local-dev": findings},
        "context_managers_by_team": managers_by_team,
        "repo_mappings_by_team": {"local-dev": repo_mappings},
        "team_context": {"local-dev": context_index},
        "context_shares": [],
        "org_chart": org_chart,
    }
    return {
        "org_id": org,
        "workspace": org_chart["workspace"],
        "totals": totals,
        "commercial": {
            "license": {"edition": "public", "status": "active"},
            "billing": {"plan": "public", "usage": 0},
        },
        "org_chart": org_chart,
        "team_rows": team_rows,
        "kind_counts": kind_counts,
        "scope_counts": scope_counts,
        "repo_mappings": repo_mappings,
        "code_brain": code_brain,
        "context_firewall": context_firewall,
        "context_index": context_index[:100],
        "findings": findings,
        "raw": raw,
    }


def seed_demo_workspace(*, org_id: str | None = None) -> dict[str, Any]:
    return {"seeded": True, "dashboard": build_dashboard_payload(org_id=org_id)}


def connect_real_workspace(*, org_id: str | None = None, root_path: str | None = None, limit: int | None = None) -> dict[str, Any]:
    root = _repo_root(root_path)
    return {
        "connected": True,
        "root_path": str(root),
        "real": {"limit": limit, "mode": "oss-local"},
        "dashboard": build_dashboard_payload(org_id=org_id, root_path=str(root)),
    }


class DheeDashboardHandler(BaseHTTPRequestHandler):
    server_version = "DheeUI/0.1"

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _org(self) -> str:
        query = self._query()
        return (query.get("org") or [_org_from_env()])[0]

    def _root_path(self) -> str | None:
        return (self._query().get("root") or [None])[0]

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
            self._send_json(build_dashboard_payload(org_id=self._org(), root_path=self._root_path()))
            return
        if parsed.path == "/api/context-firewall":
            self._send_json(token_router_demo())
            return
        if parsed.path == "/api/team":
            team = (self._query().get("team") or [""])[0]
            if not team:
                self._send_json({"error": "team is required"}, HTTPStatus.BAD_REQUEST)
                return
            dashboard = build_dashboard_payload(org_id=self._org(), root_path=self._root_path())
            self._send_json(
                {
                    "team_id": team,
                    "context": [item for item in dashboard["context_index"] if item.get("team_id") in {team, "local-dev"}],
                    "findings": [item for item in dashboard["findings"] if item.get("team_id") in {team, "local-dev"}],
                }
            )
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
            self._send_json(seed_demo_workspace(org_id=self._org()))
            return
        if parsed.path == "/api/real":
            query = self._query()
            limit_raw = (query.get("limit") or [""])[0]
            limit = int(limit_raw) if limit_raw.strip().isdigit() else None
            root_path = (query.get("root") or [None])[0]
            self._send_json(connect_real_workspace(org_id=self._org(), root_path=root_path, limit=limit))
            return
        if parsed.path == "/api/sync":
            self._send_json({"sync": {"mode": "oss-local", "ok": True}, "dashboard": build_dashboard_payload(org_id=self._org())})
            return
        if parsed.path == "/api/review":
            team = (self._query().get("team") or [""])[0]
            if not team:
                self._send_json({"error": "team is required"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(
                {
                    "review": {"team_id": team, "mode": "oss-local", "ok": True},
                    "dashboard": build_dashboard_payload(org_id=self._org()),
                }
            )
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(*, host: str = "127.0.0.1", port: int = 8765, org_id: str | None = None, repo: str | None = None, open_browser: bool = False) -> ThreadingHTTPServer:
    if host not in _LOOPBACK_HOSTS and os.environ.get("DHEE_UI_ALLOW_PUBLIC") != "1":
        raise ValueError(
            "Refusing to expose Dhee UI on a non-loopback host. "
            "Set DHEE_UI_ALLOW_PUBLIC=1 only behind a trusted auth proxy."
        )
    if org_id:
        os.environ["DHEE_UI_ORG_ID"] = org_id
    if repo:
        os.environ["DHEE_UI_ROOT"] = str(_repo_root(repo))
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
    parser.add_argument("--org", default=None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, org_id=args.org, repo=args.repo, open_browser=args.open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
