"""Shared handler functions for Dhee router MCP tools.

Exposed by both `dhee-mcp` (slim) and `dhee-mcp-full`. Handlers take a
plain argument dict and return a plain dict — no memory/plugin
dependency, no MCP framework coupling.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from dhee.router import agent_digest as _agent_digest
from dhee.router import bash_digest as _bash_digest
from dhee.router import digest as _digest
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
# older than this when the router is invoked.
PTR_TTL_SECONDS = 24 * 3600  # 24 h

# Inflation floor. When raw input is small, a full digest wrapper can
# exceed the raw content, *losing* tokens. Below this size we compare
# the rendered digest against the raw and fall back to a minimal
# inlined wrapper when the digest would inflate the payload. Set
# empirically: beyond 2 KB the digest is always cheaper because the
# symbol + head/tail summary compresses well; under that it often
# isn't.
INLINE_INFLATION_THRESHOLD = 2048


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


def handle_dhee_expand_result(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Retrieve raw content stored by a dhee_* router tool."""
    ptr = str(arguments.get("ptr", "")).strip()
    if not ptr:
        return {"error": "ptr is required"}
    content = ptr_store.load(ptr)
    if content is None:
        return {"error": f"ptr not found: {ptr}"}
    meta = ptr_store.load_meta(ptr) or {}
    ptr_store.record_expansion(
        ptr,
        tool=str(meta.get("tool") or ""),
        intent=str(meta.get("intent") or meta.get("class") or meta.get("kind") or ""),
        depth=str(meta.get("depth") or ""),
    )
    return {"ptr": ptr, "meta": meta, "content": content}
