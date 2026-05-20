"""Built-in DheeFS mounts."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dhee.fs.types import DheeFSEntry, DheeFSNotFoundError, DheeMount, entries_to_text


_LEARNING_DIRS = {
    "candidates": "candidate",
    "promoted": "promoted",
    "rejected": "rejected",
    "archived": "archived",
}


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _parts(path: str) -> List[str]:
    return [p for p in str(path or "").strip("/").split("/") if p]


def _strip_md(name: str) -> str:
    return name[:-3] if name.endswith(".md") else name


def _ts(value: Any) -> str:
    if not value:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(value)))
    except Exception:
        return str(value)


def _metadata_lines(data: Dict[str, Any], keys: Iterable[str]) -> List[str]:
    lines: List[str] = []
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        if key.endswith("_at") and isinstance(value, (int, float)):
            value = _ts(value)
        lines.append(f"- {key}: {value}")
    return lines


class StateMount(DheeMount):
    prefix = "/state"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["state"]:
            return [
                DheeFSEntry(name="current.md", path="/state/current.md"),
                DheeFSEntry(name="card.xml", path="/state/card.xml"),
                DheeFSEntry(name="decisions.md", path="/state/decisions.md"),
                DheeFSEntry(name="superseded.md", path="/state/superseded.md"),
                DheeFSEntry(name="history.md", path="/state/history.md"),
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        store = self.workspace.context_state_store()
        if parts == ["state"]:
            return entries_to_text(self.list(path))
        if parts == ["state", "current.md"]:
            state = store.load()
            if not state.get("goal") and hasattr(self.workspace, "infer_current_goal"):
                inferred = self.workspace.infer_current_goal()
                if inferred:
                    state["goal"] = inferred
                    state["goal_inferred_from"] = "dhee_memory_or_shared_task"
            return store.render_markdown(state=state)
        if parts == ["state", "card.xml"]:
            state = store.load()
            if not state.get("goal") and hasattr(self.workspace, "infer_current_goal"):
                inferred = self.workspace.infer_current_goal()
                if inferred:
                    state["goal"] = inferred
                    state["goal_inferred_from"] = "dhee_memory_or_shared_task"
            return store.render_state_card(state=state)
        if parts == ["state", "decisions.md"]:
            return store.render_decisions(superseded=False)
        if parts == ["state", "superseded.md"]:
            return store.render_decisions(superseded=True)
        if parts == ["state", "history.md"]:
            return store.render_history()
        raise DheeFSNotFoundError(path)

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        text = self.read(path if path != "/state" else "/state/current.md")
        needle = str(query or "").lower()
        return [
            {"path": path, "line": idx, "text": line}
            for idx, line in enumerate(text.splitlines(), start=1)
            if not needle or needle in line.lower()
        ]


class ContextMount(DheeMount):
    prefix = "/context"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["context"]:
            return [
                DheeFSEntry(name="status.json", path="/context/status.json"),
                DheeFSEntry(name="debt.json", path="/context/debt.json"),
                DheeFSEntry(name="checkpoints/", path="/context/checkpoints", kind="dir"),
            ]
        if parts == ["context", "checkpoints"]:
            return [
                DheeFSEntry(
                    name=f"{row.get('id')}.json",
                    path=f"/context/checkpoints/{row.get('id')}.json",
                    metadata={"created_at": row.get("created_at"), "reason": row.get("reason")},
                )
                for row in self.workspace.context_state_store().list_checkpoints()
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        store = self.workspace.context_state_store()
        if parts == ["context"]:
            return entries_to_text(self.list(path))
        if parts == ["context", "status.json"]:
            return _json(store.status())
        if parts == ["context", "debt.json"]:
            return _json(store.debt_summary(top=True))
        if parts == ["context", "checkpoints"]:
            return entries_to_text(self.list(path))
        if len(parts) == 3 and parts[:2] == ["context", "checkpoints"]:
            checkpoint = store.read_checkpoint(_strip_md(parts[2]))
            if not checkpoint:
                raise DheeFSNotFoundError(f"unknown checkpoint: {parts[2]}")
            return _json(checkpoint)
        raise DheeFSNotFoundError(path)


class LearningMount(DheeMount):
    prefix = "/learnings"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["learnings"]:
            return [
                DheeFSEntry(name=f"{name}/", path=f"/learnings/{name}", kind="dir")
                for name in _LEARNING_DIRS
            ]
        if len(parts) == 2 and parts[0] == "learnings":
            status = _LEARNING_DIRS.get(parts[1])
            if not status:
                raise DheeFSNotFoundError(f"unknown learning directory: /learnings/{parts[1]}")
            rows = self.workspace.learning_exchange.list(status=status)
            rows.sort(key=lambda row: (row.updated_at, row.created_at), reverse=True)
            return [
                DheeFSEntry(
                    name=f"{row.id}.md",
                    path=f"/learnings/{parts[1]}/{row.id}.md",
                    metadata={"title": row.title, "status": row.status, "kind": row.kind},
                )
                for row in rows
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        if len(parts) <= 2:
            return entries_to_text(self.list(path))
        status_dir, learning_id = self._parse_learning_path(path)
        item = self.workspace.learning_exchange.get(learning_id)
        if not item:
            raise DheeFSNotFoundError(f"unknown learning: {learning_id}")
        expected = _LEARNING_DIRS.get(status_dir)
        if expected and item.status != expected:
            raise DheeFSNotFoundError(f"{learning_id} is {item.status}, not {expected}")
        return self._render_learning(item)

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        parts = _parts(path)
        statuses: List[str]
        if len(parts) >= 2 and parts[1] in _LEARNING_DIRS:
            statuses = [_LEARNING_DIRS[parts[1]]]
        else:
            statuses = list(_LEARNING_DIRS.values())
        needle = str(query or "").lower()
        matches: List[Dict[str, Any]] = []
        for status in statuses:
            dir_name = next(k for k, v in _LEARNING_DIRS.items() if v == status)
            for row in self.workspace.learning_exchange.list(status=status):
                haystack = "\n".join([row.id, row.title, row.body, row.kind, row.task_type or ""]).lower()
                if needle and needle not in haystack:
                    continue
                matches.append(
                    {
                        "path": f"/learnings/{dir_name}/{row.id}.md",
                        "learning_id": row.id,
                        "status": row.status,
                        "title": row.title,
                        "snippet": row.body[:240],
                    }
                )
        return matches

    def explain(self, path: str) -> str:
        _status_dir, learning_id = self._parse_learning_path(path)
        item = self.workspace.learning_exchange.get(learning_id)
        if not item:
            raise DheeFSNotFoundError(f"unknown learning: {learning_id}")
        data = item.to_dict()
        lines = [
            f"# Why: {item.title}",
            "",
            "## Provenance",
            f"- id: {item.id}",
            f"- status: {item.status}",
            f"- source_agent_id: {item.source_agent_id}",
            f"- source_harness: {item.source_harness}",
            f"- scope: {item.scope}",
            f"- repo: {item.repo or ''}",
            f"- created_at: {_ts(item.created_at)}",
            f"- updated_at: {_ts(item.updated_at)}",
        ]
        if item.promoted_at:
            lines.append(f"- promoted_at: {_ts(item.promoted_at)}")
        if item.rejected_reason:
            lines.append(f"- rejected_reason: {item.rejected_reason}")
        lines.extend(
            [
                "",
                "## Reuse",
                f"- reuse_count: {item.reuse_count}",
                f"- success_count: {item.success_count}",
                f"- failure_count: {item.failure_count}",
                f"- confidence: {item.confidence}",
                f"- utility: {item.utility}",
                "",
                "## Evidence",
                _json(data.get("evidence") or []),
                "",
                "## Metadata",
                _json(data.get("metadata") or {}),
            ]
        )
        return "\n".join(lines)

    def promote(self, path_or_id: str, *, scope: str = "personal", repo: Optional[str] = None) -> Dict[str, Any]:
        learning_id = self._id_from_path_or_value(path_or_id)
        item = self.workspace.learning_exchange.promote(
            learning_id,
            scope=scope,
            repo=repo or self.workspace.repo,
            approved_by=self.workspace.agent_id or "dheefs",
        )
        return {"learning": item.to_dict()}

    def reject(self, path_or_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        learning_id = self._id_from_path_or_value(path_or_id)
        item = self.workspace.learning_exchange.reject(learning_id, reason=reason)
        return {"learning": item.to_dict()}

    def _parse_learning_path(self, path: str) -> Tuple[str, str]:
        parts = _parts(path)
        if len(parts) != 3 or parts[0] != "learnings":
            raise DheeFSNotFoundError(path)
        if parts[1] not in _LEARNING_DIRS:
            raise DheeFSNotFoundError(path)
        return parts[1], _strip_md(parts[2])

    def _id_from_path_or_value(self, value: str) -> str:
        value = str(value or "").strip()
        if value.startswith("/"):
            return self._parse_learning_path(value)[1]
        return _strip_md(value.rsplit("/", 1)[-1])

    def _render_learning(self, item: Any) -> str:
        data = item.to_dict()
        lines = [
            f"# {item.title}",
            "",
            *(_metadata_lines(
                data,
                [
                    "id",
                    "kind",
                    "status",
                    "scope",
                    "source_agent_id",
                    "source_harness",
                    "task_type",
                    "repo",
                    "confidence",
                    "utility",
                    "reuse_count",
                    "success_count",
                    "failure_count",
                    "created_at",
                    "updated_at",
                    "promoted_at",
                    "rejected_reason",
                ],
            )),
            "",
            "## Body",
            item.body,
        ]
        evidence = data.get("evidence") or []
        if evidence:
            lines.extend(["", "## Evidence", _json(evidence)])
        metadata = data.get("metadata") or {}
        if metadata:
            lines.extend(["", "## Metadata", _json(metadata)])
        return "\n".join(lines)


class RouterMount(DheeMount):
    prefix = "/router"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["router"]:
            return [
                DheeFSEntry(name="ptr/", path="/router/ptr", kind="dir"),
                DheeFSEntry(name="policy.json", path="/router/policy.json"),
                DheeFSEntry(name="reports/", path="/router/reports", kind="dir"),
            ]
        if parts == ["router", "ptr"]:
            return []
        if parts == ["router", "reports"]:
            return []
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        if parts == ["router", "policy.json"]:
            return _json(
                {
                    "pointer_expansion": "explicit_only",
                    "dheefs_reads_create_memories": False,
                    "supported_paths": ["/router/ptr/<ptr>"],
                }
            )
        if len(parts) == 3 and parts[:2] == ["router", "ptr"]:
            from dhee.router import ptr_store

            ptr = parts[2]
            raw = ptr_store.load(ptr)
            if raw is None:
                raise DheeFSNotFoundError(f"unknown router pointer: {ptr}")
            ptr_store.record_expansion(
                ptr,
                tool="dhee_shell",
                intent="cat_router_ptr",
                depth="raw",
                agent_id=getattr(self.workspace, "agent_id", ""),
            )
            return raw
        return entries_to_text(self.list(path))

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        text = self.read(path)
        needle = str(query or "").lower()
        return [
            {"path": path, "line": idx, "text": line}
            for idx, line in enumerate(text.splitlines(), start=1)
            if not needle or needle in line.lower()
        ]


class HandoffMount(DheeMount):
    prefix = "/handoff"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["handoff"]:
            return [
                DheeFSEntry(name="latest.md", path="/handoff/latest.md"),
                DheeFSEntry(name="snapshot.json", path="/handoff/snapshot.json"),
                DheeFSEntry(name="recent/", path="/handoff/recent", kind="dir"),
            ]
        if parts == ["handoff", "recent"]:
            return [DheeFSEntry(name="latest.md", path="/handoff/latest.md")]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        snapshot = self.workspace.handoff_snapshot()
        if parts in (["handoff", "snapshot.json"], ["handoff", "recent", "latest.json"]):
            return _json(snapshot)
        if parts in (["handoff", "latest.md"], ["handoff", "recent", "latest.md"]):
            return self._render(snapshot)
        return entries_to_text(self.list(path))

    def _render(self, snapshot: Dict[str, Any]) -> str:
        if snapshot.get("error"):
            return f"# Latest Handoff\n\n{snapshot['error']}"
        lines = [
            "# Latest Handoff",
            "",
            f"- generated_at: {snapshot.get('generated_at', '')}",
            f"- continuity_source: {snapshot.get('continuity_source', 'none')}",
            f"- repo: {snapshot.get('repo') or ''}",
            "",
            "## Resume Hints",
        ]
        hints = snapshot.get("resume_hints") or []
        lines.extend([f"- {hint}" for hint in hints] or ["- none"])
        if snapshot.get("shared_task"):
            task = snapshot["shared_task"]
            lines.extend(["", "## Shared Task", f"- id: {task.get('id')}", f"- title: {task.get('title') or task.get('goal') or ''}"])
        results = snapshot.get("shared_task_results") or []
        if results:
            lines.append("")
            lines.append("## Shared Results")
            for row in results[:5]:
                digest = str(row.get("digest") or "").strip().splitlines()
                lines.append(f"- {row.get('tool_name') or row.get('packet_kind')}: {(digest[0] if digest else '')[:160]}")
        return "\n".join(lines)


class ArtifactMount(DheeMount):
    prefix = "/artifacts"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts != ["artifacts"]:
            raise DheeFSNotFoundError(path)
        rows = self.workspace.list_artifacts(limit=50)
        return [
            DheeFSEntry(
                name=f"{row.get('artifact_id')}.md",
                path=f"/artifacts/{row.get('artifact_id')}.md",
                metadata={
                    "filename": row.get("filename"),
                    "lifecycle_state": row.get("lifecycle_state"),
                    "extraction_count": row.get("extraction_count"),
                },
            )
            for row in rows
            if row.get("artifact_id")
        ]

    def read(self, path: str) -> str:
        parts = _parts(path)
        if parts == ["artifacts"]:
            return entries_to_text(self.list(path))
        if len(parts) != 2:
            raise DheeFSNotFoundError(path)
        artifact_id = _strip_md(parts[1])
        artifact = self.workspace.get_artifact(artifact_id)
        if not artifact:
            raise DheeFSNotFoundError(f"unknown artifact: {artifact_id}")
        return self._render(artifact)

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        needle = str(query or "").lower()
        rows = self.workspace.list_artifacts(limit=200)
        matches: List[Dict[str, Any]] = []
        for row in rows:
            artifact_id = row.get("artifact_id")
            if not artifact_id:
                continue
            artifact = self.workspace.get_artifact(str(artifact_id)) or row
            text = self._render(artifact)
            for lineno, line in enumerate(text.splitlines(), start=1):
                if not needle or needle in line.lower():
                    matches.append({"path": f"/artifacts/{artifact_id}.md", "line": lineno, "text": line})
        return matches

    def _render(self, artifact: Dict[str, Any]) -> str:
        lines = [
            f"# {artifact.get('filename') or artifact.get('artifact_id')}",
            "",
            *_metadata_lines(
                artifact,
                [
                    "artifact_id",
                    "mime_type",
                    "byte_size",
                    "lifecycle_state",
                    "binding_count",
                    "extraction_count",
                    "created_at",
                    "updated_at",
                    "last_extraction_at",
                ],
            ),
        ]
        extractions = artifact.get("extractions") or []
        if extractions:
            lines.extend(["", "## Extractions"])
            for row in extractions[:3]:
                text = str(row.get("extracted_text") or "")
                lines.append(f"### {row.get('extraction_source') or row.get('id')}")
                lines.append(text[:1200])
        chunks = artifact.get("chunks") or []
        if chunks:
            lines.extend(["", "## Chunks"])
            for row in chunks[:8]:
                lines.append(f"### chunk {row.get('chunk_index', 0)}")
                lines.append(str(row.get("content") or "")[:800])
        return "\n".join(lines)


class RepoMount(DheeMount):
    prefix = "/repo"

    _ROOT_FILES = {
        "decisions.md": "decisions.md",
        "conventions.md": "conventions.md",
        "handoff.md": "handoff.md",
        "active-tasks.jsonl": "active-tasks.jsonl",
    }

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["repo"]:
            entries = [
                DheeFSEntry(name=name, path=f"/repo/{name}") for name in self._ROOT_FILES
            ]
            entries.append(DheeFSEntry(name="context/", path="/repo/context", kind="dir"))
            return entries
        if parts == ["repo", "context"]:
            context_dir = self._context_dir()
            if not context_dir.exists():
                return []
            return [
                DheeFSEntry(
                    name=f"{child.name}/" if child.is_dir() else child.name,
                    path=f"/repo/context/{child.name}",
                    kind="dir" if child.is_dir() else "file",
                )
                for child in sorted(context_dir.iterdir(), key=lambda p: p.name)
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        if parts == ["repo"]:
            return entries_to_text(self.list(path))
        if len(parts) == 2 and parts[0] == "repo" and parts[1] in self._ROOT_FILES:
            return self._read_context_file(self._ROOT_FILES[parts[1]], default_title=parts[1])
        if len(parts) >= 3 and parts[:2] == ["repo", "context"]:
            rel = "/".join(parts[2:])
            return self._read_context_file(rel)
        raise DheeFSNotFoundError(path)

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        needle = str(query or "").lower()
        matches: List[Dict[str, Any]] = []
        context_dir = self._context_dir()
        if not context_dir.exists():
            return matches
        for file_path in context_dir.rglob("*"):
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except Exception:
                continue
            virtual = f"/repo/context/{file_path.relative_to(context_dir)}"
            for lineno, line in enumerate(text.splitlines(), start=1):
                if not needle or needle in line.lower():
                    matches.append({"path": virtual, "line": lineno, "text": line})
        return matches

    def _context_dir(self) -> Path:
        repo = Path(self.workspace.repo or os.getcwd()).expanduser()
        return repo / ".dhee" / "context"

    def _read_context_file(self, rel: str, *, default_title: Optional[str] = None) -> str:
        target = (self._context_dir() / rel).resolve()
        root = self._context_dir().resolve()
        if root not in target.parents and target != root:
            raise DheeFSNotFoundError(rel)
        if not target.exists() or not target.is_file():
            if default_title:
                return f"# {default_title}\n\nNo repo context file exists at .dhee/context/{rel}."
            raise DheeFSNotFoundError(f"missing repo context file: {rel}")
        return target.read_text(encoding="utf-8")


class SessionsMount(DheeMount):
    prefix = "/sessions"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["sessions"]:
            return [
                DheeFSEntry(name="current/", path="/sessions/current", kind="dir"),
                DheeFSEntry(name="latest.md", path="/sessions/latest.md"),
                DheeFSEntry(name="latest.json", path="/sessions/latest.json"),
            ]
        if parts == ["sessions", "current"]:
            return [
                DheeFSEntry(name="audit.jsonl", path="/sessions/current/audit.jsonl"),
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        snapshot = self.workspace.handoff_snapshot()
        if parts == ["sessions", "latest.json"]:
            return _json(snapshot)
        if parts == ["sessions", "latest.md"]:
            session = snapshot.get("last_session") or {}
            if not session:
                return "# Latest Session\n\nNo saved session digest found."
            lines = ["# Latest Session", ""]
            lines.extend(_metadata_lines(session, ["id", "agent_id", "status", "task_summary", "created_at", "updated_at"]))
            todos = session.get("todos") or session.get("todos_remaining") or []
            if todos:
                lines.extend(["", "## Todos"])
                lines.extend([f"- {todo}" for todo in todos])
            return "\n".join(lines)
        if parts == ["sessions", "current"]:
            return entries_to_text(self.list(path))
        if parts == ["sessions", "current", "audit.jsonl"]:
            return self.workspace.context_state_store().read_audit_text(limit=500)
        return entries_to_text(self.list(path))


class AgentsMount(DheeMount):
    prefix = "/agents"

    _AGENTS = ("hermes", "claude-code", "codex")

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["agents"]:
            return [
                DheeFSEntry(name=f"{agent}/", path=f"/agents/{agent}", kind="dir")
                for agent in self._AGENTS
            ]
        if len(parts) == 2 and parts[0] == "agents" and parts[1] in self._AGENTS:
            agent = parts[1]
            return [
                DheeFSEntry(name="memory.md", path=f"/agents/{agent}/memory.md"),
                DheeFSEntry(name="sessions/", path=f"/agents/{agent}/sessions", kind="dir"),
                DheeFSEntry(name="learnings/", path=f"/agents/{agent}/learnings", kind="dir"),
                DheeFSEntry(name="handoffs/", path=f"/agents/{agent}/handoffs", kind="dir"),
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        if parts == ["agents"]:
            return entries_to_text(self.list(path))
        if len(parts) >= 2 and parts[0] == "agents" and parts[1] in self._AGENTS:
            agent = parts[1]
            return "\n".join(
                [
                    f"# {agent}",
                    "",
                    "- role: coding-agent context view",
                    f"- current_agent: {self.workspace.agent_id}",
                    "",
                    "## Useful Paths",
                    f"- /agents/{agent}/sessions",
                    f"- /agents/{agent}/learnings",
                    "- /learnings/promoted",
                    "- /handoff/latest.md",
                ]
            )
        raise DheeFSNotFoundError(path)


class SharedMount(DheeMount):
    prefix = "/shared"

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["shared"]:
            return [
                DheeFSEntry(name="inbox/", path="/shared/inbox", kind="dir"),
                DheeFSEntry(name="broadcasts/", path="/shared/broadcasts", kind="dir"),
                DheeFSEntry(name="task-results/", path="/shared/task-results", kind="dir"),
            ]
        raise DheeFSNotFoundError(path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        if parts == ["shared"]:
            return entries_to_text(self.list(path))
        if parts in (["shared", "task-results"], ["shared", "task-results", "latest.json"]):
            return _json(self.workspace.shared_snapshot())
        if parts in (["shared", "inbox"], ["shared", "broadcasts"]):
            return _json(self.workspace.inbox(mark_read=False))
        raise DheeFSNotFoundError(path)


class SourceRootMount(DheeMount):
    prefix = "/sources"

    _MEMORY_FILES = {
        "recent.md": "recent",
        "canonical.md": "canonical",
        "passive-screen.md": "passive-screen",
        "test.md": "test",
    }

    def list(self, path: str) -> List[DheeFSEntry]:
        parts = _parts(path)
        if parts == ["sources"]:
            entries: List[DheeFSEntry] = []
            if getattr(self.workspace, "db", None) is not None:
                entries.append(DheeFSEntry(name="memory/", path="/sources/memory", kind="dir"))
            for source in self.workspace.context_sources:
                entries.append(DheeFSEntry(name=f"{source.name}/", path=f"/sources/{source.name}", kind="dir"))
            return entries
        if parts == ["sources", "memory"]:
            return [
                DheeFSEntry(name=name, path=f"/sources/memory/{name}")
                for name in self._MEMORY_FILES
            ]
        source, source_path = self._resolve_source(path)
        return source.list(source_path)

    def read(self, path: str) -> str:
        parts = _parts(path)
        if parts == ["sources"]:
            entries = self.list(path)
            if not entries:
                return "# Context Sources\n\nNo context source mounts are registered."
            return entries_to_text(entries)
        if parts == ["sources", "memory"]:
            return entries_to_text(self.list(path))
        if len(parts) == 3 and parts[:2] == ["sources", "memory"]:
            view = self._MEMORY_FILES.get(parts[2])
            if not view:
                raise DheeFSNotFoundError(path)
            return self._render_memory_source(view=view)
        source, source_path = self._resolve_source(path)
        return source.read(source_path)

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        parts = _parts(path)
        if parts == ["sources"] or parts[:2] == ["sources", "memory"]:
            view = "recent"
            if len(parts) == 3 and parts[2] in self._MEMORY_FILES:
                view = self._MEMORY_FILES[parts[2]]
            return [
                {"path": f"/sources/memory/{view}.md", **row}
                for row in self.workspace.memory_source_rows(view=view, query=query, limit=50)
            ]
        if len(parts) >= 2:
            source, source_path = self._resolve_source(path)
            return [
                {"path": item.get("path", source_path), **item}
                for item in self._source_search(source, source_path, query)
            ]
        matches: List[Dict[str, Any]] = []
        for source in self.workspace.context_sources:
            try:
                for item in self._source_search(source, f"/sources/{source.name}", query):
                    item_path = item.pop("path", f"/sources/{source.name}/{item.get('id', '')}")
                    matches.append({"path": item_path, **item})
            except Exception:
                continue
        return matches

    def _resolve_source(self, path: str) -> Tuple[Any, str]:
        parts = _parts(path)
        if len(parts) < 2 or parts[0] != "sources":
            raise DheeFSNotFoundError(path)
        name = parts[1]
        for source in self.workspace.context_sources:
            if getattr(source, "name", None) == name:
                return source, path
        raise DheeFSNotFoundError(f"unknown context source: {name}")

    def _render_memory_source(self, *, view: str) -> str:
        rows = self.workspace.memory_source_rows(view=view, limit=80)
        title = {
            "recent": "Recent Dhee Memory Sources",
            "canonical": "Canonical Personal Memory Sources",
            "passive-screen": "Passive Screen Observation Sources",
            "test": "Isolated Test Memory Sources",
        }.get(view, "Dhee Memory Sources")
        lines = [f"# {title}", ""]
        if not rows:
            lines.append("No matching memory sources.")
            return "\n".join(lines)
        for row in rows:
            lines.extend(
                [
                    f"## {row.get('id')}",
                    f"- class: {row.get('memory_class')}",
                    f"- kind: {row.get('canonical_kind') or row.get('memory_type') or ''}",
                    f"- namespace: {row.get('namespace')}",
                    f"- source: {row.get('source_type') or ''} / {row.get('source_app') or ''}",
                    f"- confidence: {row.get('confidence') if row.get('confidence') is not None else ''}",
                    f"- strength: {row.get('strength') if row.get('strength') is not None else ''}",
                    f"- created_at: {row.get('created_at') or ''}",
                    "",
                    str(row.get("memory") or "").replace("\n", " ")[:500],
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    def _source_search(self, source: Any, path: str, query: str) -> List[Dict[str, Any]]:
        try:
            rows = source.search(path, query)
        except TypeError:
            rows = source.search(query)
        out: List[Dict[str, Any]] = []
        for row in rows or []:
            if hasattr(row, "to_dict"):
                out.append(row.to_dict())
            elif isinstance(row, dict):
                out.append(dict(row))
        return out
