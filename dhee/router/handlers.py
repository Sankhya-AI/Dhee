"""Shared handler functions for Dhee router MCP tools.

Exposed by both `dhee-mcp` (slim) and `dhee-mcp-full`. Handlers take a
plain argument dict and return a plain dict — no memory/plugin
dependency, no MCP framework coupling.
"""

from __future__ import annotations

import os
import subprocess
import time
import hashlib
from pathlib import Path
from typing import Any, Dict

from dhee.core.shared_tasks import publish_in_flight, publish_shared_task_result
from dhee.router import agent_digest as _agent_digest
from dhee.router import bash_digest as _bash_digest
from dhee.router import critical_surface as _critical_surface
from dhee.router import digest as _digest
from dhee.router import grep_digest as _grep_digest
from dhee.router import intent as _intent
from dhee.router import policy as _policy
from dhee.router import ptr_store

# dhee_agent limits
AGENT_MAX_TEXT_BYTES = 5 * 1024 * 1024  # 5 MB


# dhee_bash limits
BASH_DEFAULT_TIMEOUT = 120
BASH_MAX_TIMEOUT = 600
BASH_MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB total per stream


# File-size guard. Prevents OOM + runaway token spend on huge files.
MAX_READ_BYTES = 5 * 1024 * 1024  # 5 MB

# Pointer-cache eviction. Delete session directories whose mtime is
# older than this when the router is invoked. 7 days accommodates a
# normal work week of long-running project sessions.
PTR_TTL_SECONDS = 7 * 24 * 3600  # 7 days

# Inflation floor. When raw input is small, a full digest wrapper can
# exceed the raw content, *losing* tokens. Below this size we compare
# the rendered digest against the raw and fall back to a minimal
# inlined wrapper when the digest would inflate the payload. Set
# empirically: beyond 2 KB the digest is always cheaper because the
# symbol + head/tail summary compresses well; under that it often
# isn't.
INLINE_INFLATION_THRESHOLD = 2048

_ROUTE_DB = None
_ROUTE_DB_PATH = None


def _shared_context() -> Dict[str, Any]:
    cwd = os.getcwd()
    harness = os.environ.get("DHEE_HARNESS") or os.environ.get("DHEE_AGENT_ID") or "unknown"
    return {
        "user_id": os.environ.get("DHEE_USER_ID", "default"),
        "repo": cwd,
        "cwd": cwd,
        "session_id": os.environ.get("DHEE_SESSION_ID"),
        "thread_id": os.environ.get("DHEE_THREAD_ID"),
        "harness": harness,
        "agent_id": os.environ.get("DHEE_AGENT_ID") or harness,
    }


def _shared_event_id(tool_name: str, *parts: Any) -> str:
    seed = "|".join([tool_name, *[str(part or "") for part in parts]])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _publish_shared_claim(
    *,
    packet_kind: str,
    tool_name: str,
    digest: str,
    source_path: str,
    source_event_id: str,
    metadata: Dict[str, Any],
) -> None:
    db = _route_db()
    if db is None:
        return
    ctx = _shared_context()
    publish_in_flight(
        db,
        user_id=ctx["user_id"],
        packet_kind=packet_kind,
        tool_name=tool_name,
        digest=digest,
        repo=ctx["repo"],
        cwd=ctx["cwd"],
        source_path=source_path,
        source_event_id=source_event_id,
        metadata=metadata,
        session_id=ctx["session_id"],
        thread_id=ctx["thread_id"],
        harness=ctx["harness"],
        agent_id=ctx["agent_id"],
    )


def _publish_shared_result(
    *,
    packet_kind: str,
    tool_name: str,
    digest: str,
    source_path: str,
    source_event_id: str,
    ptr: str | None = None,
    artifact_id: str | None = None,
    metadata: Dict[str, Any],
) -> None:
    db = _route_db()
    if db is None:
        return
    ctx = _shared_context()
    publish_shared_task_result(
        db,
        user_id=ctx["user_id"],
        packet_kind=packet_kind,
        tool_name=tool_name,
        digest=digest,
        repo=ctx["repo"],
        cwd=ctx["cwd"],
        source_path=source_path,
        source_event_id=source_event_id,
        ptr=ptr,
        artifact_id=artifact_id,
        metadata=metadata,
        session_id=ctx["session_id"],
        thread_id=ctx["thread_id"],
        harness=ctx["harness"],
        agent_id=ctx["agent_id"],
    )


def _inline_read(content: str, ptr: str, path: str, line_count: int, char_count: int) -> str:
    """Minimal inlined dhee_read output used when the full digest would
    be larger than the raw content.
    """
    trailer = "" if content.endswith("\n") else "\n"
    return (
        f'<dhee_read ptr="{ptr}" inlined="1">\n'
        f"path={path}\n"
        f"size={line_count} lines, {char_count} chars (inlined — digest not shorter)\n"
        f"{content}{trailer}"
        f"</dhee_read>"
    )


def _inline_bash(cmd: str, exit_code: int, duration_ms: int, stdout: str, stderr: str, ptr: str) -> str:
    """Minimal inlined dhee_bash output used when the full digest would
    be larger than the raw stdout+stderr combined.
    """
    parts: list[str] = [
        f'<dhee_bash ptr="{ptr}" inlined="1">',
        f"cmd={cmd}",
        f"exit={exit_code} duration={duration_ms}ms (inlined — digest not shorter)",
    ]
    if stdout:
        parts.append("stdout:")
        parts.append(stdout.rstrip("\n"))
    if stderr:
        parts.append("stderr:")
        parts.append(stderr.rstrip("\n"))
    parts.append("</dhee_bash>")
    return "\n".join(parts)


def _evict_stale_ptr_sessions() -> None:
    """Prune ptr-cache session directories older than PTR_TTL_SECONDS.

    Best-effort: silently skip on errors. Never touches the current
    session's own directory.
    """
    try:
        root = ptr_store._root()
        if not root.exists():
            return
        current = ptr_store._session_dir()
        cutoff = time.time() - PTR_TTL_SECONDS
        for entry in root.iterdir():
            if not entry.is_dir() or entry == current:
                continue
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
                for f in entry.iterdir():
                    try:
                        f.unlink()
                    except Exception:
                        pass
                entry.rmdir()
            except Exception:
                continue
    except Exception:
        return


def _route_db():
    """Lazy analytics DB for critical-surface route decisions."""
    global _ROUTE_DB, _ROUTE_DB_PATH
    try:
        from dhee.configs.base import _dhee_data_dir
        from dhee.db.sqlite import SQLiteManager

        db_path = os.path.join(_dhee_data_dir(), "history.db")
        if _ROUTE_DB is not None and _ROUTE_DB_PATH == db_path:
            return _ROUTE_DB
        if _ROUTE_DB is not None and hasattr(_ROUTE_DB, "close"):
            try:
                _ROUTE_DB.close()
            except Exception:
                pass
        _ROUTE_DB = SQLiteManager(db_path)
        _ROUTE_DB_PATH = db_path
        return _ROUTE_DB
    except Exception:
        return None


def _record_route_decision(decision: Dict[str, Any]) -> None:
    db = _route_db()
    if db is None:
        return
    try:
        db.record_route_decision(decision)
    except Exception:
        return


def handle_dhee_read(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Digest-returning wrapper for file reads."""
    _evict_stale_ptr_sessions()

    file_path = str(arguments.get("file_path", "")).strip()
    if not file_path:
        return {"error": "file_path is required"}

    offset_raw = arguments.get("offset")
    limit_raw = arguments.get("limit")
    try:
        offset = None if offset_raw is None else int(offset_raw)
    except (TypeError, ValueError):
        return {"error": f"offset must be int or None, got: {offset_raw!r}"}
    try:
        limit = None if limit_raw is None else int(limit_raw)
    except (TypeError, ValueError):
        return {"error": f"limit must be int or None, got: {limit_raw!r}"}
    if limit is not None and limit < 0:
        return {"error": f"limit must be >= 0, got: {limit}"}
    # Phase 4/8: if caller didn't pin depth, consult the tuned policy.
    intent_label = _intent.classify_read(file_path)
    explicit_depth = arguments.get("digest_depth")
    if explicit_depth in ("shallow", "normal", "deep"):
        depth = explicit_depth
    else:
        depth = _policy.get_depth("Read", intent_label)
    shared_event_id = _shared_event_id("Read", file_path, offset, limit, depth)
    _publish_shared_claim(
        packet_kind="routed_read",
        tool_name="Read",
        digest=f"Reading {file_path} (intent={intent_label}, depth={depth})",
        source_path=file_path,
        source_event_id=shared_event_id,
        metadata={
            "intent": intent_label,
            "depth": depth,
            "offset": offset,
            "limit": limit,
        },
    )

    try:
        size = os.path.getsize(file_path)
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}
    except PermissionError:
        return {"error": f"Permission denied: {file_path}"}
    except OSError as exc:
        return {"error": f"Stat failed: {type(exc).__name__}: {exc}"}

    if size > MAX_READ_BYTES and offset is None and limit is None:
        return {
            "error": (
                f"File too large ({size} bytes > {MAX_READ_BYTES} cap). "
                "Pass offset+limit to read a range."
            ),
            "file_size": size,
            "max_read_bytes": MAX_READ_BYTES,
        }

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except FileNotFoundError:
        return {"error": f"File not found: {file_path}"}
    except PermissionError:
        return {"error": f"Permission denied: {file_path}"}
    except IsADirectoryError:
        return {"error": f"Is a directory: {file_path}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Read failed: {type(exc).__name__}: {exc}"}

    total_lines = len(all_lines)
    if offset is not None or limit is not None:
        o = max(1, offset if offset is not None else 1)
        lim = limit if limit is not None else total_lines
        start = o - 1
        end = start + lim
        selected = all_lines[start:end]
        range_ = (o, o + len(selected) - 1) if selected else (o, o - 1)
    else:
        selected = all_lines
        range_ = None

    content = "".join(selected)
    d = _digest.digest_read(
        path=file_path,
        text=content,
        depth=depth,
        range_=range_,
    )
    stored = ptr_store.store(
        content,
        tool="Read",
        meta={
            "file_path": file_path,
            "offset": offset,
            "limit": limit,
            "line_count": d.line_count,
            "char_count": d.char_count,
            "est_tokens": d.est_tokens,
            "kind": d.kind,
            "intent": intent_label,
            "depth": depth,
        },
    )
    rendered = d.render(stored.ptr, depth=depth)
    inlined = False
    if d.char_count < INLINE_INFLATION_THRESHOLD and len(rendered) >= len(content):
        rendered = _inline_read(content, stored.ptr, file_path, d.line_count, d.char_count)
        inlined = True
    _record_route_decision(
        _critical_surface.routed_read_decision(
            source_path=file_path,
            intent=intent_label,
            depth=depth,
            raw_text=content,
            rendered_text=rendered,
            inlined=inlined,
            cwd=os.getcwd(),
            source_event_id=stored.ptr,
        )
    )
    _publish_shared_result(
        packet_kind="routed_read",
        tool_name="Read",
        digest=rendered,
        source_path=file_path,
        source_event_id=shared_event_id,
        ptr=stored.ptr,
        metadata={
            "intent": intent_label,
            "depth": depth,
            "offset": offset,
            "limit": limit,
            "line_count": d.line_count,
            "char_count": d.char_count,
            "est_tokens": d.est_tokens,
            "kind": d.kind,
            "inlined": inlined,
        },
    )
    return {
        "ptr": stored.ptr,
        "digest": rendered,
        "line_count": d.line_count,
        "char_count": d.char_count,
        "est_tokens": d.est_tokens,
        "kind": d.kind,
        "inlined": inlined,
    }


def handle_dhee_bash(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a shell command and return a class-aware digest + ptr.

    Full stdout/stderr is stored under the pointer; only the digest is
    returned. The digest branches on command class (git_log, pytest,
    listing, grep, generic) for a more useful summary than a plain
    truncation.
    """
    _evict_stale_ptr_sessions()

    cmd = str(arguments.get("command", "")).strip()
    if not cmd:
        return {"error": "command is required"}

    try:
        timeout = float(arguments.get("timeout", BASH_DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = BASH_DEFAULT_TIMEOUT
    timeout = max(1.0, min(timeout, BASH_MAX_TIMEOUT))

    cwd = arguments.get("cwd")
    if cwd and not os.path.isdir(cwd):
        return {"error": f"cwd does not exist: {cwd}"}
    shared_event_id = _shared_event_id("Bash", cwd or os.getcwd(), cmd)
    _publish_shared_claim(
        packet_kind="routed_bash",
        tool_name="Bash",
        digest=f"Running bash in {cwd or os.getcwd()}: {cmd}",
        source_path=str(cwd or os.getcwd()),
        source_event_id=shared_event_id,
        metadata={"command": cmd, "cwd": cwd},
    )

    started = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            text=False,
        )
        stdout_raw = proc.stdout or b""
        stderr_raw = proc.stderr or b""
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout_raw = exc.stdout or b""
        stderr_raw = exc.stderr or b""
        exit_code = 124
    except Exception as exc:  # noqa: BLE001
        return {"error": f"exec failed: {type(exc).__name__}: {exc}"}
    duration_ms = int((time.perf_counter() - started) * 1000)

    truncated_streams: list[str] = []
    if len(stdout_raw) > BASH_MAX_OUTPUT_BYTES:
        stdout_raw = stdout_raw[:BASH_MAX_OUTPUT_BYTES]
        truncated_streams.append("stdout")
    if len(stderr_raw) > BASH_MAX_OUTPUT_BYTES:
        stderr_raw = stderr_raw[:BASH_MAX_OUTPUT_BYTES]
        truncated_streams.append("stderr")

    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")

    d = _bash_digest.digest_bash(
        cmd=cmd,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout=stdout,
        stderr=stderr,
    )
    if timed_out:
        d.notes.append(f"TIMED OUT after {timeout:.0f}s")
    for s in truncated_streams:
        d.notes.append(f"{s} truncated to {BASH_MAX_OUTPUT_BYTES} bytes")

    raw_blob = (
        f"$ {cmd}\n"
        f"[exit={exit_code} duration={duration_ms}ms]\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}\n"
    )
    stored = ptr_store.store(
        raw_blob,
        tool="Bash",
        meta={
            "command": cmd,
            "cwd": cwd,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "class": d.cls,
            "intent": d.cls,  # bash class == intent bucket
            "stdout_bytes": d.stdout_bytes,
            "stderr_bytes": d.stderr_bytes,
            "timed_out": timed_out,
        },
    )
    rendered = d.render(stored.ptr)
    raw_output_bytes = d.stdout_bytes + d.stderr_bytes
    inlined = False
    if raw_output_bytes < INLINE_INFLATION_THRESHOLD and len(rendered) >= raw_output_bytes:
        rendered = _inline_bash(cmd, exit_code, duration_ms, stdout, stderr, stored.ptr)
        inlined = True
    _record_route_decision(
        _critical_surface.routed_bash_decision(
            command=cmd,
            cls=d.cls,
            raw_output_bytes=raw_output_bytes,
            rendered_text=rendered,
            inlined=inlined,
            cwd=str(cwd or os.getcwd()),
            source_event_id=stored.ptr,
            exit_code=exit_code,
            timed_out=timed_out,
        )
    )
    _publish_shared_result(
        packet_kind="routed_bash",
        tool_name="Bash",
        digest=rendered,
        source_path=str(cwd or os.getcwd()),
        source_event_id=shared_event_id,
        ptr=stored.ptr,
        metadata={
            "command": cmd,
            "cwd": cwd,
            "class": d.cls,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "stdout_bytes": d.stdout_bytes,
            "stderr_bytes": d.stderr_bytes,
            "timed_out": timed_out,
            "inlined": inlined,
        },
    )
    return {
        "ptr": stored.ptr,
        "digest": rendered,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "class": d.cls,
        "stdout_bytes": d.stdout_bytes,
        "stderr_bytes": d.stderr_bytes,
        "timed_out": timed_out,
        "inlined": inlined,
    }


def handle_dhee_agent(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Digest a subagent (or other long-text) tool return.

    Accepts `text` (the blob to digest) and an optional `kind` hint.
    Extracts file:line references, headings, bullets, error signals,
    and head/tail excerpts. Stores raw under a ptr.
    """
    _evict_stale_ptr_sessions()

    text = arguments.get("text")
    if not isinstance(text, str) or not text:
        return {"error": "text is required (non-empty string)"}

    if len(text.encode("utf-8", errors="replace")) > AGENT_MAX_TEXT_BYTES:
        return {
            "error": (
                f"text too large (>{AGENT_MAX_TEXT_BYTES} bytes). "
                "Split before digesting."
            )
        }

    kind = arguments.get("kind")
    if not isinstance(kind, str):
        kind = None

    source = arguments.get("source")
    if not isinstance(source, str):
        source = None

    d = _agent_digest.digest_agent(text, kind=kind)
    stored = ptr_store.store(
        text,
        tool="Agent",
        meta={
            "source": source,
            "kind": d.kind,
            "intent": d.kind,  # agent digest kind == intent bucket
            "char_count": d.char_count,
            "line_count": d.line_count,
            "est_tokens": d.est_tokens,
            "file_refs": d.file_refs[:50],
            "error_hits": d.error_hits,
        },
    )
    return {
        "ptr": stored.ptr,
        "digest": d.render(stored.ptr),
        "char_count": d.char_count,
        "line_count": d.line_count,
        "est_tokens": d.est_tokens,
        "kind": d.kind,
        "file_ref_count": len(d.file_refs),
    }


def handle_dhee_grep(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Run a pattern search, return a digest + ptr to the full hit list."""
    _evict_stale_ptr_sessions()

    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return {"error": "pattern is required (non-empty string)"}
    path = arguments.get("path")
    if not isinstance(path, str) or not path:
        path = "."

    glob = arguments.get("glob")
    if glob is not None and not isinstance(glob, str):
        return {"error": f"glob must be a string, got: {type(glob).__name__}"}

    case_insensitive = bool(arguments.get("-i") or arguments.get("case_insensitive"))
    fixed_string = bool(arguments.get("fixed_string"))
    multiline = bool(arguments.get("multiline"))
    try:
        context = int(arguments.get("context") or arguments.get("-C") or 0)
    except (TypeError, ValueError):
        context = 0
    if context < 0:
        context = 0
    shared_event_id = _shared_event_id(
        "Grep",
        path,
        pattern,
        glob,
        case_insensitive,
        fixed_string,
        multiline,
        context,
    )
    _publish_shared_claim(
        packet_kind="routed_grep",
        tool_name="Grep",
        digest=f"Searching {path} for {pattern!r}",
        source_path=path,
        source_event_id=shared_event_id,
        metadata={
            "pattern": pattern,
            "glob": glob,
            "case_insensitive": case_insensitive,
            "fixed_string": fixed_string,
            "multiline": multiline,
            "context": context,
        },
    )

    try:
        digest, raw = _grep_digest.digest_grep(
            pattern=pattern,
            path=path,
            glob=glob,
            case_insensitive=case_insensitive,
            fixed_string=fixed_string,
            multiline=multiline,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"grep failed: {type(exc).__name__}: {exc}"}

    stored = ptr_store.store(
        raw,
        tool="Grep",
        meta={
            "pattern": pattern,
            "path": path,
            "glob": glob,
            "case_insensitive": case_insensitive,
            "fixed_string": fixed_string,
            "multiline": multiline,
            "context": context,
            "match_count": digest.match_count,
            "file_count": digest.file_count,
            "total_bytes": digest.total_bytes,
            "est_tokens": digest.est_tokens,
            "engine": digest.engine,
            "intent": "grep",
        },
    )
    rendered = digest.render(stored.ptr)
    inlined = False
    if digest.total_bytes < INLINE_INFLATION_THRESHOLD and len(rendered) >= digest.total_bytes:
        rendered = (
            f'<dhee_grep ptr="{stored.ptr}" inlined="1">\n'
            f"pattern={pattern}\n"
            f"path={path}\n"
            f"matches={digest.match_count} files={digest.file_count} "
            f"(inlined — digest not shorter)\n"
            f"{raw}\n"
            f"</dhee_grep>"
        )
        inlined = True
    _record_route_decision(
        _critical_surface.routed_grep_decision(
            search_path=path,
            pattern=pattern,
            match_count=digest.match_count,
            file_count=digest.file_count,
            total_bytes=digest.total_bytes,
            rendered_text=rendered,
            inlined=inlined,
            cwd=os.getcwd(),
            source_event_id=stored.ptr,
        )
    )
    _publish_shared_result(
        packet_kind="routed_grep",
        tool_name="Grep",
        digest=rendered,
        source_path=path,
        source_event_id=shared_event_id,
        ptr=stored.ptr,
        metadata={
            "pattern": pattern,
            "glob": glob,
            "match_count": digest.match_count,
            "file_count": digest.file_count,
            "total_bytes": digest.total_bytes,
            "est_tokens": digest.est_tokens,
            "engine": digest.engine,
            "inlined": inlined,
        },
    )
    return {
        "ptr": stored.ptr,
        "digest": rendered,
        "match_count": digest.match_count,
        "file_count": digest.file_count,
        "total_bytes": digest.total_bytes,
        "est_tokens": digest.est_tokens,
        "engine": digest.engine,
        "inlined": inlined,
    }


def _slice_by_range(content: str, range_spec: Any) -> tuple[str, dict[str, Any]]:
    """Return (sliced_text, {'range': (start, end), 'total_lines': N}).

    Accepts ``range_spec`` as ``"start:end"`` or ``[start, end]``. Lines
    are 1-indexed inclusive. Clamps to valid range and never raises.
    """
    lines = content.splitlines(keepends=True)
    total = len(lines)
    start: int | None = None
    end: int | None = None
    try:
        if isinstance(range_spec, str) and ":" in range_spec:
            a, b = range_spec.split(":", 1)
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else total
        elif isinstance(range_spec, (list, tuple)) and len(range_spec) == 2:
            start = int(range_spec[0])
            end = int(range_spec[1])
    except (TypeError, ValueError):
        start = end = None
    if start is None or end is None:
        return content, {"range": None, "total_lines": total}
    start = max(1, start)
    end = max(start, min(end, total))
    sliced = "".join(lines[start - 1 : end])
    return sliced, {"range": [start, end], "total_lines": total}


_SYMBOL_PATTERNS = [
    # Python def/class, JS/TS function/class/const fn
    (r"^\s*(?:async\s+)?def\s+{name}\b", "python_def"),
    (r"^\s*class\s+{name}\b", "python_class"),
    (r"^\s*(?:export\s+)?(?:async\s+)?function\s+{name}\b", "js_function"),
    (r"^\s*(?:export\s+)?class\s+{name}\b", "js_class"),
    (r"^\s*const\s+{name}\s*=\s*(?:async\s*)?\(", "js_const_fn"),
]


def _slice_by_symbol(content: str, symbol: str) -> tuple[str, dict[str, Any]]:
    """Locate `symbol` in the content; return the block spanning from the
    definition line to the next dedent (or a 200-line window, whichever
    is smaller). If not found, returns the full content with a note.
    """
    import re as _re

    lines = content.splitlines(keepends=True)
    total = len(lines)
    name_rx = _re.escape(symbol)
    start_idx = -1
    match_kind = ""
    for tmpl, kind in _SYMBOL_PATTERNS:
        rx = _re.compile(tmpl.replace("{name}", name_rx))
        for i, ln in enumerate(lines):
            if rx.search(ln):
                start_idx = i
                match_kind = kind
                break
        if start_idx >= 0:
            break
    if start_idx < 0:
        return content, {"symbol": symbol, "found": False, "total_lines": total}

    def _indent(s: str) -> int:
        stripped = s.lstrip()
        if not stripped:
            return -1  # blank line; treat as continuation
        return len(s) - len(stripped)

    base = _indent(lines[start_idx])
    end_idx = start_idx + 1
    max_span = min(total, start_idx + 200)
    while end_idx < max_span:
        ind = _indent(lines[end_idx])
        if ind >= 0 and ind <= base and lines[end_idx].strip():
            break
        end_idx += 1
    sliced = "".join(lines[start_idx:end_idx])
    return sliced, {
        "symbol": symbol,
        "found": True,
        "kind": match_kind,
        "range": [start_idx + 1, end_idx],
        "total_lines": total,
    }


def handle_dhee_expand_result(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve raw content stored by a dhee_* router tool.

    Optional slicing keeps the expansion lean:
      - ``range="start:end"`` or ``range=[start, end]`` — 1-indexed lines
      - ``symbol="MyClass"`` — return just the block for the named def/class

    When both are provided, ``range`` wins. With neither, the full raw
    content is returned (original behaviour).
    """
    ptr = str(arguments.get("ptr", "")).strip()
    if not ptr:
        return {"error": "ptr is required"}
    content = ptr_store.load(ptr)
    if content is None:
        return {"error": f"ptr not found: {ptr}"}
    meta = ptr_store.load_meta(ptr) or {}

    slice_info: dict[str, Any] = {}
    range_spec = arguments.get("range")
    symbol = arguments.get("symbol")
    sliced = content
    if range_spec is not None:
        sliced, slice_info = _slice_by_range(content, range_spec)
        slice_info["mode"] = "range"
    elif isinstance(symbol, str) and symbol.strip():
        sliced, slice_info = _slice_by_symbol(content, symbol.strip())
        slice_info["mode"] = "symbol"

    ptr_store.record_expansion(
        ptr,
        tool=str(meta.get("tool") or ""),
        intent=str(meta.get("intent") or meta.get("class") or meta.get("kind") or ""),
        depth=str(meta.get("depth") or ""),
    )
    result: dict[str, Any] = {"ptr": ptr, "meta": meta, "content": sliced}
    if slice_info:
        result["slice"] = slice_info
    return result
