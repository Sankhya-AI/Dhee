"""Durable, corruption-aware runtime file I/O for Dhee.

The contract runtime is a safety boundary, so its state files must not behave
like casual cache files.  JSON writes are atomic, JSONL appends are locked, and
read failures are returned as structured diagnostics instead of being treated as
missing state.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

try:  # pragma: no cover - Windows fallback; CI and target runtime are POSIX.
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


JsonSanitizer = Callable[[Any], Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _diagnostic(path: Path, code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "path": str(path),
        "created_at": _now_iso(),
        **extra,
    }


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_atomic(
    path: str | os.PathLike[str],
    data: Any,
    *,
    sanitize: Optional[JsonSanitizer] = None,
) -> Dict[str, Any]:
    """Atomically replace *path* with JSON data and fsync the file + directory."""

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = sanitize(data) if sanitize else data
    fd = -1
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=str(target.parent),
            text=True,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
        _fsync_dir(target.parent)
        return {
            "ok": True,
            "path": str(target),
            "bytes": target.stat().st_size if target.exists() else None,
        }
    except Exception as exc:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "ok": False,
            "path": str(target),
            "diagnostic": _diagnostic(
                target,
                "ATOMIC_JSON_WRITE_FAILED",
                f"{type(exc).__name__}: {exc}",
            ),
        }


def append_jsonl_locked(
    path: str | os.PathLike[str],
    item: Any,
    *,
    sanitize: Optional[JsonSanitizer] = None,
) -> Dict[str, Any]:
    """Append one JSONL record while holding a sibling lock file."""

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    payload = sanitize(item) if sanitize else item
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_fh:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                with target.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        return {"ok": True, "path": str(target)}
    except Exception as exc:
        return {
            "ok": False,
            "path": str(target),
            "diagnostic": _diagnostic(
                target,
                "LOCKED_JSONL_APPEND_FAILED",
                f"{type(exc).__name__}: {exc}",
            ),
        }


def quarantine_corrupt_file(
    path: str | os.PathLike[str],
    reason: str,
    *,
    code: str = "CORRUPT_RUNTIME_FILE_QUARANTINED",
) -> Dict[str, Any]:
    """Move a corrupt runtime file aside and return quarantine metadata."""

    source = Path(path).expanduser()
    if not source.exists():
        return {
            "ok": False,
            "path": str(source),
            "diagnostic": _diagnostic(source, "QUARANTINE_SOURCE_MISSING", "File is already missing."),
        }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = source.with_name(f"{source.name}.corrupt.{stamp}")
    suffix = 1
    while quarantine_path.exists():
        quarantine_path = source.with_name(f"{source.name}.corrupt.{stamp}.{suffix}")
        suffix += 1
    try:
        os.replace(source, quarantine_path)
        _fsync_dir(source.parent)
        return {
            "ok": True,
            "path": str(source),
            "quarantine_path": str(quarantine_path),
            "diagnostic": _diagnostic(source, code, reason, quarantine_path=str(quarantine_path)),
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(source),
            "diagnostic": _diagnostic(
                source,
                "QUARANTINE_FAILED",
                f"{type(exc).__name__}: {exc}",
            ),
        }


def read_json_checked(
    path: str | os.PathLike[str],
    *,
    expected_schema: Optional[str] = None,
    quarantine: bool = False,
) -> Dict[str, Any]:
    """Read JSON and return data plus structured diagnostics.

    Missing files are not corrupt.  Decode errors, non-object data for runtime
    state, and schema mismatches are diagnostics the caller can surface or block
    on.
    """

    target = Path(path).expanduser()
    if not target.exists():
        return {
            "ok": False,
            "exists": False,
            "path": str(target),
            "data": None,
            "diagnostics": [
                _diagnostic(target, "RUNTIME_FILE_MISSING", "Runtime file does not exist.")
            ],
        }
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:
        diagnostic = _diagnostic(target, "RUNTIME_FILE_READ_FAILED", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "exists": True, "path": str(target), "data": None, "diagnostics": [diagnostic]}
    try:
        data = json.loads(text)
    except Exception as exc:
        diagnostic = _diagnostic(target, "RUNTIME_JSON_CORRUPT", f"{type(exc).__name__}: {exc}")
        diagnostics = [diagnostic]
        quarantine_result = quarantine_corrupt_file(target, diagnostic["message"]) if quarantine else None
        if quarantine_result:
            diagnostics.append(quarantine_result.get("diagnostic") or {})
        return {
            "ok": False,
            "exists": True,
            "path": str(target),
            "data": None,
            "diagnostics": diagnostics,
            "quarantine": quarantine_result,
        }
    if not isinstance(data, dict):
        diagnostic = _diagnostic(target, "RUNTIME_JSON_NOT_OBJECT", "Runtime JSON root must be an object.")
        diagnostics = [diagnostic]
        quarantine_result = quarantine_corrupt_file(target, diagnostic["message"]) if quarantine else None
        if quarantine_result:
            diagnostics.append(quarantine_result.get("diagnostic") or {})
        return {
            "ok": False,
            "exists": True,
            "path": str(target),
            "data": None,
            "diagnostics": diagnostics,
            "quarantine": quarantine_result,
        }
    diagnostics: List[Dict[str, Any]] = []
    if expected_schema:
        observed = data.get("schema_version") or data.get("format")
        if observed != expected_schema:
            diagnostics.append(
                _diagnostic(
                    target,
                    "RUNTIME_SCHEMA_MISMATCH",
                    "Runtime JSON schema version does not match the expected schema.",
                    expected_schema=expected_schema,
                    observed_schema=observed,
                )
            )
    return {
        "ok": not diagnostics,
        "exists": True,
        "path": str(target),
        "data": data,
        "diagnostics": diagnostics,
    }


def read_jsonl_checked(
    path: str | os.PathLike[str],
    *,
    quarantine_on_corrupt: bool = False,
) -> Dict[str, Any]:
    """Read JSONL records and surface every corrupt line as a diagnostic."""

    target = Path(path).expanduser()
    if not target.exists():
        return {
            "ok": True,
            "exists": False,
            "path": str(target),
            "records": [],
            "diagnostics": [],
        }
    records: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    try:
        lines: Iterable[str] = target.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        diagnostic = _diagnostic(target, "RUNTIME_JSONL_READ_FAILED", f"{type(exc).__name__}: {exc}")
        return {"ok": False, "exists": True, "path": str(target), "records": [], "diagnostics": [diagnostic]}
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception as exc:
            diagnostics.append(
                _diagnostic(
                    target,
                    "RUNTIME_JSONL_LINE_CORRUPT",
                    f"{type(exc).__name__}: {exc}",
                    line=line_no,
                )
            )
            continue
        if not isinstance(data, dict):
            diagnostics.append(
                _diagnostic(
                    target,
                    "RUNTIME_JSONL_LINE_NOT_OBJECT",
                    "Runtime JSONL line root must be an object.",
                    line=line_no,
                )
            )
            continue
        records.append(data)
    quarantine_result = None
    if diagnostics and quarantine_on_corrupt:
        quarantine_result = quarantine_corrupt_file(target, "JSONL file contains corrupt records.")
        if quarantine_result:
            diagnostics.append(quarantine_result.get("diagnostic") or {})
    return {
        "ok": not diagnostics,
        "exists": True,
        "path": str(target),
        "records": records,
        "diagnostics": diagnostics,
        "quarantine": quarantine_result,
    }
