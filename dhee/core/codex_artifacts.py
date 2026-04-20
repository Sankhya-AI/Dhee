"""Codex transcript ingestion for host-parsed artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from dhee.core.artifacts import ArtifactManager, find_prompt_file_references, is_supported_artifact_path


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


def ingest_codex_session_log(
    manager: ArtifactManager,
    jsonl_path: str,
    *,
    user_id: str = "default",
) -> Dict[str, int]:
    stats = {
        "logs": 0,
        "attached": 0,
        "parsed": 0,
        "chunks_indexed": 0,
    }
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"Codex session log not found: {jsonl_path}")

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            stats["logs"] += 1
            _ingest_entry(entry, manager, user_id=user_id, stats=stats)
    return stats


def _ingest_entry(
    entry: Dict[str, Any],
    manager: ArtifactManager,
    *,
    user_id: str,
    stats: Dict[str, int],
) -> None:
    entry_type = entry.get("type")
    payload = entry.get("payload", {}) or {}
    if entry_type == "response_item":
        _ingest_response_item(payload, manager, user_id=user_id, stats=stats)
        return
    if entry_type == "event_msg" and payload.get("type") == "exec_command_end":
        _ingest_exec_command(payload, manager, user_id=user_id, stats=stats)


def _ingest_response_item(
    payload: Dict[str, Any],
    manager: ArtifactManager,
    *,
    user_id: str,
    stats: Dict[str, int],
) -> None:
    if payload.get("type") != "message" or payload.get("role") != "user":
        return
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


def _ingest_exec_command(
    payload: Dict[str, Any],
    manager: ArtifactManager,
    *,
    user_id: str,
    stats: Dict[str, int],
) -> None:
    cwd = str(payload.get("cwd") or "")
    aggregated_output = str(
        payload.get("aggregated_output")
        or payload.get("stdout")
        or ""
    ).strip()
    parsed_cmds = payload.get("parsed_cmd", []) or []
    for parsed in _iter_read_commands(parsed_cmds):
        raw_path = str(parsed.get("path") or parsed.get("name") or "").strip()
        if not raw_path:
            continue
        if not is_supported_artifact_path(raw_path):
            continue
        artifact = manager.attach(
            raw_path,
            user_id=user_id,
            cwd=cwd,
            harness="codex",
            binding_source="artifact_attached",
            metadata={"call_id": payload.get("call_id")},
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
                "call_id": payload.get("call_id"),
                "command": payload.get("command"),
            },
        )
        if result:
            stats["parsed"] += 1
            stats["chunks_indexed"] += int(result.get("indexed_count", 0))


def _iter_read_commands(parsed_cmds: Iterable[Any]) -> Iterable[Dict[str, Any]]:
    for parsed in parsed_cmds:
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") != "read":
            continue
        yield parsed
