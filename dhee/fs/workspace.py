"""DheeFS virtual workspace and command dispatcher."""

from __future__ import annotations

import math
import os
import re
import shlex
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from dhee.core.learnings import LearningExchange
from dhee.fs.mounts import (
    AgentsMount,
    ArtifactMount,
    HandoffMount,
    LearningMount,
    RepoMount,
    RouterMount,
    SessionsMount,
    SharedMount,
    SourceRootMount,
)
from dhee.fs.types import (
    DheeFSCommandError,
    DheeFSEntry,
    DheeFSError,
    DheeFSNotFoundError,
    DheeFSResult,
    DheeMount,
    entries_to_text,
)


_PTR_PATTERN = re.compile(r"\b[A-Z]-[0-9a-f]{10}\b")


class CommandRegistry:
    """Approved DheeFS command surface."""

    def __init__(self, workspace: "ContextWorkspace"):
        self.workspace = workspace
        self.handlers: Dict[str, Callable[[List[str], str], DheeFSResult]] = {
            "ls": self._ls,
            "cat": self._cat,
            "grep": self._grep,
            "why": self._why,
            "promote": self._promote,
            "reject": self._reject,
            "broadcast": self._broadcast,
            "provision": self._provision,
            "snapshot": self._snapshot,
        }

    @property
    def commands(self) -> List[str]:
        return sorted(self.handlers)

    def execute(self, argv: List[str], raw_command: str) -> DheeFSResult:
        if not argv:
            raise DheeFSCommandError("empty command")
        name = argv[0]
        handler = self.handlers.get(name)
        if not handler:
            raise DheeFSCommandError(
                f"unsupported command: {name}. Supported commands: {', '.join(self.commands)}"
            )
        return handler(argv[1:], raw_command)

    def _ls(self, args: List[str], raw: str) -> DheeFSResult:
        if len(args) > 1:
            raise DheeFSCommandError("ls accepts at most one path")
        path = args[0] if args else "/"
        entries = self.workspace.list(path)
        return DheeFSResult(
            command=raw,
            stdout=entries_to_text(entries),
            data={"path": self.workspace.normalize_path(path), "entries": [entry.to_dict() for entry in entries]},
        )

    def _cat(self, args: List[str], raw: str) -> DheeFSResult:
        if not args:
            raise DheeFSCommandError("cat requires a path")
        if len(args) > 1:
            raise DheeFSCommandError("cat accepts exactly one path")
        path = args[0]
        text = self.workspace.read(path)
        return DheeFSResult(command=raw, stdout=text, data={"path": self.workspace.normalize_path(path)})

    def _grep(self, args: List[str], raw: str) -> DheeFSResult:
        if len(args) < 2:
            raise DheeFSCommandError("grep requires a query and a path")
        if len(args) > 2:
            raise DheeFSCommandError("grep accepts exactly one query and one path; quote multi-word queries")
        query, path = args[0], args[1]
        matches = self.workspace.search(path, query)
        lines = []
        for match in matches:
            where = match.get("path", self.workspace.normalize_path(path))
            if "line" in match:
                lines.append(f"{where}:{match['line']}: {match.get('text', '')}")
            elif match.get("title"):
                lines.append(f"{where}: {match['title']}")
            elif match.get("text"):
                lines.append(f"{where}: {match['text']}")
            else:
                lines.append(f"{where}: {match.get('snippet', '')}")
        return DheeFSResult(
            command=raw,
            stdout="\n".join(lines),
            data={"path": self.workspace.normalize_path(path), "query": query, "matches": matches, "count": len(matches)},
        )

    def _why(self, args: List[str], raw: str) -> DheeFSResult:
        if not args:
            raise DheeFSCommandError("why requires a path")
        if len(args) > 1:
            raise DheeFSCommandError("why accepts exactly one path")
        path = args[0]
        text = self.workspace.explain(path)
        return DheeFSResult(command=raw, stdout=text, data={"path": self.workspace.normalize_path(path)})

    def _promote(self, args: List[str], raw: str) -> DheeFSResult:
        flags, positional = _split_flags(args)
        unknown = set(flags) - {"scope"}
        if unknown:
            raise DheeFSCommandError(f"unsupported promote option(s): {', '.join(sorted(unknown))}")
        if not positional:
            raise DheeFSCommandError("promote requires a learning id or /learnings/candidates/<id>.md path")
        if len(positional) > 1:
            raise DheeFSCommandError("promote accepts exactly one learning id or path")
        data = self.workspace.promote_learning(positional[0], scope=flags.get("scope", "personal"))
        learning = data.get("learning") or {}
        stdout = f"promoted {learning.get('id')} -> /learnings/promoted/{learning.get('id')}.md"
        return DheeFSResult(command=raw, stdout=stdout, data=data)

    def _reject(self, args: List[str], raw: str) -> DheeFSResult:
        if not args:
            raise DheeFSCommandError("reject requires a learning id or /learnings/candidates/<id>.md path")
        target = args[0]
        reason = " ".join(args[1:]).strip() or None
        data = self.workspace.reject_learning(target, reason=reason)
        learning = data.get("learning") or {}
        stdout = f"rejected {learning.get('id')} -> /learnings/rejected/{learning.get('id')}.md"
        return DheeFSResult(command=raw, stdout=stdout, data=data)

    def _broadcast(self, args: List[str], raw: str) -> DheeFSResult:
        if not args:
            raise DheeFSCommandError("broadcast requires a message")
        message = " ".join(args).strip()
        data = self.workspace.broadcast(message)
        stdout = "broadcast published" if data.get("ok") else data.get("error", "broadcast failed")
        return DheeFSResult(command=raw, stdout=stdout, data=data, exit_code=0 if data.get("ok") else 1)

    def _provision(self, args: List[str], raw: str) -> DheeFSResult:
        query = " ".join(args).strip()
        data = self.workspace.provision(query)
        stdout = "\n".join(
            [
                f"Estimated raw tokens: {data['estimated_raw_tokens']}",
                f"Estimated routed tokens: {data['estimated_routed_tokens']}",
                f"Pointer count: {data['pointer_count']}",
                f"Candidate learnings: {data['candidate_learning_count']}",
                f"Candidate artifacts: {data['candidate_artifact_count']}",
                f"Risk: {data['risk']}",
            ]
        )
        return DheeFSResult(command=raw, stdout=stdout, data=data)

    def _snapshot(self, args: List[str], raw: str) -> DheeFSResult:
        if args:
            raise DheeFSCommandError("snapshot does not accept arguments yet")
        data = self.workspace.snapshot_manifest()
        return DheeFSResult(command=raw, stdout=self.workspace.json(data), data=data)


class ContextWorkspace:
    """Virtual learning/context space for coding agents."""

    def __init__(
        self,
        *,
        repo: Optional[str] = None,
        user_id: str = "default",
        agent_id: Optional[str] = None,
        db: Any = None,
        learning_exchange: Optional[LearningExchange] = None,
        context_sources: Optional[Iterable[Any]] = None,
        workspace_id: Optional[str] = None,
    ):
        self.repo = os.path.abspath(os.path.expanduser(repo)) if repo else None
        self.user_id = str(user_id or "default")
        self.agent_id = str(agent_id or os.environ.get("DHEE_AGENT_ID") or "dheefs")
        self.db = db
        self.learning_exchange = learning_exchange or LearningExchange()
        self.workspace_id = workspace_id or self.repo
        self.context_sources = list(context_sources or [])
        self.command_registry = CommandRegistry(self)
        builtin_mounts: List[DheeMount] = [
            LearningMount(self),
            HandoffMount(self),
            RouterMount(self),
            ArtifactMount(self),
            SessionsMount(self),
            RepoMount(self),
            AgentsMount(self),
            SharedMount(self),
            SourceRootMount(self),
        ]
        source_mounts = [source for source in self.context_sources if isinstance(source, DheeMount)]
        for source in source_mounts:
            source.workspace = self
        self.mounts = builtin_mounts + source_mounts

    def execute(self, command: str) -> DheeFSResult:
        raw = str(command or "").strip()
        try:
            argv = shlex.split(raw)
            result = self.command_registry.execute(argv, raw)
            return result
        except DheeFSError as exc:
            return DheeFSResult(command=raw, stderr=str(exc), stdout=str(exc), exit_code=2, data={"error": str(exc)})
        except ValueError as exc:
            return DheeFSResult(command=raw, stderr=str(exc), stdout=str(exc), exit_code=2, data={"error": str(exc)})
        except Exception as exc:
            return DheeFSResult(
                command=raw,
                stderr=f"{type(exc).__name__}: {exc}",
                stdout=f"{type(exc).__name__}: {exc}",
                exit_code=1,
                data={"error": str(exc), "error_type": type(exc).__name__},
            )

    def normalize_path(self, path: str) -> str:
        value = str(path or "/").strip()
        if not value:
            value = "/"
        if not value.startswith("/"):
            value = "/" + value
        value = re.sub(r"/+", "/", value)
        if value == "/dhee":
            return "/"
        if value.startswith("/dhee/"):
            value = value[5:]
        if len(value) > 1:
            value = value.rstrip("/")
        return value or "/"

    def list(self, path: str = "/") -> List[DheeFSEntry]:
        path = self.normalize_path(path)
        if path == "/":
            return [
                DheeFSEntry(name=f"{mount.prefix.strip('/')}/", path=mount.prefix, kind="dir")
                for mount in sorted(self.mounts, key=lambda item: item.prefix)
            ]
        mount = self.resolve_mount(path)
        return mount.list(path)

    def read(self, path: str) -> str:
        path = self.normalize_path(path)
        if path == "/":
            return entries_to_text(self.list(path))
        return self.resolve_mount(path).read(path)

    def search(self, path: str, query: str) -> List[Dict[str, Any]]:
        path = self.normalize_path(path)
        if path == "/":
            matches: List[Dict[str, Any]] = []
            for mount in self.mounts:
                try:
                    matches.extend(mount.search(mount.prefix, query))
                except Exception:
                    continue
            return matches
        return self.resolve_mount(path).search(path, query)

    def explain(self, path: str) -> str:
        path = self.normalize_path(path)
        return self.resolve_mount(path).explain(path)

    def resolve_mount(self, path: str) -> DheeMount:
        path = self.normalize_path(path)
        candidates = [
            mount
            for mount in self.mounts
            if path == mount.prefix or path.startswith(mount.prefix.rstrip("/") + "/")
        ]
        if not candidates:
            raise DheeFSNotFoundError(f"unknown path: {path}")
        return sorted(candidates, key=lambda mount: len(mount.prefix), reverse=True)[0]

    def promote_learning(self, path_or_id: str, *, scope: str = "personal") -> Dict[str, Any]:
        mount = self.resolve_mount("/learnings")
        if not isinstance(mount, LearningMount):
            raise DheeFSCommandError("learning mount unavailable")
        target = self.normalize_path(path_or_id) if str(path_or_id or "").startswith("/") else path_or_id
        return mount.promote(target, scope=scope, repo=self.repo)

    def reject_learning(self, path_or_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        mount = self.resolve_mount("/learnings")
        if not isinstance(mount, LearningMount):
            raise DheeFSCommandError("learning mount unavailable")
        target = self.normalize_path(path_or_id) if str(path_or_id or "").startswith("/") else path_or_id
        return mount.reject(target, reason=reason)

    def broadcast(self, message: str) -> Dict[str, Any]:
        if not self.db:
            return {"ok": False, "error": "broadcast requires a Dhee database handle"}
        from dhee.core.live_context import broadcast_live_context

        return broadcast_live_context(
            self.db,
            user_id=self.user_id,
            body=message,
            title="DheeFS broadcast",
            repo=self.repo,
            cwd=self.repo,
            workspace_id=self.workspace_id,
            metadata={"source": "dheefs"},
            agent_id=self.agent_id,
            harness=self.agent_id,
        )

    def handoff_snapshot(self) -> Dict[str, Any]:
        if not self.db:
            return {"error": "handoff snapshot requires a Dhee database handle", "repo": self.repo}
        from dhee.core.handoff_snapshot import build_handoff_snapshot

        try:
            return build_handoff_snapshot(
                self.db,
                user_id=self.user_id,
                repo=self.repo,
                workspace_id=self.workspace_id,
            )
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}", "repo": self.repo}

    def shared_snapshot(self) -> Dict[str, Any]:
        if not self.db:
            return {"task": None, "results": [], "error": "shared snapshot requires a Dhee database handle"}
        from dhee.core.shared_tasks import shared_task_snapshot

        try:
            return shared_task_snapshot(
                self.db,
                user_id=self.user_id,
                repo=self.repo,
                workspace_id=self.workspace_id,
                limit=20,
            )
        except Exception as exc:
            return {"task": None, "results": [], "error": f"{type(exc).__name__}: {exc}"}

    def inbox(self, *, mark_read: bool = False) -> Dict[str, Any]:
        if not self.db:
            return {"messages": [], "error": "inbox requires a Dhee database handle"}
        from dhee.core.live_context import live_context_inbox

        try:
            return live_context_inbox(
                self.db,
                user_id=self.user_id,
                repo=self.repo,
                cwd=self.repo,
                workspace_id=self.workspace_id,
                consumer_id=f"dheefs:{self.agent_id}",
                agent_id=self.agent_id,
                harness=self.agent_id,
                mark_read=mark_read,
                include_own=True,
                limit=20,
            )
        except Exception as exc:
            return {"messages": [], "error": f"{type(exc).__name__}: {exc}"}

    def list_artifacts(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.db or not hasattr(self.db, "list_artifacts"):
            return []
        return self.db.list_artifacts(user_id=self.user_id, workspace_id=self.workspace_id, limit=limit)

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        if not self.db or not hasattr(self.db, "get_artifact"):
            return None
        return self.db.get_artifact(artifact_id)

    def provision(self, query: str) -> Dict[str, Any]:
        text = str(query or "")
        raw_tokens = max(1, math.ceil(len(text) / 4))
        pointers = _PTR_PATTERN.findall(text)
        learning_rows = self.learning_exchange.search(
            query=text,
            repo=self.repo,
            status="promoted",
            include_candidates=True,
            limit=10,
        )
        artifact_rows = self.list_artifacts(limit=50)
        artifact_matches = 0
        if text:
            needle = text.lower()
            for row in artifact_rows:
                haystack = " ".join(str(row.get(k) or "") for k in ("artifact_id", "filename", "mime_type")).lower()
                if any(token in haystack for token in needle.split()):
                    artifact_matches += 1
        routed_tokens = max(80, min(raw_tokens, 120 + len(learning_rows) * 120 + artifact_matches * 80 + len(pointers) * 40))
        risk = "low"
        if raw_tokens > 12000 or len(pointers) > 3:
            risk = "medium"
        if raw_tokens > 50000 or len(pointers) > 20:
            risk = "high"
        return {
            "format": "dheefs_provision",
            "version": "0",
            "query": text,
            "estimated_raw_tokens": raw_tokens,
            "estimated_routed_tokens": routed_tokens,
            "pointer_count": len(pointers),
            "pointers": pointers[:20],
            "candidate_learning_count": len(learning_rows),
            "candidate_learnings": learning_rows,
            "candidate_artifact_count": artifact_matches if text else len(artifact_rows),
            "risk": risk,
            "will_change_files_or_memories": False,
        }

    def snapshot_manifest(self) -> Dict[str, Any]:
        return {
            "format": "dheefs_snapshot_manifest",
            "version": "0",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "repo": self.repo,
            "mounts": [mount.prefix for mount in self.mounts],
            "learning_counts": {
                status: len(self.learning_exchange.list(status=status))
                for status in ("candidate", "promoted", "rejected", "archived")
            },
            "artifact_count": len(self.list_artifacts(limit=100)),
            "handoff": self.handoff_snapshot(),
            "context_sources": [getattr(source, "name", "") for source in self.context_sources],
        }

    @staticmethod
    def json(data: Any) -> str:
        import json

        return json.dumps(data, indent=2, sort_keys=True, default=str)


def _split_flags(args: List[str]) -> Tuple[Dict[str, str], List[str]]:
    flags: Dict[str, str] = {}
    positional: List[str] = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token.startswith("--") and "=" in token:
            key, value = token[2:].split("=", 1)
            flags[key.replace("-", "_")] = value
        elif token.startswith("--"):
            key = token[2:].replace("-", "_")
            if idx + 1 >= len(args):
                raise DheeFSCommandError(f"missing value for --{key.replace('_', '-')}")
            idx += 1
            flags[key] = args[idx]
        else:
            positional.append(token)
        idx += 1
    return flags, positional
