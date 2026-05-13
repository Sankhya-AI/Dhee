"""Privacy-safe replay corpus harvesting.

The router replay harness is only strategically useful when it can run on
representative real sessions. Raw Claude/Codex transcripts are too sensitive
to check in or share, so this module converts them into redacted replay
fixtures that preserve the tool-call shape, output size, exit status, and
high-level failure/success signals without storing prompts, source text,
absolute paths, or secrets.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dhee.benchmarks.router_replay import (
    _PROJECTORS,
    _command_text,
    _detect_harness,
    _flatten_result,
    _jsonl_records,
    _loads_tool_args,
    _normalise_codex_tool,
    aggregate_reports,
    discover_transcripts,
    load_golden_annotations,
    replay_session,
)
from dhee.hooks.claude_code.privacy import filter_secrets


_PATHISH_KEYS = {
    "path",
    "file_path",
    "filepath",
    "cwd",
    "workdir",
    "directory",
    "repo",
    "root",
}
_OUTPUT_KEYS = {
    "stdout",
    "stderr",
    "output",
    "aggregated_output",
    "content",
    "result",
}
_KEEP_SCALARS = {"limit", "offset", "exit_code", "duration_ms", "timeout_ms"}
_GENERIC_SESSION_PREFIX = "redacted_real"


@dataclass
class HarvestedSession:
    session_id: str
    harness: str
    output_path: str
    source_size_bytes: int
    source_path_sha256: str
    sanitized_records: int
    total_calls: int
    calls_by_tool: dict[str, int]
    raw_tokens: int
    digest_tokens: int
    saved_pct: float
    warnings_count: int
    annotation_status: str = "needs_review"


def _sha_text(value: str, *, chars: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:chars]


def _sha_file(path: Path, *, chars: int = 16) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        h.update(str(path).encode("utf-8", errors="replace"))
    return h.hexdigest()[:chars]


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def _safe_suffix(value: str) -> str:
    suffix = Path(value).suffix.lower()
    if 1 <= len(suffix) <= 10 and re.fullmatch(r"\.[a-z0-9_+-]+", suffix):
        return suffix
    return ".txt"


def sanitize_path(value: Any) -> str:
    """Return a stable placeholder path without preserving local details."""
    text = filter_secrets(str(value or "")).strip()
    if not text:
        return "<path>/unknown.txt"
    return f"<path>/file_{_sha_text(text)}{_safe_suffix(text)}"


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _normalise_program(token: str) -> str:
    name = Path(token).name
    if name in {"python3", "python3.9", "python3.10", "python3.11", "python3.12", "python3.13", "python3.14"}:
        return "python"
    return name or token


def sanitize_command(command: Any) -> str:
    """Keep the command class, remove args that could reveal local work."""
    text = filter_secrets(_command_text(command)).strip()
    if not text:
        return "<empty-command>"
    tokens = _command_tokens(text)
    lowered = [_normalise_program(t).lower() for t in tokens]

    if "pytest" in lowered:
        return "python -m pytest tests/test_redacted.py -q"
    if lowered[:3] == ["python", "-m", "pytest"]:
        return "python -m pytest tests/test_redacted.py -q"
    if lowered and lowered[0] in {"uv", "poetry"} and "pytest" in lowered:
        return f"{lowered[0]} run python -m pytest tests/test_redacted.py -q"
    if "npm" in lowered:
        if "test" in lowered:
            return "npm test -- --runInBand"
        if "build" in lowered:
            return "npm run build"
        return "npm <redacted-args>"
    if "pnpm" in lowered:
        if "test" in lowered:
            return "pnpm test"
        if "build" in lowered:
            return "pnpm build"
        return "pnpm <redacted-args>"
    if "yarn" in lowered:
        if "test" in lowered:
            return "yarn test"
        if "build" in lowered:
            return "yarn build"
        return "yarn <redacted-args>"
    if lowered and lowered[0] == "git":
        sub = lowered[1] if len(lowered) > 1 else "status"
        if sub in {"status", "diff", "show", "log", "branch", "rev-parse", "ls-files"}:
            return f"git {sub} <redacted-args>"
        return "git <redacted-args>"
    if lowered and lowered[0] in {"rg", "grep"}:
        return f"{lowered[0]} <pattern> <path>"
    if lowered and lowered[0] in {"cat", "sed", "head", "tail", "nl"}:
        return "sed -n '1,120p' <path>"
    if lowered and lowered[0] in {"ls", "find", "fd"}:
        return f"{lowered[0]} <path>"
    if lowered and lowered[0] in {"make", "cargo", "go"}:
        sub = lowered[1] if len(lowered) > 1 else ""
        return f"{lowered[0]} {sub}".strip()
    program = _normalise_program(tokens[0])
    return f"{program} <redacted-args>"


def _pad_placeholder(base: str, target_len: int) -> str:
    if target_len <= 0:
        return base
    if len(base) >= target_len:
        return base
    return base + ("." * (target_len - len(base)))


def _sanitize_line(line: str) -> str:
    filtered = filter_secrets(line)
    target_len = max(16, min(len(filtered), 240))
    stripped = filtered.strip()
    if not stripped:
        return ""
    summary_parts = re.findall(
        r"\d+\s+(?:passed|failed|failures?|errors?|skipped|warnings?)",
        stripped,
        flags=re.IGNORECASE,
    )
    if summary_parts and len(stripped) < 220:
        return _pad_placeholder(" ".join(summary_parts), target_len)
    if re.search(r"\bFAILED\b|\bERROR\b|AssertionError|Traceback", stripped):
        return _pad_placeholder(
            "FAILED tests/test_redacted.py::test_case AssertionError: redacted",
            target_len,
        )
    if re.search(r"\bPASSED\b|\bok\b", stripped, flags=re.IGNORECASE):
        return _pad_placeholder(
            "tests/test_redacted.py::test_case PASSED",
            target_len,
        )
    if re.search(r"\bWARNING\b|\bDeprecationWarning\b", stripped):
        return _pad_placeholder("WARNING redacted warning text", target_len)
    if re.search(r"\b(exit code|return code)\b", stripped, flags=re.IGNORECASE):
        return _pad_placeholder("exit code: <redacted>", target_len)
    return _pad_placeholder(
        f"<redacted line sha={_sha_text(filtered)} chars={len(filtered)}>",
        target_len,
    )


def sanitize_output(value: Any, *, max_output_chars: int = 50_000) -> str:
    """Redact tool output while preserving rough token mass and result shape."""
    text = filter_secrets(str(value or ""))
    if not text:
        return ""
    max_chars = max(500, int(max_output_chars or 50_000))
    out: list[str] = []
    used = 0
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        sanitized = _sanitize_line(line)
        next_len = len(sanitized) + 1
        if used + next_len > max_chars:
            remaining_lines = len(lines) - idx
            remaining_chars = max(0, len(text) - used)
            out.append(
                f"<redacted {remaining_lines} additional lines; original_chars_remaining={remaining_chars}>"
            )
            break
        out.append(sanitized)
        used += next_len
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix


def _sanitize_scalar(key: str, value: Any, *, max_output_chars: int) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    lower = key.lower()
    if lower in _KEEP_SCALARS:
        return value
    if lower in _PATHISH_KEYS or lower.endswith("_path"):
        return sanitize_path(value)
    if lower in {"command", "cmd", "script"}:
        return sanitize_command(value)
    if lower in _OUTPUT_KEYS or lower.endswith("_output"):
        return sanitize_output(value, max_output_chars=max_output_chars)
    if lower in {"description", "prompt", "instructions", "query", "pattern"}:
        return f"<redacted {lower} sha={_sha_text(str(value))}>"
    text = filter_secrets(str(value))
    if len(text) <= 20 and re.fullmatch(r"[a-zA-Z0-9_.:/ -]+", text):
        return text
    return f"<redacted value sha={_sha_text(text)} chars={len(text)}>"


def sanitize_tool_input(
    tool_name: str,
    data: Any,
    *,
    max_output_chars: int = 50_000,
) -> dict[str, Any]:
    """Sanitize tool input but keep fields the replay projector needs."""
    raw = data if isinstance(data, dict) else _loads_tool_args(data)
    safe: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            safe[key] = sanitize_tool_input(
                tool_name,
                value,
                max_output_chars=max_output_chars,
            )
        elif isinstance(value, list):
            if key.lower() in {"command", "cmd"}:
                safe[key] = sanitize_command(value)
            else:
                safe[key] = [
                    sanitize_tool_input(tool_name, item, max_output_chars=max_output_chars)
                    if isinstance(item, dict)
                    else _sanitize_scalar(key, item, max_output_chars=max_output_chars)
                    for item in value
                ]
        else:
            safe[key] = _sanitize_scalar(key, value, max_output_chars=max_output_chars)

    if tool_name == "Bash":
        safe["command"] = sanitize_command(raw.get("command") or raw.get("cmd") or raw.get("script") or "")
    elif tool_name == "Read":
        safe["file_path"] = sanitize_path(raw.get("file_path") or raw.get("path") or "")
    elif tool_name in {"Task", "Agent"}:
        safe.setdefault("description", "<redacted agent task>")
        safe.setdefault("prompt", "<redacted agent prompt>")
    return safe


def _sanitize_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    out: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        try:
            value = int(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            out[key] = value
    return out


def _sanitize_claude_records(path: Path, *, max_output_chars: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    next_id = 1

    for rec in _jsonl_records(path):
        rec_type = rec.get("type")
        msg = rec.get("message") or rec
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue

        tool_blocks: list[dict[str, Any]] = []
        result_blocks: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = str(block.get("name") or "")
                old_id = str(block.get("id") or "")
                if not old_id or name not in _PROJECTORS:
                    continue
                new_id = f"tool-{next_id:04d}"
                next_id += 1
                id_map[old_id] = new_id
                tool_blocks.append(
                    {
                        "type": "tool_use",
                        "id": new_id,
                        "name": name,
                        "input": sanitize_tool_input(
                            name,
                            block.get("input") or {},
                            max_output_chars=max_output_chars,
                        ),
                    }
                )
            elif btype == "tool_result":
                old_id = str(block.get("tool_use_id") or "")
                if old_id not in id_map:
                    continue
                result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": id_map[old_id],
                        "content": sanitize_output(
                            _flatten_result(block.get("content")),
                            max_output_chars=max_output_chars,
                        ),
                    }
                )

        if rec_type == "assistant" and tool_blocks:
            records.append(
                {
                    "type": "assistant",
                    "message": {
                        "usage": _sanitize_usage(msg.get("usage")),
                        "content": tool_blocks,
                    },
                }
            )
        if result_blocks:
            records.append(
                {
                    "type": "user",
                    "message": {"content": result_blocks},
                }
            )

    return records


def _sanitize_codex_records(path: Path, *, max_output_chars: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    id_map: dict[str, str] = {}
    next_id = 1

    def mapped_call_id(old: str) -> str:
        nonlocal next_id
        if old and old in id_map:
            return id_map[old]
        new = f"call-{next_id:04d}"
        next_id += 1
        if old:
            id_map[old] = new
        return new

    for rec in _jsonl_records(path):
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        ptype = payload.get("type")
        if rec.get("type") == "response_item" and ptype == "function_call":
            original_name = str(payload.get("name") or "")
            original_args = _loads_tool_args(payload.get("arguments"))
            normalised = _normalise_codex_tool(original_name, original_args)
            if not normalised:
                continue
            tool, tool_input = normalised
            call_id = mapped_call_id(str(payload.get("call_id") or payload.get("id") or ""))
            sanitized_args = sanitize_tool_input(
                tool,
                tool_input,
                max_output_chars=max_output_chars,
            )
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": original_name or ("exec_command" if tool == "Bash" else "read_file"),
                        "call_id": call_id,
                        "arguments": json.dumps(sanitized_args, separators=(",", ":"), sort_keys=True),
                    },
                }
            )
            continue

        if rec.get("type") != "event_msg" or ptype not in {
            "exec_command_end",
            "function_call_output",
            "tool_result",
        }:
            continue
        old_call_id = str(payload.get("call_id") or payload.get("id") or payload.get("tool_call_id") or "")
        if old_call_id and old_call_id not in id_map and ptype != "exec_command_end":
            continue
        call_id = mapped_call_id(old_call_id)
        output = (
            payload.get("aggregated_output")
            or payload.get("output")
            or payload.get("stdout")
            or payload.get("content")
            or ""
        )
        safe_payload: dict[str, Any] = {
            "type": ptype,
            "call_id": call_id,
        }
        if payload.get("exit_code") is not None:
            safe_payload["exit_code"] = payload.get("exit_code")
        if payload.get("command") is not None:
            cmd = sanitize_command(payload.get("command"))
            safe_payload["command"] = ["/bin/sh", "-lc", cmd]
        if ptype == "exec_command_end":
            safe_payload["aggregated_output"] = sanitize_output(
                output,
                max_output_chars=max_output_chars,
            )
        else:
            safe_payload["output"] = sanitize_output(
                output,
                max_output_chars=max_output_chars,
            )
        if payload.get("stderr"):
            safe_payload["stderr"] = sanitize_output(
                payload.get("stderr"),
                max_output_chars=max_output_chars,
            )
        records.append({"type": "event_msg", "payload": safe_payload})

    return records


def sanitize_transcript(
    path: Path,
    output_path: Path,
    *,
    harness: str = "auto",
    max_output_chars: int = 50_000,
) -> HarvestedSession | None:
    selected = _detect_harness(path) if harness in {"auto", "all"} else harness
    if selected in {"codex", "codex_cli"}:
        records = _sanitize_codex_records(path, max_output_chars=max_output_chars)
        selected = "codex"
    else:
        records = _sanitize_claude_records(path, max_output_chars=max_output_chars)
        selected = "claude_code"
    if not records:
        return None

    _write_jsonl(output_path, records)
    report = replay_session(output_path, harness=selected)
    if report.total_calls <= 0:
        try:
            output_path.unlink()
        except OSError:
            pass
        return None
    stat = path.stat()
    return HarvestedSession(
        session_id=output_path.stem,
        harness=selected,
        output_path=str(output_path),
        source_size_bytes=stat.st_size,
        source_path_sha256=_sha_text(str(path.resolve()), chars=16),
        sanitized_records=len(records),
        total_calls=report.total_calls,
        calls_by_tool=dict(report.calls_by_tool),
        raw_tokens=report.raw_tokens,
        digest_tokens=report.digest_tokens,
        saved_pct=round(report.saved_pct, 2),
        warnings_count=len(report.warnings),
    )


def _session_output_path(source: Path, output_sessions_dir: Path, harness: str) -> Path:
    source_hash = _sha_file(source)
    return output_sessions_dir / f"{_GENERIC_SESSION_PREFIX}_{harness}_{source_hash}.jsonl"


def _write_review_annotations(path: Path, sessions: list[HarvestedSession]) -> None:
    records = [
        {
            "session_id": session.session_id,
            "task_parity": "needs_review",
            "stale_context_incidents": 0,
            "note": f"Harvested from redacted real {session.harness} session; requires human parity review before release gating.",
        }
        for session in sessions
    ]
    _write_jsonl(path, records)


def _manifest(
    *,
    sessions: list[HarvestedSession],
    aggregate: dict[str, Any],
    output_dir: Path,
    golden_path: Path | None,
) -> dict[str, Any]:
    return {
        "format": "dhee_replay_corpus_manifest",
        "version": 1,
        "generated_at": time.time(),
        "source": "redacted_real_sessions",
        "privacy": {
            "raw_prompts": False,
            "raw_tool_outputs": False,
            "raw_paths": False,
            "secret_filter": "dhee.hooks.claude_code.privacy.filter_secrets",
        },
        "output_dir": str(output_dir),
        "golden_path": str(golden_path) if golden_path else "",
        "aggregate": aggregate,
        "sessions": [asdict(session) for session in sessions],
    }


def harvest_corpus(
    *,
    sessions_dir: Path | None = None,
    output_dir: Path,
    harness: str = "all",
    limit: int = 0,
    min_calls: int = 1,
    max_output_chars: int = 50_000,
    golden_output: Path | None = None,
    manifest_output: Path | None = None,
) -> dict[str, Any]:
    """Harvest local transcripts into a redacted replay corpus."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_sessions_dir = output_dir / "sessions"
    output_sessions_dir.mkdir(parents=True, exist_ok=True)

    transcripts = discover_transcripts(
        sessions_dir=sessions_dir,
        harness=harness,
        limit=limit,
    )
    harvested: list[HarvestedSession] = []
    skipped: list[dict[str, Any]] = []
    replay_harness = "auto" if harness in {"all", "auto"} else harness

    for source in transcripts:
        try:
            selected = _detect_harness(source) if replay_harness == "auto" else replay_harness
            selected = "codex" if selected in {"codex", "codex_cli"} else "claude_code"
            output_path = _session_output_path(source, output_sessions_dir, selected)
            session = sanitize_transcript(
                source,
                output_path,
                harness=selected,
                max_output_chars=max_output_chars,
            )
            if session is None or session.total_calls < min_calls:
                if session is not None:
                    try:
                        Path(session.output_path).unlink()
                    except OSError:
                        pass
                skipped.append({"source_path_sha256": _sha_text(str(source.resolve()), chars=16), "reason": "too_few_calls"})
                continue
            harvested.append(session)
        except Exception as exc:  # noqa: BLE001
            skipped.append(
                {
                    "source_path_sha256": _sha_text(str(source.resolve()), chars=16),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    if golden_output is None:
        golden_output = output_dir / "golden_needs_review.jsonl"
    if harvested:
        _write_review_annotations(golden_output, harvested)

    annotations = load_golden_annotations(golden_output) if harvested else {}
    reports = [
        replay_session(
            Path(session.output_path),
            harness=session.harness,
            annotations=annotations,
        )
        for session in harvested
    ]
    aggregate = aggregate_reports(reports)

    manifest = _manifest(
        sessions=harvested,
        aggregate=aggregate,
        output_dir=output_dir,
        golden_path=golden_output if harvested else None,
    )
    manifest["transcripts_considered"] = len(transcripts)
    manifest["skipped"] = skipped
    if manifest_output is None:
        manifest_output = output_dir / "manifest.json"
    manifest_output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "output_dir": str(output_dir),
        "sessions_dir": str(output_sessions_dir),
        "golden_path": str(golden_output) if harvested else "",
        "manifest_path": str(manifest_output),
        "transcripts_considered": len(transcripts),
        "harvested_sessions": len(harvested),
        "skipped": skipped,
        "sessions": [asdict(session) for session in harvested],
        "aggregate": aggregate,
        "privacy": manifest["privacy"],
    }


def inspect_corpus(
    *,
    sessions_dir: Path,
    harness: str = "all",
    golden_path: Path | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    transcripts = discover_transcripts(
        sessions_dir=sessions_dir,
        harness=harness,
        limit=limit,
    )
    annotations = load_golden_annotations(golden_path)
    replay_harness = "auto" if harness in {"all", "auto"} else harness
    reports = [
        replay_session(path, harness=replay_harness, annotations=annotations)
        for path in transcripts
    ]
    return {
        "sessions_dir": str(sessions_dir),
        "harness": harness,
        "golden_path": str(golden_path) if golden_path else "",
        "sessions": [
            {
                "session_id": report.session_id,
                "harness": report.harness,
                "total_calls": report.total_calls,
                "calls_by_tool": dict(report.calls_by_tool),
                "raw_tokens": report.raw_tokens,
                "digest_tokens": report.digest_tokens,
                "saved_tokens": report.net_saved,
                "saved_pct": round(report.saved_pct, 2),
                "annotations_count": report.annotations_count,
                "task_parity": report.task_parity,
                "task_parity_score": report.task_parity_score,
                "stale_context_incidents": report.stale_context_incidents,
                "warnings_count": len(report.warnings),
            }
            for report in reports
        ],
        "aggregate": aggregate_reports(reports),
    }


def upsert_golden_annotation(
    *,
    golden_path: Path,
    session_id: str,
    task_parity: str,
    task_parity_score: float | None = None,
    stale_context_incidents: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Create or replace one golden replay annotation record."""
    parity = str(task_parity or "").strip().lower()
    if parity not in {"pass", "fail", "needs_review"}:
        raise ValueError("task_parity must be pass, fail, or needs_review")
    if not session_id:
        raise ValueError("session_id is required")

    record: dict[str, Any] = {
        "session_id": session_id,
        "task_parity": parity,
        "review_status": "needs_review" if parity == "needs_review" else "reviewed",
    }
    if task_parity_score is not None:
        record["task_parity_score"] = float(task_parity_score)
    if stale_context_incidents is not None:
        record["stale_context_incidents"] = max(0, int(stale_context_incidents))
    else:
        record["stale_context_incidents"] = 0
    if note:
        record["note"] = str(note)

    records = list(_jsonl_records(golden_path)) if golden_path.exists() else []
    updated: list[dict[str, Any]] = []
    replaced = False
    for existing in records:
        existing_session = str(existing.get("session_id") or "")
        if existing_session == session_id:
            updated.append(record)
            replaced = True
        else:
            updated.append(existing)
    if not replaced:
        updated.append(record)
    _write_jsonl(golden_path, updated)
    return {
        "golden_path": str(golden_path),
        "session_id": session_id,
        "action": "updated" if replaced else "created",
        "annotation": record,
    }


def format_harvest_human(result: dict[str, Any]) -> str:
    lines = [
        "Replay corpus harvest",
        f"  considered: {result.get('transcripts_considered', 0)}",
        f"  harvested:  {result.get('harvested_sessions', 0)}",
        f"  sessions:   {result.get('sessions_dir') or '(none)'}",
        f"  golden:     {result.get('golden_path') or '(none)'}",
        f"  manifest:   {result.get('manifest_path') or '(none)'}",
    ]
    aggregate = result.get("aggregate") or {}
    lines.extend(
        [
            f"  calls:      {aggregate.get('total_calls', 0)}",
            f"  saved:      {aggregate.get('saved_pct', 0.0)}%",
            f"  pending:    {aggregate.get('pending_review_sessions', 0)}",
            f"  harnesses:  {aggregate.get('sessions_by_harness', {})}",
        ]
    )
    if result.get("skipped"):
        lines.append(f"  skipped:    {len(result['skipped'])}")
    lines.append("  privacy:    raw prompts/outputs/paths omitted; review annotations are pending")
    return "\n".join(lines)


def format_inspect_human(result: dict[str, Any]) -> str:
    aggregate = result.get("aggregate") or {}
    lines = [
        "Replay corpus",
        f"  sessions dir: {result.get('sessions_dir')}",
        f"  harness:      {result.get('harness')}",
        f"  sessions:     {aggregate.get('sessions', 0)}",
        f"  harnesses:    {aggregate.get('sessions_by_harness', {})}",
        f"  calls:        {aggregate.get('total_calls', 0)}",
        f"  by tool:      {aggregate.get('calls_by_tool', {})}",
        f"  saved:        {aggregate.get('saved_pct', 0.0)}%",
        f"  annotated:    {aggregate.get('annotated_sessions', 0)}",
        f"  pending:      {aggregate.get('pending_review_sessions', 0)}",
        f"  parity:       {aggregate.get('task_parity', {})}",
        f"  stale ctx:    {aggregate.get('stale_context_incidents', 0)}",
    ]
    return "\n".join(lines)
