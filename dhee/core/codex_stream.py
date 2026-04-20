"""Incremental native Codex event-stream ingestion.

Codex persists a structured JSONL event stream under ``~/.codex/sessions``.
This module tails that stream incrementally and projects the useful parts
into Dhee's shared-task bus and artifact substrate:

* user file references -> ``artifact_attached``
* native shell/function calls -> shared-task ``in_flight`` claims
* completed shell events -> shared-task completed results + ptr-backed digests
* successful host reads -> durable artifact parses

The cursor lives in Dhee's SQLite analytics DB so Codex can keep using its
native tools while Dhee still sees post-tool results without re-reading the
entire transcript every turn.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from dhee.core.artifacts import ArtifactManager, find_prompt_file_references, is_supported_artifact_path
from dhee.core.shared_tasks import publish_in_flight, publish_shared_task_result
from dhee.router import bash_digest as _bash_digest
from dhee.router import ptr_store


def find_latest_codex_log(root: Optional[str] = None) -> Optional[str]:
    sessions_root = Path(root or Path.home() / ".codex" / "sessions")
    if not sessions_root.exists():
        return None
    jsonl_files = sorted(
        sessions_root.glob("**/*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return str(jsonl_files[0]) if jsonl_files else None


def sync_latest_codex_stream(
    manager: ArtifactManager,
    db: Any,
    *,
    user_id: str = "default",
    sessions_root: Optional[str] = None,
    log_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Incrementally ingest the newest Codex event stream."""
    resolved = os.path.abspath(
        str(log_path or find_latest_codex_log(sessions_root) or "")
    )
    if not resolved:
        return {"status": "no_log", "logs": 0}
    path = Path(resolved)
    if not path.exists():
        return {"status": "missing_log", "log_path": resolved, "logs": 0}

    stream_id = resolved
    cursor = db.get_harness_stream_cursor(
        user_id=user_id,
        harness="codex",
        stream_id=stream_id,
    )
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0
    start_offset = int((cursor or {}).get("byte_offset") or 0)
    if start_offset > file_size:
        start_offset = 0

    stats = {
        "status": "ok",
        "log_path": resolved,
        "logs": 0,
        "claims": 0,
        "completed": 0,
        "attached": 0,
        "parsed": 0,
        "chunks_indexed": 0,
        "start_offset": start_offset,
        "end_offset": start_offset,
    }

    with path.open("rb") as handle:
        handle.seek(start_offset)
        while True:
            line = handle.readline()
            if not line:
                break
            stats["end_offset"] = handle.tell()
            decoded = line.decode("utf-8", errors="replace").strip()
            if not decoded:
                continue
            try:
                entry = json.loads(decoded)
            except json.JSONDecodeError:
                continue
            stats["logs"] += 1
            _ingest_entry(entry, manager, db, user_id=user_id, stats=stats)

    db.upsert_harness_stream_cursor(
        {
            "user_id": user_id,
            "harness": "codex",
            "stream_id": stream_id,
            "byte_offset": stats["end_offset"],
            "metadata": {"logs_seen": stats["logs"]},
        }
    )
    return stats


def _ingest_entry(
    entry: Dict[str, Any],
    manager: ArtifactManager,
    db: Any,
    *,
    user_id: str,
    stats: Dict[str, Any],
) -> None:
    entry_type = entry.get("type")
    payload = entry.get("payload", {}) or {}
    if entry_type == "response_item":
        _ingest_response_item(payload, manager, db, user_id=user_id, stats=stats)
        return
    if entry_type == "event_msg" and payload.get("type") == "exec_command_end":
        _ingest_exec_command(payload, manager, db, user_id=user_id, stats=stats)


def _ingest_response_item(
    payload: Dict[str, Any],
    manager: ArtifactManager,
    db: Any,
    *,
    user_id: str,
    stats: Dict[str, Any],
) -> None:
    if payload.get("type") == "message" and payload.get("role") == "user":
        texts = []
        for block in payload.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"input_text", "output_text", "text"}:
                text = str(block.get("text", "")).strip()
                if text:
                    texts.append(text)
        if not texts:
            return
        combined = "\n".join(texts)
        for ref in find_prompt_file_references(combined):
            if not is_supported_artifact_path(ref):
                continue
            artifact = manager.attach(
                ref,
                user_id=user_id,
                harness="codex",
                binding_source="artifact_attached",
            )
            if artifact is not None:
                stats["attached"] += 1
        return

    if payload.get("type") != "function_call":
        return

    tool_name = str(payload.get("name") or "").strip()
    if tool_name not in {"exec_command", "shell"}:
        return
    args = _parse_tool_arguments(payload.get("arguments"))
    command = _command_text(args)
    cwd = str(args.get("cwd") or args.get("workdir") or "").strip()
    call_id = str(payload.get("call_id") or payload.get("id") or "").strip()
    result = publish_in_flight(
        db,
        user_id=user_id,
        packet_kind="native_bash",
        tool_name=tool_name or "exec_command",
        digest=(f"Codex running {command}" if command else "Codex running a native tool"),
        repo=cwd or None,
        cwd=cwd or None,
        source_path=cwd or None,
        source_event_id=call_id or None,
        metadata={
            "command": command,
            "native_tool": tool_name,
            "from_log": True,
        },
        harness="codex",
        agent_id="codex",
    )
    if result is not None:
        stats["claims"] += 1


def _ingest_exec_command(
    payload: Dict[str, Any],
    manager: ArtifactManager,
    db: Any,
    *,
    user_id: str,
    stats: Dict[str, Any],
) -> None:
    cwd = str(payload.get("cwd") or "").strip()
    command = _payload_command_text(payload)
    call_id = str(payload.get("call_id") or "").strip()
    aggregated_output = str(
        payload.get("aggregated_output")
        or payload.get("stdout")
        or ""
    ).strip()
    stderr = str(payload.get("stderr") or "").strip()
    exit_code = _int_or(payload.get("exit_code"), 0)
    duration_ms = _int_or(payload.get("duration_ms"), 0)

    if command or aggregated_output or stderr:
        digest = _bash_digest.digest_bash(
            cmd=command or "exec_command",
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=aggregated_output,
            stderr=stderr,
        )
        raw_blob = (
            f"$ {command or 'exec_command'}\n"
            f"[exit={exit_code} duration={duration_ms}ms]\n"
            f"--- stdout ---\n{aggregated_output}\n"
            f"--- stderr ---\n{stderr}\n"
        )
        stored = ptr_store.store(
            raw_blob,
            tool="CodexExec",
            meta={
                "command": command,
                "cwd": cwd,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "class": digest.cls,
                "native_tool": "exec_command",
                "harness": "codex",
            },
        )
        shared = publish_shared_task_result(
            db,
            user_id=user_id,
            packet_kind="native_bash",
            tool_name="exec_command",
            digest=digest.render(stored.ptr),
            repo=cwd or None,
            cwd=cwd or None,
            source_path=cwd or None,
            source_event_id=call_id or None,
            ptr=stored.ptr,
            metadata={
                "command": command,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "class": digest.cls,
                "stdout_bytes": digest.stdout_bytes,
                "stderr_bytes": digest.stderr_bytes,
                "from_log": True,
            },
            harness="codex",
            agent_id="codex",
        )
        if shared is not None:
            stats["completed"] += 1

    parsed_cmds = payload.get("parsed_cmd", []) or []
    for parsed in _iter_read_commands(parsed_cmds):
        raw_path = str(parsed.get("path") or parsed.get("name") or "").strip()
        if not raw_path or not is_supported_artifact_path(raw_path):
            continue
        artifact = manager.attach(
            raw_path,
            user_id=user_id,
            cwd=cwd,
            harness="codex",
            binding_source="artifact_attached",
            metadata={"call_id": call_id},
        )
        if artifact is not None:
            stats["attached"] += 1
        if not aggregated_output:
            continue
        result = manager.capture_host_parse(
            path=raw_path,
            extracted_text=aggregated_output,
            user_id=user_id,
            cwd=cwd,
            harness="codex",
            extraction_source="codex_exec_command",
            extraction_version="host-v1",
            metadata={
                "call_id": call_id,
                "command": command,
            },
        )
        if result:
            stats["parsed"] += 1
            stats["chunks_indexed"] += int(result.get("indexed_count", 0))


def _parse_tool_arguments(arguments: Any) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _command_text(args: Dict[str, Any]) -> str:
    command = args.get("cmd") or args.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if isinstance(command, str):
        return command.strip()
    return ""


def _payload_command_text(payload: Dict[str, Any]) -> str:
    command = payload.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if isinstance(command, str):
        return command.strip()
    return ""


def _iter_read_commands(parsed_cmds: Iterable[Any]) -> Iterable[Dict[str, Any]]:
    for parsed in parsed_cmds:
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") != "read":
            continue
        yield parsed


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
