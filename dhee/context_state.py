"""Compiled working state for coding-agent sessions.

The transcript is an audit log. This module keeps the compact state the
next model turn should actually see: goal, canonical facts, active
decisions, current plan, next action, active files, test status, and
pointer-backed evidence.

Storage is local-first under ``~/.dhee/context_state`` (or
``DHEE_DATA_DIR/context_state``). Raw tool output never lives here; the
state points at router ptrs and stores short labels, hashes, and token
estimates for liability accounting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set
from xml.sax.saxutils import escape as _xml_escape


CHARS_PER_TOKEN = 3.5
STATE_CARD_TARGET_TOKENS = 800
COMPILED_PREFIX_TOKENS = 6000
BURN_TARGET_TOKENS = 30000
BURN_WARNING_TOKENS = 45000
BURN_ROLLOVER_TOKENS = 60000
EXPANSION_HEALTHY = 0.05
EXPANSION_WARNING = 0.15
EXPANSION_CRITICAL = 0.30
CACHE_ONE_HOUR = "one_hour_stable"
CACHE_FIVE_MINUTE = "five_minute_stable"
CACHE_VOLATILE = "volatile"
CACHE_TIERS = {CACHE_ONE_HOUR, CACHE_FIVE_MINUTE, CACHE_VOLATILE}

_MAX_FACTS = 24
_MAX_DECISIONS = 24
_MAX_EVIDENCE = 32
_MAX_ACTIVE_FILES = 24
_MAX_OPEN_QUESTIONS = 16
_MAX_PLAN_STEPS = 12
_MAX_LEDGER_READ = 500
_MAX_GOAL_HISTORY = 12
_MAX_STALE_FACTS = 12
_MAX_ADMISSION_RECEIPTS = 64
_MAX_ASSERTIONS = 48
_MAX_ROLLOVER_RECEIPTS = 12
_MAX_LEDGER_WARNINGS = 32
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]{4,}")
_FILE_REF_RE = re.compile(r"\b([A-Za-z0-9_./-]+\.(?:py|js|jsx|ts|tsx|go|rs|java|rb|php|md|toml|yaml|yml|json))(?::(\d+))?")
_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "build",
    "check",
    "code",
    "does",
    "done",
    "from",
    "have",
    "into",
    "just",
    "make",
    "need",
    "please",
    "repo",
    "should",
    "that",
    "their",
    "there",
    "this",
    "what",
    "when",
    "with",
    "work",
    "would",
    "your",
}
_PIVOT_ACTION_TERMS = {
    "add",
    "analyse",
    "analyze",
    "audit",
    "build",
    "debug",
    "deploy",
    "design",
    "fix",
    "harden",
    "implement",
    "install",
    "publish",
    "refactor",
    "release",
    "ship",
    "strengthen",
    "test",
    "verify",
}
_CONTINUATION_TERMS = {"continue", "resume", "status", "where", "done", "again", "same"}


def estimate_tokens(text: str) -> int:
    return max(0, int(len(str(text or "")) / CHARS_PER_TOKEN))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _data_dir() -> Path:
    custom = os.environ.get("DHEE_DATA_DIR")
    if custom:
        base = Path(custom).expanduser()
    else:
        try:
            from dhee.configs.base import _dhee_data_dir

            base = Path(_dhee_data_dir())
        except Exception:
            base = Path.home() / ".dhee"
    root = base / "context_state"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _safe_workspace_key(user_id: str, workspace_id: str) -> str:
    seed = f"{user_id}\0{workspace_id}".encode("utf-8", errors="replace")
    return hashlib.sha256(seed).hexdigest()[:32]


def _workspace_id(repo: Optional[str], workspace_id: Optional[str]) -> str:
    if workspace_id:
        return str(workspace_id)
    if repo:
        return os.path.abspath(os.path.expanduser(str(repo)))
    return os.getcwd()


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def _atomic_json_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _short(text: Any, limit: int = 240) -> str:
    value = " ".join(str(text or "").strip().split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def _append_unique(items: List[Any], value: Any, *, limit: int) -> List[Any]:
    if value is None or value == "":
        return items[-limit:]
    if value in items:
        items = [item for item in items if item != value]
    items.append(value)
    return items[-limit:]


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _rel(path: str, repo: Optional[str]) -> str:
    if not path:
        return ""
    try:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if repo:
            root = os.path.abspath(os.path.expanduser(repo))
            if abs_path == root:
                return "."
            if abs_path.startswith(root.rstrip(os.sep) + os.sep):
                return abs_path[len(root.rstrip(os.sep)) + 1 :]
        return abs_path
    except Exception:
        return str(path)


def _terms(text: Any) -> Set[str]:
    tokens = {
        token.lower().strip("./:-")
        for token in _TOKEN_RE.findall(str(text or ""))
        if token and len(token) >= 4
    }
    return {token for token in tokens if token and token not in _STOPWORDS}


def _overlap_ratio(left: Any, right: Any) -> float:
    a = _terms(left)
    b = _terms(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _looks_like_continuation(text: str) -> bool:
    terms = _terms(text)
    if not terms:
        return False
    if len(terms) <= 4 and terms & _CONTINUATION_TERMS:
        return True
    lowered = str(text or "").lower()
    return any(phrase in lowered for phrase in ("where are we", "are we done", "keep going", "continue"))


def _looks_like_test_command(command: str) -> bool:
    value = str(command or "").lower()
    return any(token in value for token in ("pytest", "npm test", "pnpm test", "yarn test", "cargo test", "go test"))


def _extract_file_refs(text: Any, *, limit: int = 8) -> List[str]:
    refs: List[str] = []
    for match in _FILE_REF_RE.finditer(str(text or "")):
        ref = match.group(1)
        if match.group(2):
            ref = f"{ref}:{match.group(2)}"
        if ref not in refs:
            refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def _normalise_cache_tier(value: Any, default: str = CACHE_VOLATILE) -> str:
    tier = str(value or "").strip()
    return tier if tier in CACHE_TIERS else default


def _default_cache_tier(kind: str, source: str = "", metadata: Optional[Dict[str, Any]] = None) -> str:
    metadata = metadata if isinstance(metadata, dict) else {}
    explicit = _normalise_cache_tier(metadata.get("cache_tier"), "")
    if explicit:
        return explicit
    value = str(kind or "").lower()
    source_name = os.path.basename(str(source or metadata.get("source_path") or "")).lower()
    if value in {"tool_schema", "tool_definition", "tool_defs", "mcp_schema", "system_prompt"}:
        return CACHE_ONE_HOUR
    if value in {"doc", "project_doc", "repo_context", "agents_doc"}:
        return CACHE_ONE_HOUR
    if source_name in {"agents.md", "claude.md", "readme.md", "memory.md"}:
        return CACHE_ONE_HOUR
    if value in {"state_card", "compiled_state", "checkpoint", "rollover", "session", "shared", "memory"}:
        return CACHE_FIVE_MINUTE
    if value.startswith("routed_") or value in {"tool_result", "failed_attempt", "assistant_echo", "live_mirror", "edit_mirror", "tool_mirror"}:
        return CACHE_VOLATILE
    return CACHE_FIVE_MINUTE


def _stable_source_key(kind: str, source: str) -> str:
    return f"{str(kind or '').lower()}:{_short(source, 200)}"


@dataclass
class ContextBlock:
    """Candidate context block before admission."""

    kind: str
    text: str = ""
    source: str = ""
    scope: str = "session"
    relevance: Optional[float] = None
    ptr: Optional[str] = None
    content_hash: Optional[str] = None
    cache_tier: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return self.content_hash or _content_hash(f"{self.kind}\0{self.source}\0{self.text}")

    @property
    def tokens(self) -> int:
        explicit = self.metadata.get("token_estimate") if isinstance(self.metadata, dict) else None
        try:
            if explicit is not None:
                return max(0, int(explicit))
        except (TypeError, ValueError):
            pass
        return estimate_tokens(self.text)


@dataclass
class AdmissionResult:
    decision: str
    reason: str
    token_estimate: int
    content_hash: str
    liability_tokens: int
    cache_tier: str = CACHE_VOLATILE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "token_estimate": self.token_estimate,
            "content_hash": self.content_hash,
            "liability_tokens": self.liability_tokens,
            "cache_tier": self.cache_tier,
            "metadata": dict(self.metadata or {}),
        }


class ContextStateStore:
    """Local compiled-state store for one user/workspace."""

    def __init__(
        self,
        *,
        repo: Optional[str] = None,
        workspace_id: Optional[str] = None,
        user_id: str = "default",
        agent_id: str = "dhee",
        data_dir: Optional[Path | str] = None,
    ):
        self.repo = os.path.abspath(os.path.expanduser(repo)) if repo else None
        self.workspace_id = _workspace_id(self.repo, workspace_id)
        self.user_id = str(user_id or "default")
        self.agent_id = str(agent_id or "dhee")
        self.root = Path(data_dir).expanduser() if data_dir else _data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.key = _safe_workspace_key(self.user_id, self.workspace_id)
        self.state_path = self.root / f"{self.key}.json"
        self.audit_path = self.root / f"{self.key}.audit.jsonl"
        self.lock_path = self.root / f"{self.key}.lock"
        self.checkpoint_dir = self.root / f"{self.key}.checkpoints"
        self._lock_depth = 0

    def empty_state(self) -> Dict[str, Any]:
        created = now_iso()
        return {
            "format": "dhee_context_state",
            "version": 1,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "workspace_id": self.workspace_id,
            "repo": self.repo,
            "task_epoch": 1,
            "goal": "",
            "goal_history": [],
            "stale_facts": [],
            "facts": [],
            "decisions": [],
            "current_plan": [],
            "next_action": "",
            "active_files": [],
            "open_questions": [],
            "test_status": "",
            "evidence": [],
            "seen_hashes": {},
            "cache_tier": CACHE_FIVE_MINUTE,
            "admission_receipts": [],
            "assertions": [],
            "rollover_receipts": [],
            "ledger_warnings": [],
            "checkpoint_count": 0,
            "state_revision": 0,
            "last_prompt_hash": "",
            "last_epoch_reason": "",
            "epoch_started_at": created,
            "created_at": created,
            "updated_at": created,
        }

    def load(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self.empty_state()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self.empty_state()
        if not isinstance(data, dict) or data.get("format") != "dhee_context_state":
            return self.empty_state()
        data.setdefault("user_id", self.user_id)
        data.setdefault("workspace_id", self.workspace_id)
        data.setdefault("repo", self.repo)
        data.setdefault("task_epoch", 1)
        data.setdefault("goal_history", [])
        data.setdefault("stale_facts", [])
        data.setdefault("facts", [])
        data.setdefault("decisions", [])
        data.setdefault("current_plan", [])
        data.setdefault("active_files", [])
        data.setdefault("open_questions", [])
        data.setdefault("evidence", [])
        data.setdefault("seen_hashes", {})
        data.setdefault("cache_tier", CACHE_FIVE_MINUTE)
        data.setdefault("admission_receipts", [])
        data.setdefault("assertions", [])
        data.setdefault("rollover_receipts", [])
        data.setdefault("ledger_warnings", [])
        data.setdefault("checkpoint_count", 0)
        data.setdefault("state_revision", 0)
        data.setdefault("last_prompt_hash", "")
        data.setdefault("last_epoch_reason", "")
        data.setdefault("epoch_started_at", data.get("created_at") or now_iso())
        return data

    def save(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state["updated_at"] = now_iso()
        state["agent_id"] = self.agent_id
        try:
            state["state_revision"] = int(state.get("state_revision") or 0) + 1
        except (TypeError, ValueError):
            state["state_revision"] = 1
        _atomic_json_write(self.state_path, state)
        return state

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Best-effort cross-process lock around load-modify-save cycles."""
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        locked = False
        self._lock_depth = 1
        try:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            except Exception:
                locked = False
            yield
        finally:
            try:
                if locked:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self._lock_depth = 0
            handle.close()

    def reset(self) -> Dict[str, Any]:
        with self._locked():
            state = self.empty_state()
            return self.save(state)

    def observe_prompt(self, prompt: str) -> Dict[str, Any]:
        with self._locked():
            state = self.load()
            text = _short(prompt, 360)
            if text and not state.get("goal"):
                state["goal"] = text
                state["last_prompt_hash"] = _content_hash(text)
            elif text and self._should_start_new_epoch(state, text):
                self._start_new_epoch(state, text, reason="user prompt changed the active task")
            elif text:
                state["last_prompt_hash"] = _content_hash(text)
            if text and not state.get("next_action"):
                state["next_action"] = "Answer the current user request using Dhee compiled state."
            self._append_audit({"event": "prompt", "summary": text, "task_epoch": state.get("task_epoch")})
            return self.save(state)

    def ingest_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        with self._locked():
            state = self.load()
            if not isinstance(session, dict):
                return state
            summary = _short(session.get("summary") or session.get("task_summary"), 320)
            if summary and not state.get("goal"):
                state["goal"] = summary
            elif summary:
                self._add_fact_to_state(state, f"Prior session: {summary}", source="session", evidence=[])
            for item in _as_list(session.get("decisions")):
                self._add_decision_to_state(state, str(item), evidence=[], source="session")
            for path in _as_list(session.get("files_touched") or session.get("files")):
                state["active_files"] = _append_unique(
                    _as_list(state.get("active_files")),
                    _rel(str(path), self.repo),
                    limit=_MAX_ACTIVE_FILES,
                )
            todos = _as_list(session.get("todos") or session.get("todos_remaining"))
            if todos:
                state["current_plan"] = [_short(todo, 160) for todo in todos[:_MAX_PLAN_STEPS]]
                state["next_action"] = _short(todos[0], 200)
            return self.save(state)

    def add_fact(self, text: str, *, source: str = "", evidence: Optional[List[str]] = None) -> Dict[str, Any]:
        with self._locked():
            state = self.load()
            self._add_fact_to_state(state, text, source=source, evidence=evidence or [])
            return self.save(state)

    def add_decision(
        self,
        text: str,
        *,
        supersedes: Optional[Iterable[str]] = None,
        reason: str = "",
        evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        with self._locked():
            state = self.load()
            decision = self._add_decision_to_state(state, text, evidence=evidence or [], source="explicit")
            for old_id in supersedes or []:
                self._mark_superseded(state, str(old_id), by=decision["id"], reason=reason)
            return self.save(state)

    def supersede_decision(self, old_id: str, new_text: str, *, reason: str = "") -> Dict[str, Any]:
        with self._locked():
            state = self.load()
            decision = self._add_decision_to_state(state, new_text, evidence=[], source="supersession")
            self._mark_superseded(state, old_id, by=decision["id"], reason=reason)
            return self.save(state)

    def record_tool_event(
        self,
        *,
        tool_name: str,
        success: bool,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_result: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tool_input = tool_input or {}
        metadata = metadata or {}
        failed_block: Optional[ContextBlock] = None
        failed_result: Optional[AdmissionResult] = None
        with self._locked():
            state = self.load()
            source_path = str(
                tool_input.get("file_path")
                or tool_input.get("path")
                or tool_input.get("notebook_path")
                or metadata.get("source_path")
                or ""
            ).strip()
            rel_path = _rel(source_path, self.repo) if source_path else ""
            command = str(tool_input.get("command") or tool_input.get("cmd") or metadata.get("command") or "").strip()
            result_text = _short(tool_result, 360)
            ptr = str(metadata.get("ptr") or "").strip()

            event = {
                "event": "tool",
                "tool_name": tool_name,
                "success": bool(success),
                "source_path": rel_path or source_path,
                "command": command,
                "ptr": ptr,
                "summary": result_text,
                "task_epoch": state.get("task_epoch"),
            }
            self._append_audit(event)

            if rel_path:
                state["active_files"] = _append_unique(_as_list(state.get("active_files")), rel_path, limit=_MAX_ACTIVE_FILES)

            if ptr:
                self._add_evidence_to_state(
                    state,
                    ptr=ptr,
                    kind=str(metadata.get("kind") or tool_name or "tool"),
                    source=rel_path or command or tool_name,
                    summary=result_text or f"{tool_name} evidence",
                )

            if not success:
                failed_block = ContextBlock(
                    kind="failed_attempt",
                    text=result_text or command or tool_name,
                    source=rel_path or command,
                    metadata={"token_estimate": estimate_tokens(str(tool_result or ""))},
                )
                failed_result = AdmissionResult(
                    decision="suppress",
                    reason="failed attempts stay in audit unless they change the next action",
                    token_estimate=estimate_tokens(str(tool_result or "")),
                    content_hash=_content_hash(str(tool_result or command or tool_name)),
                    liability_tokens=0,
                )
                self.save(state)
            else:
                if tool_name in {"Edit", "Write", "MultiEdit", "NotebookEdit"} and rel_path:
                    self._add_fact_to_state(state, f"Edited {rel_path}", source=tool_name, evidence=[ptr] if ptr else [])
                    state["next_action"] = f"Verify the change in {rel_path}."
                elif tool_name == "Read" and rel_path:
                    state["next_action"] = state.get("next_action") or f"Use the relevant slice of {rel_path}."
                elif command and _looks_like_test_command(command):
                    exit_code = metadata.get("exit_code")
                    state["test_status"] = _short(
                        f"{command} exit={exit_code if exit_code is not None else 'unknown'} {result_text}",
                        240,
                    )
                    state["next_action"] = "Act on the current test result."
                saved = self.save(state)
                return saved

        if failed_block and failed_result:
            self.record_admission(failed_block, failed_result)
        return self.load()

    def record_admission(self, block: ContextBlock, result: AdmissionResult) -> None:
        with self._locked():
            state = self.load()
            cache_tier = _normalise_cache_tier(
                result.cache_tier or (result.metadata or {}).get("cache_tier"),
                _default_cache_tier(block.kind, block.source, block.metadata),
            )
            result.cache_tier = cache_tier
            receipt = self._make_admission_receipt(state, block, result, cache_tier=cache_tier)
            seen = state.setdefault("seen_hashes", {})
            seen[result.content_hash] = {
                "decision": result.decision,
                "source": block.source,
                "kind": block.kind,
                "source_key": receipt.get("source_key"),
                "cache_tier": cache_tier,
                "receipt_id": receipt.get("id"),
                "updated_at": now_iso(),
                "task_epoch": state.get("task_epoch"),
            }
            self._append_admission_receipt(state, receipt)
            self._learn_from_admission(state, block, result, receipt_id=str(receipt.get("id") or ""))
            self.save(state)
        self._append_audit(
            {
                "event": "admission",
                "kind": block.kind,
                "source": block.source,
                "decision": result.decision,
                "reason": result.reason,
                "tokens": result.token_estimate,
                "liability_tokens": result.liability_tokens,
                "hash": result.content_hash,
                "receipt_id": receipt.get("id"),
                "cache_tier": cache_tier,
                "ptr": block.ptr,
                "metadata": result.metadata,
                "task_epoch": receipt.get("task_epoch"),
            }
        )

    def _make_admission_receipt(
        self,
        state: Dict[str, Any],
        block: ContextBlock,
        result: AdmissionResult,
        *,
        cache_tier: str,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        if isinstance(block.metadata, dict):
            metadata.update(block.metadata)
        if isinstance(result.metadata, dict):
            metadata.update(result.metadata)
        supersedes = metadata.get("supersedes")
        if isinstance(supersedes, str):
            supersedes_list = [supersedes]
        else:
            supersedes_list = [str(item) for item in _as_list(supersedes) if str(item).strip()]
        assertions = metadata.get("assertions")
        if isinstance(assertions, str):
            assertion_list = [assertions]
        else:
            assertion_list = [str(item) for item in _as_list(assertions) if str(item).strip()]
        source = str(block.source or metadata.get("source_path") or "").strip()
        receipt_seed = json.dumps(
            {
                "hash": result.content_hash,
                "decision": result.decision,
                "kind": block.kind,
                "source": source,
                "task_epoch": state.get("task_epoch"),
            },
            sort_keys=True,
            default=str,
        )
        receipt_id = "AR-" + _content_hash(receipt_seed)[:12]
        warnings: List[Dict[str, Any]] = []
        if assertion_list and not (block.ptr or metadata.get("evidence") or metadata.get("ptr")):
            warning = self._add_ledger_warning_to_state(
                state,
                kind="assertion_without_evidence",
                source=source or block.kind,
                text="Assertion admitted without ptr/evidence backing.",
                receipt_id=receipt_id,
            )
            warnings.append(warning)
        if metadata.get("stable_prefix_mutation"):
            warning = self._add_ledger_warning_to_state(
                state,
                kind="stable_prefix_mutation",
                source=source or block.kind,
                text="One-hour-stable source changed mid-session and was downgraded.",
                receipt_id=receipt_id,
            )
            warnings.append(warning)
        if metadata.get("reread_short_circuit"):
            warning = self._add_ledger_warning_to_state(
                state,
                kind="reread_short_circuit",
                source=source or block.kind,
                text="Repeated raw read suppressed; rollover receipt should be expanded first.",
                receipt_id=receipt_id,
            )
            warnings.append(warning)
        return {
            "id": receipt_id,
            "created_at": now_iso(),
            "task_epoch": state.get("task_epoch"),
            "kind": _short(block.kind, 80),
            "source": _short(source, 220),
            "source_key": _stable_source_key(block.kind, source),
            "ptr": str(block.ptr or metadata.get("ptr") or ""),
            "decision": result.decision,
            "reason": _short(result.reason, 220),
            "content_hash": result.content_hash,
            "cache_tier": cache_tier,
            "token_estimate": result.token_estimate,
            "liability_tokens": result.liability_tokens,
            "supersedes": supersedes_list[:8],
            "assertions": [_short(item, 160) for item in assertion_list[:8]],
            "metadata": {
                key: metadata.get(key)
                for key in (
                    "projected_cache_read_tokens",
                    "relevance",
                    "command",
                    "exit_code",
                    "depth",
                    "task_intent",
                    "reread_short_circuit",
                    "rollover_receipt_id",
                    "stable_prefix_mutation",
                )
                if key in metadata
            },
            "warnings": warnings,
        }

    def _append_admission_receipt(self, state: Dict[str, Any], receipt: Dict[str, Any]) -> None:
        rows = [
            row
            for row in _as_list(state.get("admission_receipts"))
            if isinstance(row, dict) and row.get("id") != receipt.get("id")
        ]
        rows.append(receipt)
        state["admission_receipts"] = rows[-_MAX_ADMISSION_RECEIPTS:]

    def record_echo(self, *, tool_text: str, assistant_text: str, source: str = "") -> Dict[str, Any]:
        report = detect_echo(tool_text, assistant_text)
        if report["is_echo"]:
            block = ContextBlock(
                kind="assistant_echo",
                text=assistant_text,
                source=source,
                metadata={"overlap": report["overlap"], "token_estimate": estimate_tokens(assistant_text)},
            )
            result = AdmissionResult(
                decision="suppress",
                reason="assistant prose repeated the prior tool result",
                token_estimate=block.tokens,
                content_hash=block.hash,
                liability_tokens=0,
                metadata=report,
            )
            self.record_admission(block, result)
        return report

    def render_state_card(self, *, max_tokens: int = STATE_CARD_TARGET_TOKENS) -> str:
        state = self.load()
        card = self._render_state_card(state, fact_limit=6, decision_limit=5, file_limit=8, evidence_limit=5)
        if estimate_tokens(card) <= max_tokens:
            return card
        card = self._render_state_card(state, fact_limit=4, decision_limit=3, file_limit=5, evidence_limit=3)
        if estimate_tokens(card) <= max_tokens:
            return card
        return self._render_state_card(state, fact_limit=2, decision_limit=2, file_limit=3, evidence_limit=2)

    def render_markdown(self) -> str:
        state = self.load()
        lines = [
            "# Dhee Compiled State",
            "",
            f"- workspace_id: {state.get('workspace_id')}",
            f"- repo: {state.get('repo') or ''}",
            f"- task_epoch: {state.get('task_epoch') or 1}",
            f"- state_revision: {state.get('state_revision') or 0}",
            f"- updated_at: {state.get('updated_at')}",
            f"- goal: {state.get('goal') or 'unset'}",
            f"- next_action: {state.get('next_action') or 'unset'}",
            f"- test_status: {state.get('test_status') or 'unset'}",
            "",
            "## Canonical Facts",
        ]
        facts = _as_list(state.get("facts"))
        lines.extend([f"- {row.get('text')}" for row in facts if isinstance(row, dict)] or ["- none"])
        lines.extend(["", "## Active Decisions"])
        active = [row for row in _as_list(state.get("decisions")) if isinstance(row, dict) and row.get("status") == "active"]
        lines.extend([f"- {row.get('id')}: {row.get('text')}" for row in active] or ["- none"])
        lines.extend(["", "## Current Plan"])
        lines.extend([f"- {step}" for step in _as_list(state.get("current_plan"))] or ["- none"])
        lines.extend(["", "## Open Questions"])
        lines.extend([f"- {q}" for q in _as_list(state.get("open_questions"))] or ["- none"])
        lines.extend(["", "## Active Files"])
        lines.extend([f"- {path}" for path in _as_list(state.get("active_files"))] or ["- none"])
        return "\n".join(lines)

    def render_decisions(self, *, superseded: bool = False) -> str:
        state = self.load()
        wanted = "superseded" if superseded else "active"
        rows = [row for row in _as_list(state.get("decisions")) if isinstance(row, dict) and row.get("status") == wanted]
        title = "Superseded Decisions" if superseded else "Active Decisions"
        lines = [f"# {title}", ""]
        if not rows:
            lines.append("No decisions.")
        for row in rows:
            lines.append(f"## {row.get('id')}")
            lines.append("")
            lines.append(str(row.get("text") or ""))
            if superseded:
                lines.append("")
                lines.append(f"- superseded_by: {row.get('superseded_by') or ''}")
                lines.append(f"- reason: {row.get('superseded_reason') or ''}")
                lines.append("")
        return "\n".join(lines).rstrip()

    def render_history(self) -> str:
        state = self.load()
        lines = [
            "# Task Epoch History",
            "",
            f"- current_epoch: {state.get('task_epoch') or 1}",
            f"- current_goal: {state.get('goal') or 'unset'}",
            f"- last_epoch_reason: {state.get('last_epoch_reason') or ''}",
            "",
            "## Previous Goals",
        ]
        history = [row for row in _as_list(state.get("goal_history")) if isinstance(row, dict)]
        if not history:
            lines.append("- none")
        for row in history:
            lines.append(f"- epoch {row.get('epoch')}: {row.get('goal')} ({row.get('ended_at')})")
        stale = [row for row in _as_list(state.get("stale_facts")) if isinstance(row, dict)]
        lines.extend(["", "## Tombstoned Facts"])
        if not stale:
            lines.append("- none")
        for row in stale[-_MAX_STALE_FACTS:]:
            lines.append(f"- epoch {row.get('epoch')}: {row.get('text')}")
        return "\n".join(lines).rstrip()

    def routing_query(self, *, extra: str = "") -> Dict[str, Any]:
        """Return the compact task signal router reducers should optimize for."""
        state = self.load()
        parts: List[str] = []
        for key in ("goal", "next_action", "test_status"):
            value = _short(state.get(key), 260)
            if value:
                parts.append(value)
        for step in _as_list(state.get("current_plan"))[-4:]:
            if step:
                parts.append(_short(step, 180))
        for question in _as_list(state.get("open_questions"))[-3:]:
            if question:
                parts.append(_short(question, 180))
        for row in _as_list(state.get("facts"))[-5:]:
            if isinstance(row, dict) and row.get("text"):
                parts.append(_short(row.get("text"), 180))
        active_files = [str(path) for path in _as_list(state.get("active_files"))[-8:] if str(path).strip()]
        if active_files:
            parts.append("active_files=" + " ".join(active_files))
        has_state_signal = bool(parts)
        if extra and has_state_signal:
            parts.append(_short(extra, 220))
        query = " | ".join(part for part in parts if part)
        intent = classify_task_intent(query)
        return {
            "query": query,
            "intent": intent,
            "source": "compiled_state" if query else "empty",
            "active_files": active_files,
        }

    def debt_summary(self, *, top: bool = False) -> Dict[str, Any]:
        state = self.load()
        events = self._read_audit(limit=_MAX_LEDGER_READ)
        admissions = [e for e in events if e.get("event") == "admission"]
        expansions = [e for e in events if e.get("event") == "pointer_expansion" or e.get("decision") == "expand"]
        admitted = [
            e for e in admissions
            if e.get("decision") in {"admit", "admit_digest", "admit_delta", "pointer_only", "checkpoint", "rollover_required"}
        ]
        suppressed = [e for e in admissions if e.get("decision") == "suppress"]
        admitted_tokens = sum(int(e.get("tokens") or 0) for e in admitted)
        liability_tokens = sum(int(e.get("liability_tokens") or 0) for e in admitted)
        suppressed_tokens = sum(int(e.get("tokens") or 0) for e in suppressed)
        state_card_tokens = estimate_tokens(self.render_state_card())
        projected_cache_read = COMPILED_PREFIX_TOKENS + state_card_tokens + admitted_tokens
        expansion_rate = (len(expansions) / max(1, len(admissions))) if admissions else 0.0
        level = "healthy"
        if projected_cache_read >= BURN_ROLLOVER_TOKENS:
            level = "rollover_required"
        elif projected_cache_read >= BURN_WARNING_TOKENS:
            level = "warning"
        elif projected_cache_read > BURN_TARGET_TOKENS:
            level = "above_target"
        expansion_level = "healthy"
        if expansion_rate >= EXPANSION_CRITICAL:
            expansion_level = "critical"
        elif expansion_rate >= EXPANSION_WARNING:
            expansion_level = "failing"
        elif expansion_rate >= EXPANSION_HEALTHY:
            expansion_level = "warning"

        rows = sorted(admissions, key=lambda e: int(e.get("tokens") or 0), reverse=True)
        receipts = [row for row in _as_list(state.get("admission_receipts")) if isinstance(row, dict)]
        cache_tier_breakdown: Dict[str, Dict[str, int]] = {}
        for row in receipts:
            tier = _normalise_cache_tier(row.get("cache_tier"), CACHE_VOLATILE)
            bucket = cache_tier_breakdown.setdefault(tier, {"count": 0, "tokens": 0})
            bucket["count"] += 1
            bucket["tokens"] += int(row.get("token_estimate") or 0)
        warnings = [row for row in _as_list(state.get("ledger_warnings")) if isinstance(row, dict)]
        assertion_mismatch_count = sum(
            1
            for row in warnings
            if str(row.get("kind") or "").startswith("assertion_")
        )
        reread_short_circuit_count = sum(
            1
            for row in receipts
            if isinstance(row.get("metadata"), dict) and row["metadata"].get("reread_short_circuit")
        )
        suppression_equivalence_projection = {
            "method": "ledger_proxy",
            "suppressed_blocks": len(suppressed),
            "admitted_blocks": len(admitted),
            "suppression_rate": round(len(suppressed) / max(1, len(admissions)), 3) if admissions else 0.0,
            "expansion_rate": round(expansion_rate, 3),
            "risk": "high" if expansion_rate >= EXPANSION_CRITICAL else ("medium" if expansion_rate >= EXPANSION_WARNING else "low"),
            "disclaimer": "Replay estimates context pressure, not live action-distribution equivalence.",
        }
        out = {
            "format": "dhee_context_debt",
            "version": 1,
            "workspace_id": self.workspace_id,
            "repo": self.repo,
            "state_card_tokens": state_card_tokens,
            "compiled_prefix_tokens": COMPILED_PREFIX_TOKENS,
            "admitted_recent_tokens": admitted_tokens,
            "liability_tokens": liability_tokens,
            "suppressed_tokens": suppressed_tokens,
            "projected_cache_read_tokens": projected_cache_read,
            "burn_target_tokens": BURN_TARGET_TOKENS,
            "burn_warning_tokens": BURN_WARNING_TOKENS,
            "burn_rollover_tokens": BURN_ROLLOVER_TOKENS,
            "level": level,
            "expansion_rate": expansion_rate,
            "expansion_level": expansion_level,
            "cache_tier_breakdown": cache_tier_breakdown,
            "receipt_count": len(receipts),
            "assertion_mismatch_count": assertion_mismatch_count,
            "reread_short_circuit_count": reread_short_circuit_count,
            "suppression_equivalence_projection": suppression_equivalence_projection,
            "quality_invariant": "savings are allowed only when expansion and outcome signals do not regress",
            "checkpoint_recommended": level in {"warning", "above_target", "rollover_required"},
            "rollover_required": level == "rollover_required",
        }
        if top:
            out["top_debt_sources"] = [
                {
                    "kind": row.get("kind"),
                    "source": row.get("source"),
                    "decision": row.get("decision"),
                    "tokens": row.get("tokens"),
                    "reason": row.get("reason"),
                }
                for row in rows[:10]
            ]
        return out

    def status(self) -> Dict[str, Any]:
        state = self.load()
        debt = self.debt_summary(top=False)
        return {
            "format": "dhee_context_status",
            "version": 1,
            "workspace_id": self.workspace_id,
            "repo": self.repo,
            "task_epoch": int(state.get("task_epoch") or 1),
            "state_revision": int(state.get("state_revision") or 0),
            "goal_history_count": len(_as_list(state.get("goal_history"))),
            "stale_fact_count": len(_as_list(state.get("stale_facts"))),
            "goal_set": bool(state.get("goal")),
            "fact_count": len(_as_list(state.get("facts"))),
            "active_decision_count": len([d for d in _as_list(state.get("decisions")) if isinstance(d, dict) and d.get("status") == "active"]),
            "superseded_decision_count": len([d for d in _as_list(state.get("decisions")) if isinstance(d, dict) and d.get("status") == "superseded"]),
            "active_file_count": len(_as_list(state.get("active_files"))),
            "evidence_count": len(_as_list(state.get("evidence"))),
            "admission_receipt_count": len(_as_list(state.get("admission_receipts"))),
            "assertion_count": len(_as_list(state.get("assertions"))),
            "ledger_warning_count": len(_as_list(state.get("ledger_warnings"))),
            "rollover_receipt_count": len(_as_list(state.get("rollover_receipts"))),
            "checkpoint_count": int(state.get("checkpoint_count") or 0),
            "state_card_tokens": debt["state_card_tokens"],
            "projected_cache_read_tokens": debt["projected_cache_read_tokens"],
            "level": debt["level"],
            "expansion_rate": debt["expansion_rate"],
            "expansion_level": debt["expansion_level"],
            "cache_tier_breakdown": debt["cache_tier_breakdown"],
            "rollover_required": debt["rollover_required"],
        }

    def provision(self, task: str) -> Dict[str, Any]:
        state = self.load()
        debt = self.debt_summary(top=True)
        task_tokens = estimate_tokens(task)
        state_card_tokens = debt["state_card_tokens"]
        routed_tokens = min(
            max(500, state_card_tokens + min(task_tokens, 600)),
            STATE_CARD_TARGET_TOKENS + 700,
        )
        raw_tokens = max(task_tokens, task_tokens + debt["admitted_recent_tokens"])
        risk = "low"
        if debt["level"] in {"above_target", "warning"}:
            risk = "medium"
        if debt["rollover_required"]:
            risk = "high"
        return {
            "format": "dhee_context_provision",
            "version": 1,
            "task": str(task or ""),
            "goal": state.get("goal") or "",
            "task_epoch": int(state.get("task_epoch") or 1),
            "state_revision": int(state.get("state_revision") or 0),
            "estimated_raw_tokens": raw_tokens,
            "estimated_compiled_tokens": routed_tokens,
            "state_card_tokens": state_card_tokens,
            "projected_cache_read_tokens": debt["projected_cache_read_tokens"],
            "risk": risk,
            "checkpoint_recommended": debt["checkpoint_recommended"],
            "rollover_required": debt["rollover_required"],
            "will_change_files_or_memories": False,
            "quality_gate": debt["quality_invariant"],
        }

    def checkpoint(self, *, reason: str = "manual") -> Dict[str, Any]:
        with self._locked():
            state = self.load()
            state["checkpoint_count"] = int(state.get("checkpoint_count") or 0) + 1
            state = self.save(state)
        snapshot = {
            "format": "dhee_context_checkpoint",
            "version": 1,
            "id": f"chk-{int(time.time())}-{secrets.token_hex(3)}",
            "created_at": now_iso(),
            "reason": reason,
            "workspace_id": self.workspace_id,
            "repo": self.repo,
            "task_epoch": state.get("task_epoch"),
            "state": state,
            "state_card": self.render_state_card(),
            "debt": self.debt_summary(top=True),
        }
        path = self.checkpoint_dir / f"{snapshot['id']}.json"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json_write(path, snapshot)
        self._append_audit({"event": "checkpoint", "id": snapshot["id"], "reason": reason})
        return snapshot

    def rollover(self, *, reason: str = "context debt") -> Dict[str, Any]:
        snapshot = self.checkpoint(reason=reason)
        receipt = self._build_rollover_receipt(snapshot, reason=reason)
        with self._locked():
            state = self.load()
            self._append_rollover_receipt(state, receipt)
            self.save(state)
        instruction = (
            "Start the next turn from Dhee compiled state. Treat prior transcript as audit-only; "
            "use /state/card.xml, /state/current.md, pointer evidence, and rollover receipts before re-reading raw history. "
            "If a file appears in a rollover receipt, expand that receipt or its evidence pointer first."
        )
        result = {
            "format": "dhee_context_rollover",
            "version": 1,
            "checkpoint_id": snapshot["id"],
            "created_at": now_iso(),
            "repo": self.repo,
            "instruction": instruction,
            "state_card": snapshot["state_card"],
            "debt": snapshot["debt"],
            "rollover_receipt": receipt,
        }
        self._append_audit(
            {
                "event": "rollover",
                "checkpoint_id": snapshot["id"],
                "rollover_receipt_id": receipt.get("id"),
                "reason": reason,
            }
        )
        return result

    def _build_rollover_receipt(self, snapshot: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else self.load()
        receipts = [row for row in _as_list(state.get("admission_receipts")) if isinstance(row, dict)]
        evidence = [row for row in _as_list(state.get("evidence")) if isinstance(row, dict)]
        decisions = [row for row in _as_list(state.get("decisions")) if isinstance(row, dict)]
        active_decisions = [
            {"id": row.get("id"), "text": _short(row.get("text"), 160)}
            for row in decisions
            if row.get("status") == "active"
        ]
        supersession_edges = [
            {
                "from": row.get("id"),
                "to": row.get("superseded_by"),
                "reason": row.get("superseded_reason") or "superseded",
            }
            for row in decisions
            if row.get("status") == "superseded" and row.get("superseded_by")
        ]
        summarized_files: List[str] = []
        for path in _as_list(state.get("active_files")):
            if str(path).strip() and str(path) not in summarized_files:
                summarized_files.append(str(path))
        for receipt in receipts:
            source = str(receipt.get("source") or "")
            if source and (Path(source).suffix or "/" in source) and source not in summarized_files:
                summarized_files.append(source)
        content_hashes = [
            str(row.get("content_hash"))
            for row in receipts
            if isinstance(row.get("content_hash"), str) and row.get("content_hash")
        ]
        tool_classes = sorted({str(row.get("kind") or "") for row in receipts if row.get("kind")})
        summarized_evidence = [
            {
                "ptr": row.get("ptr"),
                "kind": row.get("kind"),
                "source": row.get("source"),
                "summary": _short(row.get("summary"), 160),
                "content_hash": next(
                    (
                        receipt.get("content_hash")
                        for receipt in receipts
                        if receipt.get("ptr") and receipt.get("ptr") == row.get("ptr")
                    ),
                    "",
                ),
            }
            for row in evidence[-12:]
        ]
        receipt_seed = json.dumps(
            {
                "checkpoint": snapshot.get("id"),
                "epoch": state.get("task_epoch"),
                "hashes": sorted(content_hashes),
            },
            sort_keys=True,
            default=str,
        )
        return {
            "id": "RR-" + _content_hash(receipt_seed)[:12],
            "checkpoint_id": snapshot.get("id"),
            "created_at": now_iso(),
            "reason": _short(reason, 220),
            "task_epoch": state.get("task_epoch"),
            "state_revision": state.get("state_revision"),
            "summarized_files": summarized_files[-24:],
            "summarized_evidence": summarized_evidence,
            "summarized_hashes": sorted(set(content_hashes))[-64:],
            "active_decisions": active_decisions[-12:],
            "test_status": _short(state.get("test_status"), 220),
            "tool_classes": tool_classes,
            "supersession_edges": supersession_edges[-16:],
            "guidance": "Expand this rollover receipt or its evidence pointers before re-reading summarized raw context.",
        }

    def _append_rollover_receipt(self, state: Dict[str, Any], receipt: Dict[str, Any]) -> None:
        rows = [
            row
            for row in _as_list(state.get("rollover_receipts"))
            if isinstance(row, dict) and row.get("id") != receipt.get("id")
        ]
        rows.append(receipt)
        state["rollover_receipts"] = rows[-_MAX_ROLLOVER_RECEIPTS:]

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        if not self.checkpoint_dir.exists():
            return []
        rows: List[Dict[str, Any]] = []
        for path in sorted(self.checkpoint_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows.append(
                {
                    "id": data.get("id") or path.stem,
                    "path": str(path),
                    "created_at": data.get("created_at"),
                    "reason": data.get("reason"),
                }
            )
        return rows

    def read_checkpoint(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        safe = str(checkpoint_id or "").strip().replace("/", "")
        path = self.checkpoint_dir / (safe if safe.endswith(".json") else f"{safe}.json")
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def read_audit_text(self, *, limit: int = 200) -> str:
        events = self._read_audit(limit=limit)
        return "\n".join(json.dumps(row, sort_keys=True, default=str) for row in events)

    def _render_state_card(
        self,
        state: Dict[str, Any],
        *,
        fact_limit: int,
        decision_limit: int,
        file_limit: int,
        evidence_limit: int,
    ) -> str:
        debt_level = self._debt_level_for_card()
        lines = [
            (
                f'<dhee_state v="1" epoch="{_esc_attr(state.get("task_epoch") or 1)}" '
                f'revision="{_esc_attr(state.get("state_revision") or 0)}" '
                f'updated="{_esc_attr(state.get("updated_at") or "")}" debt="{_esc_attr(debt_level)}">'
            ),
        ]
        if state.get("goal"):
            lines.append(f"<goal>{_esc(state.get('goal'))}</goal>")

        facts = [row for row in _as_list(state.get("facts")) if isinstance(row, dict) and row.get("text")]
        if facts:
            lines.append("<facts>")
            for row in facts[-fact_limit:]:
                attrs = _attrs(src=str(row.get("source") or ""))
                lines.append(f"  <f{(' ' + attrs) if attrs else ''}>{_esc(row.get('text'))}</f>")
            lines.append("</facts>")

        active = [row for row in _as_list(state.get("decisions")) if isinstance(row, dict) and row.get("status") == "active"]
        if active:
            lines.append("<decisions>")
            for row in active[-decision_limit:]:
                lines.append(f'  <d id="{_esc_attr(row.get("id") or "")}">{_esc(row.get("text"))}</d>')
            lines.append("</decisions>")

        superseded = [row for row in _as_list(state.get("decisions")) if isinstance(row, dict) and row.get("status") == "superseded"]
        if superseded:
            tombs = []
            for row in superseded[-2:]:
                tombs.append(f'{row.get("id")}->{row.get("superseded_by")}')
            lines.append(f"<tombstones>{_esc(', '.join(tombs))}</tombstones>")

        plan = [str(step) for step in _as_list(state.get("current_plan")) if str(step).strip()]
        if plan:
            lines.append("<plan>")
            for step in plan[:_MAX_PLAN_STEPS]:
                lines.append(f"  <step>{_esc(step)}</step>")
            lines.append("</plan>")

        if state.get("next_action"):
            lines.append(f"<next>{_esc(state.get('next_action'))}</next>")
        if state.get("test_status"):
            lines.append(f"<tests>{_esc(state.get('test_status'))}</tests>")

        questions = [str(q) for q in _as_list(state.get("open_questions")) if str(q).strip()]
        if questions:
            lines.append("<open_questions>")
            for question in questions[-3:]:
                lines.append(f"  <q>{_esc(question)}</q>")
            lines.append("</open_questions>")

        files = [str(path) for path in _as_list(state.get("active_files")) if str(path).strip()]
        if files:
            lines.append("<files>")
            for path in files[-file_limit:]:
                lines.append(f"  <file>{_esc(path)}</file>")
            lines.append("</files>")

        evidence = [row for row in _as_list(state.get("evidence")) if isinstance(row, dict) and row.get("ptr")]
        if evidence:
            lines.append("<evidence>")
            for row in evidence[-evidence_limit:]:
                attrs = _attrs(ptr=str(row.get("ptr") or ""), kind=str(row.get("kind") or ""), src=str(row.get("source") or ""))
                lines.append(f"  <ptr {attrs}>{_esc(row.get('summary') or '')}</ptr>")
            lines.append("</evidence>")

        receipts = [row for row in _as_list(state.get("rollover_receipts")) if isinstance(row, dict)]
        if receipts:
            lines.append("<rollover_receipts>")
            for row in receipts[-2:]:
                attrs = _attrs(id=str(row.get("id") or ""), checkpoint=str(row.get("checkpoint_id") or ""))
                files = ", ".join(str(path) for path in _as_list(row.get("summarized_files"))[-4:])
                lines.append(f"  <receipt {attrs}>{_esc(files or row.get('guidance') or '')}</receipt>")
            lines.append("</rollover_receipts>")

        lines.append("</dhee_state>")
        return "\n".join(lines)

    def _debt_level_for_card(self) -> str:
        events = self._read_audit(limit=200)
        admitted_tokens = sum(
            int(row.get("tokens") or 0)
            for row in events
            if row.get("event") == "admission"
            and row.get("decision") in {"admit", "admit_digest", "admit_delta", "pointer_only", "checkpoint", "rollover_required"}
        )
        projected = COMPILED_PREFIX_TOKENS + STATE_CARD_TARGET_TOKENS + admitted_tokens
        if projected >= BURN_ROLLOVER_TOKENS:
            return "rollover_required"
        if projected >= BURN_WARNING_TOKENS:
            return "warning"
        if projected > BURN_TARGET_TOKENS:
            return "above_target"
        return "healthy"

    def _should_start_new_epoch(self, state: Dict[str, Any], prompt: str) -> bool:
        goal = str(state.get("goal") or "").strip()
        prompt = str(prompt or "").strip()
        if not goal or not prompt:
            return False
        prompt_hash = _content_hash(prompt)
        if prompt_hash == state.get("last_prompt_hash"):
            return False
        if _looks_like_continuation(prompt):
            return False
        prompt_terms = _terms(prompt)
        if len(prompt_terms) < 5:
            return False
        overlap = _overlap_ratio(goal, prompt)
        action_signal = bool(prompt_terms & _PIVOT_ACTION_TERMS)
        if overlap <= 0.12 and action_signal:
            return True
        if overlap <= 0.20 and action_signal and (state.get("facts") or state.get("active_files") or state.get("evidence")):
            return True
        return False

    def _start_new_epoch(self, state: Dict[str, Any], new_goal: str, *, reason: str) -> None:
        old_epoch = int(state.get("task_epoch") or 1)
        ended_at = now_iso()
        old_goal = _short(state.get("goal"), 360)
        if old_goal:
            history = _as_list(state.get("goal_history"))
            history.append(
                {
                    "epoch": old_epoch,
                    "goal": old_goal,
                    "ended_at": ended_at,
                    "reason": reason,
                    "fact_count": len(_as_list(state.get("facts"))),
                    "evidence_count": len(_as_list(state.get("evidence"))),
                }
            )
            state["goal_history"] = history[-_MAX_GOAL_HISTORY:]

        stale: List[Dict[str, Any]] = _as_list(state.get("stale_facts"))
        for row in _as_list(state.get("facts"))[-6:]:
            if isinstance(row, dict) and row.get("text"):
                stale.append(
                    {
                        "epoch": old_epoch,
                        "id": row.get("id"),
                        "text": row.get("text"),
                        "source": row.get("source"),
                        "ended_at": ended_at,
                    }
                )
        state["stale_facts"] = stale[-_MAX_STALE_FACTS:]

        marker = f"epoch-{old_epoch + 1}"
        for row in _as_list(state.get("decisions")):
            if isinstance(row, dict) and row.get("status") == "active":
                row["status"] = "superseded"
                row["superseded_by"] = marker
                row["superseded_reason"] = "previous task epoch"
                row["updated_at"] = ended_at

        state["task_epoch"] = old_epoch + 1
        state["goal"] = _short(new_goal, 360)
        state["facts"] = []
        state["current_plan"] = []
        state["next_action"] = "Answer the current user request using Dhee compiled state."
        state["active_files"] = []
        state["open_questions"] = []
        state["test_status"] = ""
        state["evidence"] = []
        state["seen_hashes"] = {}
        state["admission_receipts"] = []
        state["assertions"] = []
        state["ledger_warnings"] = []
        state["last_prompt_hash"] = _content_hash(new_goal)
        state["last_epoch_reason"] = reason
        state["epoch_started_at"] = ended_at
        self._append_audit(
            {
                "event": "task_epoch_started",
                "old_epoch": old_epoch,
                "new_epoch": old_epoch + 1,
                "reason": reason,
                "goal": state["goal"],
            }
        )

    def _learn_from_admission(
        self,
        state: Dict[str, Any],
        block: ContextBlock,
        result: AdmissionResult,
        *,
        receipt_id: str = "",
    ) -> None:
        if result.decision not in {"admit", "admit_digest", "admit_delta", "pointer_only", "checkpoint", "rollover_required"}:
            return
        metadata: Dict[str, Any] = {}
        if isinstance(block.metadata, dict):
            metadata.update(block.metadata)
        if isinstance(result.metadata, dict):
            metadata.update({k: v for k, v in result.metadata.items() if k not in metadata})

        ptr = str(block.ptr or metadata.get("ptr") or "").strip()
        source = str(block.source or metadata.get("source_path") or "").strip()
        rel_source = _rel(source, self.repo) if source else ""
        kind = str(block.kind or metadata.get("kind") or "").lower()
        text = str(block.text or "")

        if rel_source and (Path(rel_source).suffix or "/" in rel_source):
            state["active_files"] = _append_unique(_as_list(state.get("active_files")), rel_source, limit=_MAX_ACTIVE_FILES)

        if ptr:
            self._add_evidence_to_state(
                state,
                ptr=ptr,
                kind=kind or "context",
                source=rel_source or source or kind,
                summary=_short(text, 220) or result.reason,
            )

        command = str(metadata.get("command") or "").strip()
        if kind in {"routed_bash", "bash"} or command:
            exit_code = metadata.get("exit_code")
            if command and _looks_like_test_command(command):
                status = f"{command} exit={exit_code if exit_code is not None else 'unknown'}"
                first_ref = _extract_file_refs(text, limit=1)
                if first_ref:
                    status = f"{status} first_ref={first_ref[0]}"
                state["test_status"] = _short(status, 240)
                state["next_action"] = (
                    "Fix the current failing test or record the green verification."
                    if str(exit_code) not in {"0", "None", ""}
                    else "Continue from the passing test result."
                )
                self._add_fact_to_state(
                    state,
                    state["test_status"],
                    source="routed_bash",
                    evidence=[ptr] if ptr else [],
                    receipt_id=receipt_id,
                )
            elif command:
                self._add_fact_to_state(
                    state,
                    f"Ran {command} ({result.decision})",
                    source="routed_bash",
                    evidence=[ptr] if ptr else [],
                    receipt_id=receipt_id,
                )

        if kind in {"routed_grep", "grep"}:
            pattern = str(metadata.get("pattern") or "").strip()
            match_count = metadata.get("match_count")
            file_count = metadata.get("file_count")
            if pattern:
                self._add_fact_to_state(
                    state,
                    f"Search {pattern!r} matched {match_count if match_count is not None else '?'} hits in {file_count if file_count is not None else '?'} files.",
                    source="routed_grep",
                    evidence=[ptr] if ptr else [],
                    receipt_id=receipt_id,
                )

        if kind in {"routed_read", "read"} and rel_source:
            intent = metadata.get("task_intent") or metadata.get("intent") or "general"
            self._add_fact_to_state(
                state,
                f"Read {rel_source} using {intent} digest.",
                source="routed_read",
                evidence=[ptr] if ptr else [],
                receipt_id=receipt_id,
            )

        if kind in {"routed_agent", "agent"}:
            for ref in _extract_file_refs(text, limit=4):
                state["active_files"] = _append_unique(_as_list(state.get("active_files")), _rel(ref.split(":", 1)[0], self.repo), limit=_MAX_ACTIVE_FILES)
            summary = _short(text, 220)
            if summary:
                self._add_fact_to_state(
                    state,
                    f"Agent evidence: {summary}",
                    source="routed_agent",
                    evidence=[ptr] if ptr else [],
                    receipt_id=receipt_id,
                )

    def _add_fact_to_state(
        self,
        state: Dict[str, Any],
        text: str,
        *,
        source: str,
        evidence: List[str],
        receipt_id: str = "",
    ) -> None:
        body = _short(text, 260)
        if not body:
            return
        fact_id = "F-" + _content_hash(body)[:10]
        facts = [row for row in _as_list(state.get("facts")) if isinstance(row, dict) and row.get("id") != fact_id]
        facts.append(
            {
                "id": fact_id,
                "text": body,
                "source": source,
                "evidence": evidence[:5],
                "receipt_id": receipt_id,
                "created_at": now_iso(),
            }
        )
        state["facts"] = facts[-_MAX_FACTS:]
        self._add_assertion_to_state(
            state,
            body,
            source=source,
            evidence=evidence,
            receipt_id=receipt_id,
            assertion_type="fact",
        )

    def _add_decision_to_state(
        self,
        state: Dict[str, Any],
        text: str,
        *,
        evidence: List[str],
        source: str,
        receipt_id: str = "",
    ) -> Dict[str, Any]:
        body = _short(text, 260)
        if not body:
            body = "decision"
        decision_id = "D-" + _content_hash(body)[:10]
        decisions = [row for row in _as_list(state.get("decisions")) if isinstance(row, dict) and row.get("id") != decision_id]
        row = {
            "id": decision_id,
            "text": body,
            "status": "active",
            "source": source,
            "evidence": evidence[:5],
            "receipt_id": receipt_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        decisions.append(row)
        state["decisions"] = decisions[-_MAX_DECISIONS:]
        self._add_assertion_to_state(
            state,
            body,
            source=source,
            evidence=evidence,
            receipt_id=receipt_id,
            assertion_type="decision",
        )
        return row

    def _add_assertion_to_state(
        self,
        state: Dict[str, Any],
        text: str,
        *,
        source: str,
        evidence: List[str],
        receipt_id: str,
        assertion_type: str,
    ) -> None:
        body = _short(text, 220)
        if not body:
            return
        assertion_id = "AS-" + _content_hash(f"{assertion_type}\0{body}")[:10]
        supported = bool(evidence or receipt_id)
        assertions = [
            row
            for row in _as_list(state.get("assertions"))
            if isinstance(row, dict) and row.get("id") != assertion_id
        ]
        assertions.append(
            {
                "id": assertion_id,
                "type": assertion_type,
                "text": body,
                "source": _short(source, 140),
                "evidence": evidence[:5],
                "receipt_id": receipt_id,
                "status": "supported" if supported else "unverified",
                "created_at": now_iso(),
            }
        )
        state["assertions"] = assertions[-_MAX_ASSERTIONS:]
        if not supported:
            self._add_ledger_warning_to_state(
                state,
                kind="assertion_without_admission",
                source=source,
                text=f"{assertion_type} assertion has no evidence pointer or admission receipt.",
                receipt_id=assertion_id,
            )

    def _add_ledger_warning_to_state(
        self,
        state: Dict[str, Any],
        *,
        kind: str,
        source: str,
        text: str,
        receipt_id: str = "",
    ) -> Dict[str, Any]:
        body = _short(text, 260)
        warning_id = "LW-" + _content_hash(f"{kind}\0{source}\0{body}\0{receipt_id}")[:10]
        row = {
            "id": warning_id,
            "kind": _short(kind, 80),
            "source": _short(source, 180),
            "text": body,
            "receipt_id": receipt_id,
            "created_at": now_iso(),
            "task_epoch": state.get("task_epoch"),
        }
        warnings = [
            item
            for item in _as_list(state.get("ledger_warnings"))
            if isinstance(item, dict) and item.get("id") != warning_id
        ]
        warnings.append(row)
        state["ledger_warnings"] = warnings[-_MAX_LEDGER_WARNINGS:]
        return row

    def _mark_superseded(self, state: Dict[str, Any], old_id: str, *, by: str, reason: str = "") -> None:
        for row in _as_list(state.get("decisions")):
            if isinstance(row, dict) and row.get("id") == old_id:
                row["status"] = "superseded"
                row["superseded_by"] = by
                row["superseded_reason"] = _short(reason, 180)
                row["updated_at"] = now_iso()

    def _add_evidence_to_state(self, state: Dict[str, Any], *, ptr: str, kind: str, source: str, summary: str) -> None:
        if not ptr:
            return
        evidence = [row for row in _as_list(state.get("evidence")) if isinstance(row, dict) and row.get("ptr") != ptr]
        evidence.append(
            {
                "ptr": ptr,
                "kind": _short(kind, 40),
                "source": _short(source, 140),
                "summary": _short(summary, 220),
                "created_at": now_iso(),
            }
        )
        state["evidence"] = evidence[-_MAX_EVIDENCE:]

    def _append_audit(self, event: Dict[str, Any]) -> None:
        row = {"ts": now_iso(), "workspace_id": self.workspace_id, "agent_id": self.agent_id, **event}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            try:
                os.chmod(self.audit_path, 0o600)
            except OSError:
                pass
        except OSError:
            return

    def _read_audit(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


class ContextAdmissionController:
    """Admission policy for context blocks before they enter the prompt."""

    def __init__(self, store: ContextStateStore):
        self.store = store

    def decide(self, block: ContextBlock) -> AdmissionResult:
        state = self.store.load()
        seen = state.get("seen_hashes") if isinstance(state.get("seen_hashes"), dict) else {}
        token_estimate = block.tokens
        content_hash = block.hash
        metadata = dict(block.metadata or {}) if isinstance(block.metadata, dict) else {}
        cache_tier = _normalise_cache_tier(
            block.cache_tier or metadata.get("cache_tier"),
            _default_cache_tier(block.kind, block.source, metadata),
        )
        metadata["cache_tier"] = cache_tier
        block.metadata = metadata

        rollover_receipt = self._find_rollover_receipt_for_block(state, block, content_hash)
        if rollover_receipt:
            metadata["reread_short_circuit"] = True
            metadata["rollover_receipt_id"] = rollover_receipt.get("id")
            result = AdmissionResult(
                decision="suppress",
                reason="rollover receipt already summarizes this source; expand receipt before re-reading raw context",
                token_estimate=token_estimate,
                content_hash=content_hash,
                liability_tokens=0,
                cache_tier=cache_tier,
                metadata=metadata,
            )
            self.store.record_admission(block, result)
            return result

        if content_hash in seen:
            result = AdmissionResult(
                decision="suppress",
                reason="unchanged context already admitted for this workspace",
                token_estimate=token_estimate,
                content_hash=content_hash,
                liability_tokens=0,
                cache_tier=cache_tier,
                metadata=metadata,
            )
            self.store.record_admission(block, result)
            return result

        kind = block.kind.lower()
        if kind in {"live_mirror", "edit_mirror", "tool_mirror"} and not self._overlaps_active_file(block, state):
            result = AdmissionResult(
                decision="suppress",
                reason="mechanical mirror does not overlap active files",
                token_estimate=token_estimate,
                content_hash=content_hash,
                liability_tokens=0,
                cache_tier=cache_tier,
                metadata=metadata,
            )
            self.store.record_admission(block, result)
            return result

        if kind in {"doc", "repo_context", "memory", "shared", "session"}:
            relevance = 1.0 if block.relevance is None else float(block.relevance)
            if relevance < 0.50:
                result = AdmissionResult(
                    decision="suppress",
                    reason="below relevance threshold for compiled state",
                    token_estimate=token_estimate,
                    content_hash=content_hash,
                    liability_tokens=0,
                    cache_tier=cache_tier,
                    metadata={**metadata, "relevance": relevance},
                )
                self.store.record_admission(block, result)
                return result

        if cache_tier == CACHE_ONE_HOUR and self._would_mutate_stable_prefix(state, block, content_hash):
            cache_tier = CACHE_FIVE_MINUTE
            metadata["cache_tier"] = cache_tier
            metadata["original_cache_tier"] = CACHE_ONE_HOUR
            metadata["stable_prefix_mutation"] = True

        debt = self.store.debt_summary(top=False)
        projected = int(debt.get("projected_cache_read_tokens") or 0) + token_estimate
        if projected >= BURN_ROLLOVER_TOKENS:
            decision = "rollover_required"
            reason = "projected context debt crossed rollover threshold"
        elif projected >= BURN_WARNING_TOKENS:
            decision = "checkpoint"
            reason = "projected context debt crossed checkpoint threshold"
        elif token_estimate > 12000:
            decision = "pointer_only" if block.ptr else "admit_digest"
            reason = "large raw context must stay behind pointer-backed evidence"
        elif token_estimate > 2000:
            decision = "admit_digest"
            reason = "digest is safer than raw context for future turns"
        else:
            decision = "admit"
            reason = "fresh relevant context within budget"

        liability_tokens = token_estimate if decision in {"admit", "admit_digest", "admit_delta", "checkpoint", "rollover_required"} else min(60, token_estimate)
        result = AdmissionResult(
            decision=decision,
            reason=reason,
            token_estimate=token_estimate,
            content_hash=content_hash,
            liability_tokens=liability_tokens,
            cache_tier=cache_tier,
            metadata={**metadata, "projected_cache_read_tokens": projected},
        )
        self.store.record_admission(block, result)
        return result

    def _find_rollover_receipt_for_block(
        self,
        state: Dict[str, Any],
        block: ContextBlock,
        content_hash: str,
    ) -> Optional[Dict[str, Any]]:
        if str(block.kind or "").lower() not in {"routed_read", "read", "tool_result"}:
            return None
        for receipt in reversed(_as_list(state.get("rollover_receipts"))):
            if not isinstance(receipt, dict):
                continue
            hashes = {str(item) for item in _as_list(receipt.get("summarized_hashes"))}
            if content_hash in hashes:
                return receipt
        return None

    def _would_mutate_stable_prefix(self, state: Dict[str, Any], block: ContextBlock, content_hash: str) -> bool:
        source_key = _stable_source_key(block.kind, block.source)
        for receipt in reversed(_as_list(state.get("admission_receipts"))):
            if not isinstance(receipt, dict):
                continue
            if receipt.get("cache_tier") != CACHE_ONE_HOUR:
                continue
            if receipt.get("task_epoch") != state.get("task_epoch"):
                continue
            if receipt.get("source_key") != source_key:
                continue
            if receipt.get("content_hash") and receipt.get("content_hash") != content_hash:
                return True
        return False

    def _overlaps_active_file(self, block: ContextBlock, state: Dict[str, Any]) -> bool:
        source = str(block.source or block.metadata.get("source_path") or "").lower()
        if not source:
            return False
        source_base = os.path.basename(source)
        for active in _as_list(state.get("active_files")):
            active_text = str(active or "").lower()
            if not active_text:
                continue
            if active_text in source or source in active_text:
                return True
            if source_base and source_base == os.path.basename(active_text):
                return True
        return False


def detect_echo(tool_text: str, assistant_text: str, *, threshold: float = 0.68) -> Dict[str, Any]:
    """Detect assistant prose that mostly repeats the prior tool result."""
    tool_tokens = set(_TOKEN_RE.findall(str(tool_text or "").lower()))
    assistant_tokens = set(_TOKEN_RE.findall(str(assistant_text or "").lower()))
    if len(assistant_tokens) < 12 or len(tool_tokens) < 12:
        return {"is_echo": False, "overlap": 0.0, "threshold": threshold}
    overlap = len(tool_tokens & assistant_tokens) / max(1, min(len(tool_tokens), len(assistant_tokens)))
    return {
        "is_echo": overlap >= threshold,
        "overlap": overlap,
        "threshold": threshold,
        "assistant_token_count": len(assistant_tokens),
        "tool_token_count": len(tool_tokens),
    }


def classify_task_intent(text: str) -> str:
    value = str(text or "").lower()
    if any(needle in value for needle in ("traceback", "failing", "failure", "bug", "debug", "pytest", "exception", "error")):
        return "debug_failure"
    if any(needle in value for needle in ("definition", "where is", "find function", "find class", "signature", "symbol")):
        return "find_definition"
    if any(needle in value for needle in ("architecture", "overview", "module", "boundaries", "understand", "map")):
        return "understand_module"
    if any(needle in value for needle in ("config", "configuration", "settings", "env", "install", "setup")):
        return "inspect_config"
    return "general"


def task_aware_read_schema(path: str, *, query: str = "", task_intent: str = "") -> Dict[str, Any]:
    intent = task_intent or classify_task_intent(query)
    ext = Path(path or "").suffix.lower()
    if intent == "find_definition":
        return {
            "intent": intent,
            "preferred_depth": "normal",
            "focus": ["symbols", "signatures", "exact ranges"],
            "note": "task_schema=find_definition: use symbols/signatures first; expand only the exact range needed",
        }
    if intent == "debug_failure":
        return {
            "intent": intent,
            "preferred_depth": "deep" if ext in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"} else "normal",
            "focus": ["failing assertion", "stack frame", "related symbol", "next verification command"],
            "note": "task_schema=debug_failure: preserve failure landmarks and likely next verification command",
        }
    if intent == "understand_module":
        return {
            "intent": intent,
            "preferred_depth": "normal",
            "focus": ["imports", "exports", "public classes", "public functions"],
            "note": "task_schema=understand_module: prefer module contracts over full body",
        }
    if intent == "inspect_config":
        return {
            "intent": intent,
            "preferred_depth": "normal",
            "focus": ["top-level keys", "scripts", "dependency boundaries"],
            "note": "task_schema=inspect_config: preserve keys and scripts before raw expansion",
        }
    return {
        "intent": intent,
        "preferred_depth": "normal",
        "focus": ["symbols", "head", "tail"],
        "note": "task_schema=general: pointer-backed digest; expand only when needed",
    }


def _esc(text: Any) -> str:
    return _xml_escape(str(text or ""))


def _esc_attr(text: Any) -> str:
    return _xml_escape(str(text or ""), {'"': "&quot;"})


def _attrs(**kwargs: str) -> str:
    parts: List[str] = []
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        parts.append(f'{key}="{_esc_attr(value)}"')
    return " ".join(parts)
