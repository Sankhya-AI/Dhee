"""Native Hermes MemoryProvider backed by Dhee."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - exercised only inside Hermes.
    from agent.memory_provider import MemoryProvider
except Exception:  # pragma: no cover - local tests do not install Hermes.
    class MemoryProvider:  # type: ignore[no-redef]
        """Fallback base class for contract tests outside Hermes."""


logger = logging.getLogger(__name__)


class DheeHermesMemoryProvider(MemoryProvider):
    """Hermes provider that mirrors memories and promotes gated learnings via Dhee."""

    @property
    def name(self) -> str:
        return "dhee"

    def __init__(self) -> None:
        self._plugin = None
        self._exchange = None
        self._session_id = ""
        self._hermes_home = ""
        self._platform = ""
        self._user_id = "default"
        self._agent_id = "hermes"
        self._repo: Optional[str] = None
        self._prefetch_cache: Dict[str, str] = {}
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._turns: List[Dict[str, str]] = []

    # ------------------------------------------------------------------
    # Hermes MemoryProvider contract
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Dhee is local-first and can activate without network calls."""
        try:
            import dhee  # noqa: F401
        except Exception:
            return False
        return True

    def initialize(self, session_id: str = "", **kwargs) -> None:
        from dhee import DheePlugin
        from dhee.core.learnings import LearningExchange

        config = self._load_config(kwargs.get("hermes_home"))
        config.update({k: v for k, v in kwargs.items() if v is not None})

        self._session_id = str(session_id or config.get("session_id") or "")
        self._hermes_home = str(config.get("hermes_home") or Path.home() / ".hermes")
        self._platform = str(config.get("platform") or "hermes")
        self._user_id = str(config.get("user_id") or os.environ.get("DHEE_USER_ID") or "default")
        self._agent_id = str(
            config.get("agent_identity")
            or config.get("agent_id")
            or os.environ.get("DHEE_AGENT_ID")
            or "hermes"
        )
        self._repo = _normalise_repo(config.get("repo") or config.get("agent_workspace"))

        data_dir = config.get("dhee_data_dir") or os.environ.get("DHEE_DATA_DIR")
        offline = bool(config.get("offline", False))
        provider = config.get("provider")
        self._plugin = DheePlugin(
            data_dir=data_dir,
            provider=provider,
            user_id=self._user_id,
            in_memory=bool(config.get("in_memory", False)),
            offline=offline,
        )
        self._exchange = LearningExchange(Path(self._plugin.data_dir) / "learnings")

        if bool(config.get("sync_on_start", False)):
            self._exchange.import_hermes_home(
                self._hermes_home,
                user_id=self._user_id,
                source_agent_id=self._agent_id,
                repo=self._repo,
                dry_run=False,
            )

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "dhee_data_dir",
                "description": "Dhee data directory",
                "default": os.environ.get("DHEE_DATA_DIR") or str(Path.home() / ".dhee"),
            },
            {
                "key": "offline",
                "description": "Use Dhee's offline mock provider",
                "default": False,
            },
            {
                "key": "sync_on_start",
                "description": "Import Hermes memories/agent-created skills as candidates at startup",
                "default": False,
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        path = Path(hermes_home).expanduser() / "dhee.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(values or {}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def system_prompt_block(self) -> str:
        return (
            "Dhee is active as the Hermes memory provider. It mirrors Hermes memory writes, "
            "stores learning candidates for audit, and injects only promoted Dhee learnings."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        key = session_id or self._session_id or "default"
        with self._lock:
            cached = self._prefetch_cache.pop(key, "")
        if cached:
            return cached
        return self._build_prefetch(query)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        key = session_id or self._session_id or "default"

        def _run() -> None:
            try:
                block = self._build_prefetch(query)
                with self._lock:
                    self._prefetch_cache[key] = block
            except Exception as exc:
                logger.warning("Dhee Hermes prefetch failed: %s", exc)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True)
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        sid = session_id or self._session_id

        def _sync() -> None:
            try:
                self._turns.append({"user": user_content or "", "assistant": assistant_content or ""})
                if self._plugin and (user_content or assistant_content):
                    self._plugin.remember(
                        _compact_turn(user_content, assistant_content),
                        user_id=self._user_id,
                        metadata={
                            "source": "hermes_sync_turn",
                            "session_id": sid,
                            "platform": self._platform,
                            "agent_id": self._agent_id,
                        },
                    )
            except Exception as exc:
                logger.warning("Dhee Hermes turn sync failed: %s", exc)

        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=0.1)
        self._sync_thread = threading.Thread(target=_sync, daemon=True)
        self._sync_thread.start()

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._plugin or not content:
            return
        meta = dict(metadata or {})
        meta.update({
            "source": "hermes_memory_write",
            "action": action,
            "target": target,
            "session_id": meta.get("session_id") or self._session_id,
            "platform": self._platform,
            "agent_id": self._agent_id,
        })
        try:
            self._plugin.remember(
                f"Hermes {target} {action}: {content}",
                user_id=self._user_id,
                metadata=meta,
            )
        except Exception as exc:
            logger.warning("Dhee Hermes memory mirror failed: %s", exc)

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        return self._session_learning_candidate(messages, create=False)

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        self._session_learning_candidate(messages, create=True)
        if self._plugin:
            try:
                self._plugin.checkpoint(
                    summary=_summarise_messages(messages),
                    task_type="hermes_session",
                    status="completed",
                    repo=self._repo,
                    user_id=self._user_id,
                    agent_id=self._agent_id,
                )
            except Exception as exc:
                logger.warning("Dhee Hermes checkpoint failed: %s", exc)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = str(new_session_id or "")
        if reset:
            self._turns = []
            with self._lock:
                self._prefetch_cache.clear()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "dhee_remember",
                "description": "Store a fact or observation in Dhee memory.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}, "metadata": {"type": "object"}},
                    "required": ["content"],
                },
            },
            {
                "name": "dhee_search",
                "description": "Search Dhee memory for relevant context.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                },
            },
            {
                "name": "dhee_submit_learning",
                "description": "Submit a Dhee learning candidate. It is not injected until promoted.",
                "parameters": _learning_parameters(required=["title", "body"]),
            },
            {
                "name": "dhee_search_learnings",
                "description": "Search promoted Dhee learnings, or candidates when explicitly requested.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "task_type": {"type": "string"},
                        "status": {"type": "string", "enum": ["candidate", "promoted", "rejected", "archived"]},
                        "include_candidates": {"type": "boolean"},
                        "limit": {"type": "integer"},
                    },
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            payload = self._handle_tool_call(tool_name, args or {})
        except Exception as exc:
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        return json.dumps(payload, indent=2, sort_keys=True)

    def shutdown(self) -> None:
        for thread in (self._prefetch_thread, self._sync_thread):
            if thread and thread.is_alive():
                thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _handle_tool_call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "dhee_remember":
            if not self._plugin:
                return {"error": "provider_not_initialized"}
            return self._plugin.remember(
                args.get("content", ""),
                user_id=self._user_id,
                metadata=args.get("metadata"),
            )
        if tool_name == "dhee_search":
            if not self._plugin:
                return {"error": "provider_not_initialized"}
            return {
                "results": self._plugin.recall(
                    args.get("query", ""),
                    user_id=self._user_id,
                    limit=max(1, min(20, int(args.get("limit", 5) or 5))),
                )
            }
        if tool_name == "dhee_submit_learning":
            exchange = self._require_exchange()
            item = exchange.submit(
                title=args.get("title", ""),
                body=args.get("body", ""),
                kind=args.get("kind", "heuristic"),
                source_agent_id=args.get("source_agent_id") or self._agent_id,
                source_harness="hermes",
                task_type=args.get("task_type"),
                repo=args.get("repo") or self._repo,
                scope=args.get("scope", "personal"),
                confidence=float(args.get("confidence", 0.5) or 0.5),
                utility=float(args.get("utility", 0.0) or 0.0),
                evidence=args.get("evidence") or [],
                metadata={"session_id": self._session_id, "platform": self._platform},
            )
            return {"learning": item.to_dict()}
        if tool_name == "dhee_search_learnings":
            exchange = self._require_exchange()
            return {
                "results": exchange.search(
                    query=args.get("query", ""),
                    task_type=args.get("task_type"),
                    repo=args.get("repo") or self._repo,
                    status=args.get("status", "promoted"),
                    include_candidates=bool(args.get("include_candidates", False)),
                    limit=max(1, min(20, int(args.get("limit", 5) or 5))),
                )
            }
        return {"error": f"unknown_tool:{tool_name}"}

    def _build_prefetch(self, query: str) -> str:
        parts: List[str] = []
        exchange = self._exchange
        if exchange:
            block = exchange.context_block(query=query, repo=self._repo, limit=5)
            if block:
                parts.append(block)
        if self._plugin and query:
            try:
                memories = self._plugin.recall(query, user_id=self._user_id, limit=3)
                if memories:
                    parts.append("### Relevant Dhee Memories")
                    for row in memories:
                        memory = str(row.get("memory") or "").strip()
                        if memory:
                            parts.append(f"- {memory[:300]}")
            except Exception as exc:
                logger.debug("Dhee Hermes recall failed: %s", exc, exc_info=True)
        return "\n".join(parts)

    def _session_learning_candidate(self, messages: List[Dict[str, Any]], create: bool) -> str:
        summary = _summarise_messages(messages or self._turns)
        if not summary:
            return ""
        block = (
            "Dhee observed this Hermes session. Preserve reusable tactics, outcomes, "
            f"and stable user preferences when compressing:\n{summary}"
        )
        if create and self._exchange:
            try:
                self._exchange.submit(
                    title=f"Hermes session learning {time.strftime('%Y-%m-%d')}",
                    body=summary,
                    kind="workflow",
                    source_agent_id=self._agent_id,
                    source_harness="hermes",
                    task_type="hermes_session",
                    repo=self._repo,
                    confidence=0.5,
                    utility=0.0,
                    evidence=[{"kind": "session_end", "session_id": self._session_id}],
                )
            except Exception as exc:
                logger.warning("Dhee Hermes learning extraction failed: %s", exc)
        return block

    def _require_exchange(self):
        if self._exchange is None:
            raise RuntimeError("provider_not_initialized")
        return self._exchange

    def _load_config(self, hermes_home: Optional[str]) -> Dict[str, Any]:
        home = Path(hermes_home or Path.home() / ".hermes").expanduser()
        path = home / "dhee.json"
        if not path.exists():
            return {"hermes_home": str(home)}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("hermes_home", str(home))
        return data


def register(ctx) -> None:
    """Hermes plugin discovery entry point."""
    ctx.register_memory_provider(DheeHermesMemoryProvider())


def _learning_parameters(required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
            "kind": {"type": "string", "enum": ["skill", "heuristic", "policy", "contrast", "memory", "workflow", "playbook"]},
            "task_type": {"type": "string"},
            "repo": {"type": "string"},
            "scope": {"type": "string", "enum": ["personal", "repo", "workspace"]},
            "confidence": {"type": "number"},
            "utility": {"type": "number"},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "source_agent_id": {"type": "string"},
        },
        "required": required or [],
    }


def _compact_turn(user_content: str, assistant_content: str) -> str:
    user = " ".join(str(user_content or "").split())
    assistant = " ".join(str(assistant_content or "").split())
    if len(user) > 500:
        user = user[:499] + "..."
    if len(assistant) > 800:
        assistant = assistant[:799] + "..."
    return f"Hermes turn\nUser: {user}\nAssistant: {assistant}"


def _summarise_messages(messages: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for message in messages[-12:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or message.get("speaker") or "message")
        content = str(message.get("content") or message.get("text") or "").strip()
        if not content:
            continue
        content = " ".join(content.split())
        if len(content) > 400:
            content = content[:399] + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _normalise_repo(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.exists():
        return str(path.resolve())
    return text
