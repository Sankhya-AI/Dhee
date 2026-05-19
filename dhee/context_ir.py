"""Portable Context IR compiler and interpreter.

This module turns Dhee update-capsule evidence into a small intermediate
representation (IR), then interprets that IR on another machine/repo.  It
does not auto-apply patches; it validates preconditions, maps symbols to the
target checkout, and emits a reproduction plan with diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CONTEXT_IR_SCHEMA = "dhee.context_ir.v1"
INTERPRETER_SCHEMA = "dhee.context_interpretation.v1"

_LANG_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript-react",
    ".ts": "typescript",
    ".tsx": "typescript-react",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".md": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_ANCHOR_RE = re.compile(
    r"^[+-]\s*(?:async\s+)?(?:def|class|function|const|let|var|export\s+function|pub\s+fn|fn)\s+([A-Za-z_][A-Za-z0-9_]*)",
)
_HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")
_EXCLUDED_WALK_DIRS = {
    ".git",
    ".dhee",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
}
_MAX_CANDIDATE_SCAN_FILES = 5_000
_MAX_CANDIDATE_MATCHES = 64
_MAX_ANCHOR_SCAN_BYTES = 2 * 1024 * 1024


def stable_hash(data: Any, length: int = 18) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def file_sha256(path: Path) -> Optional[str]:
    try:
        if not path.exists() or not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _language_for(path: str) -> str:
    return _LANG_BY_SUFFIX.get(Path(path).suffix.lower(), "text")


def _op_for_status(status: str) -> str:
    status = str(status or "").lower()
    if status in {"added", "untracked"}:
        return "create_file"
    if status == "deleted":
        return "delete_file"
    if status == "renamed":
        return "rename_or_modify_file"
    return "modify_file"


def _anchors_from_diff(diff: str) -> List[str]:
    anchors: List[str] = []
    for line in (diff or "").splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("@@"):
            tail = line.split("@@", 2)[-1].strip()
            if tail and tail not in anchors:
                anchors.append(tail[:120])
            continue
        match = _ANCHOR_RE.match(line)
        if match:
            name = match.group(1)
            if name not in anchors:
                anchors.append(name)
        if len(anchors) >= 12:
            break
    return anchors


def _hunk_by_path(compact_hunks: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for hunk in compact_hunks or []:
        if isinstance(hunk, dict) and hunk.get("path"):
            out[str(hunk["path"])] = hunk
    return out


def build_context_ir(
    *,
    capsule_id: str,
    title: str,
    summary: str,
    repo_id: str,
    base_ref: str,
    base_commit: str,
    head_commit: str,
    changed_paths: List[Dict[str, Any]],
    compact_hunks: List[Dict[str, Any]],
    commands: List[str],
    evidence_pointers: List[Dict[str, Any]],
    base_file_hashes: Dict[str, str],
    file_hashes: Dict[str, str],
    privacy: Dict[str, Any],
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compile capsule metadata into portable Context IR."""

    hunks = _hunk_by_path(compact_hunks)
    file_symbols: List[Dict[str, Any]] = []
    operations: List[Dict[str, Any]] = []
    ir_diagnostics = list(diagnostics or [])

    for index, item in enumerate(changed_paths or []):
        rel_path = str(item.get("path") or "")
        if not rel_path:
            continue
        status = str(item.get("status") or "changed")
        symbol = f"file:{rel_path}"
        hunk = hunks.get(rel_path, {})
        diff = str(hunk.get("diff") or "")
        anchors = _anchors_from_diff(diff)
        before_hash = base_file_hashes.get(rel_path)
        after_hash = file_hashes.get(rel_path) or item.get("sha256")
        file_symbol = {
            "symbol": symbol,
            "path": rel_path,
            "status": status,
            "language": _language_for(rel_path),
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "size": item.get("size"),
            "anchors": anchors,
            "hunk_ref": f"hunk:{index}",
        }
        file_symbols.append(file_symbol)

        preconditions: List[Dict[str, Any]] = []
        if before_hash:
            preconditions.append({"kind": "sha256", "path": rel_path, "equals": before_hash})
        elif status not in {"added", "untracked"}:
            ir_diagnostics.append({
                "level": "warning",
                "code": "MISSING_BASE_HASH",
                "path": rel_path,
                "message": "Compiler could not capture a base-file hash; interpreter will rely on anchors and path existence.",
            })
        postconditions: List[Dict[str, Any]] = []
        if after_hash:
            postconditions.append({"kind": "sha256", "path": rel_path, "equals": after_hash})
        else:
            postconditions.append({"kind": "path_absent", "path": rel_path})

        if status == "untracked" and not diff:
            ir_diagnostics.append({
                "level": "warning",
                "code": "UNTRACKED_NO_DIFF",
                "path": rel_path,
                "message": "Untracked file has a hash but no git diff body; receiving agent must reconstruct from intent or source context.",
            })
        if "<redacted-secret>" in diff:
            ir_diagnostics.append({
                "level": "warning",
                "code": "SECRET_REDACTED_IN_DIFF",
                "path": rel_path,
                "message": "A secret-like value was redacted from the compact diff; receiving agent must supply its own local secret/config.",
            })

        operations.append({
            "op": _op_for_status(status),
            "target": symbol,
            "path": rel_path,
            "hunk_ref": f"hunk:{index}",
            "anchor_hints": anchors,
            "preconditions": preconditions,
            "postconditions": postconditions,
            "recipe": {
                "kind": "compact_diff_recipe",
                "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else None,
                "has_diff_body": bool(diff),
                "truncated": bool(hunk.get("truncated")),
                "status": status,
            },
        })

    command_symbols = [
        {
            "symbol": f"cmd:{index}",
            "command": command,
            "kind": "verification" if any(word in command for word in ("test", "pytest", "npm run build", "cargo test")) else "context",
        }
        for index, command in enumerate(commands or [])
    ]
    evidence_symbols = [
        {
            "symbol": f"evidence:{index}",
            "kind": pointer.get("kind"),
            "ref": pointer.get("ref"),
            "label": pointer.get("label"),
            "source_app": pointer.get("source_app"),
            "agent_id": pointer.get("agent_id"),
            "confidentiality_scope": pointer.get("confidentiality_scope"),
        }
        for index, pointer in enumerate(evidence_pointers or [])
    ]

    ir = {
        "schema_version": CONTEXT_IR_SCHEMA,
        "compiler": {
            "name": "dhee-context-compiler",
            "version": 1,
            "phases": [
                "source_collection",
                "privacy_gate",
                "symbol_table",
                "operation_ir",
                "diagnostics",
                "verification_plan",
            ],
        },
        "module": {
            "id": capsule_id,
            "title": title,
            "intent": summary,
            "source_repo": {
                "repo_id": repo_id,
                "base_ref": base_ref,
                "base_commit": base_commit,
                "head_commit": head_commit,
            },
        },
        "symbol_table": {
            "files": file_symbols,
            "commands": command_symbols,
            "evidence": evidence_symbols,
        },
        "operations": operations,
        "verification": {
            "commands": command_symbols,
            "assertions": [
                {"kind": "postcondition", "path": op["path"], "checks": op["postconditions"]}
                for op in operations
            ],
        },
        "diagnostics": ir_diagnostics,
        "privacy": dict(privacy or {}),
        "semantics": {
            "execution_model": "interpret_plan_not_auto_apply",
            "raw_personal_memory_included": bool((privacy or {}).get("raw_personal_memory_included")),
            "whole_file_snapshots_included": False,
        },
        "fingerprint": stable_hash({
            "capsule_id": capsule_id,
            "files": file_symbols,
            "operations": operations,
            "commands": commands,
        }, 32),
    }
    return ir


def _resolve_repo_root(repo: str | os.PathLike[str] | None) -> Path:
    base = Path(repo or os.getcwd()).expanduser().resolve()
    proc = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return base


def _load_ir(capsule_or_ir: Dict[str, Any]) -> Dict[str, Any]:
    if capsule_or_ir.get("schema_version") == CONTEXT_IR_SCHEMA:
        return capsule_or_ir
    ir = capsule_or_ir.get("context_ir")
    if isinstance(ir, dict):
        return ir
    # Legacy fallback: build an interpreter-readable shell from capsule fields.
    return build_context_ir(
        capsule_id=str(capsule_or_ir.get("id") or stable_hash(capsule_or_ir)),
        title=str(capsule_or_ir.get("title") or ""),
        summary=str(capsule_or_ir.get("summary") or ""),
        repo_id=str((capsule_or_ir.get("repo") or {}).get("repo_id") or capsule_or_ir.get("repo_id") or ""),
        base_ref=str(capsule_or_ir.get("base_ref") or (capsule_or_ir.get("repo") or {}).get("base_ref") or ""),
        base_commit=str(capsule_or_ir.get("base_commit") or (capsule_or_ir.get("repo") or {}).get("base_commit") or ""),
        head_commit=str(capsule_or_ir.get("head_commit") or (capsule_or_ir.get("repo") or {}).get("head_commit") or ""),
        changed_paths=list(capsule_or_ir.get("changed_paths") or []),
        compact_hunks=list(capsule_or_ir.get("compact_hunks") or []),
        commands=list(capsule_or_ir.get("commands") or []),
        evidence_pointers=list(capsule_or_ir.get("evidence_pointers") or []),
        base_file_hashes=dict(capsule_or_ir.get("base_file_hashes") or {}),
        file_hashes=dict(capsule_or_ir.get("file_hashes") or {}),
        privacy=dict(capsule_or_ir.get("privacy") or {}),
        diagnostics=[{
            "level": "warning",
            "code": "LEGACY_CAPSULE_COMPILED_ON_INTERPRET",
            "message": "Capsule did not include context_ir; interpreter built a best-effort IR shell.",
        }],
    )


def validate_context_ir(ir: Dict[str, Any], *, strict: bool = False) -> Dict[str, Any]:
    """Validate Context IR before import or interpretation.

    The compiler emits a compact program.  Validation checks that the program
    has a known schema, a module identity, resolvable symbols, operation
    contracts, and no raw private payloads.
    """

    diagnostics: List[Dict[str, Any]] = []

    def add(level: str, code: str, message: str, **extra: Any) -> None:
        diagnostics.append({"level": level, "code": code, "message": message, **extra})

    if not isinstance(ir, dict):
        add("error", "IR_NOT_OBJECT", "Context IR must be a JSON object.")
        return {
            "ok": False,
            "schema_version": None,
            "operation_count": 0,
            "file_symbol_count": 0,
            "diagnostics": diagnostics,
        }

    schema_version = ir.get("schema_version")
    if schema_version != CONTEXT_IR_SCHEMA:
        add("error", "UNKNOWN_IR_SCHEMA", f"Unsupported context IR schema: {schema_version!r}")

    module = ir.get("module")
    if not isinstance(module, dict):
        add("error", "MISSING_MODULE", "Context IR is missing a module object.")
        module = {}
    if not module.get("id"):
        add("error", "MISSING_MODULE_ID", "Context IR module is missing an id.")

    symbol_table = ir.get("symbol_table")
    if not isinstance(symbol_table, dict):
        add("error", "MISSING_SYMBOL_TABLE", "Context IR is missing a symbol_table object.")
        symbol_table = {}

    file_symbols = symbol_table.get("files") or []
    if not isinstance(file_symbols, list):
        add("error", "INVALID_FILE_SYMBOLS", "symbol_table.files must be a list.")
        file_symbols = []

    file_symbol_ids = set()
    file_symbol_paths = set()
    for index, symbol in enumerate(file_symbols):
        if not isinstance(symbol, dict):
            add("error", "INVALID_FILE_SYMBOL", "File symbol must be an object.", index=index)
            continue
        symbol_id = symbol.get("symbol")
        path = symbol.get("path")
        if not symbol_id:
            add("error", "MISSING_FILE_SYMBOL_ID", "File symbol is missing symbol id.", index=index)
        else:
            file_symbol_ids.add(str(symbol_id))
        if not path:
            add("error", "MISSING_FILE_SYMBOL_PATH", "File symbol is missing path.", index=index)
        else:
            path_text = str(path)
            file_symbol_paths.add(path_text)
            if Path(path_text).is_absolute() or ".." in Path(path_text).parts:
                add("error", "UNSAFE_FILE_SYMBOL_PATH", "File symbol path must be repo-relative.", path=path_text)
        for key in ("before_sha256", "after_sha256"):
            digest = symbol.get(key)
            if digest and not _HEX_64_RE.match(str(digest)):
                add("error" if strict else "warning", "INVALID_FILE_HASH", f"{key} is not a sha256 hex digest.", path=path)

    operations = ir.get("operations") or []
    if not isinstance(operations, list):
        add("error", "INVALID_OPERATIONS", "operations must be a list.")
        operations = []
    if strict and not operations:
        add("error", "NO_OPERATIONS", "Strict Context IR requires at least one operation.")
    elif not operations:
        add("warning", "NO_OPERATIONS", "Context IR contains no operations.")

    for index, op in enumerate(operations):
        if not isinstance(op, dict):
            add("error", "INVALID_OPERATION", "Operation must be an object.", index=index)
            continue
        action = op.get("op")
        path = op.get("path")
        target = op.get("target")
        if action not in {"create_file", "modify_file", "delete_file", "rename_or_modify_file"}:
            add("error", "UNKNOWN_OPERATION", f"Unsupported operation: {action!r}", index=index, path=path)
        if not path:
            add("error", "MISSING_OPERATION_PATH", "Operation is missing path.", index=index)
        else:
            path_text = str(path)
            if Path(path_text).is_absolute() or ".." in Path(path_text).parts:
                add("error", "UNSAFE_OPERATION_PATH", "Operation path must be repo-relative.", index=index, path=path_text)
            if path_text not in file_symbol_paths:
                add("warning", "OPERATION_PATH_NOT_IN_SYMBOL_TABLE", "Operation path has no matching file symbol.", index=index, path=path_text)
        if target and file_symbol_ids and str(target) not in file_symbol_ids:
            add("warning", "OPERATION_TARGET_UNRESOLVED", "Operation target is not present in the file symbol table.", index=index, target=target)
        if not op.get("hunk_ref"):
            add("warning", "MISSING_HUNK_REF", "Operation does not point to a compact hunk.", index=index, path=path)
        for check_group in ("preconditions", "postconditions"):
            checks = op.get(check_group) or []
            if not isinstance(checks, list):
                add("error", "INVALID_CONDITION_GROUP", f"{check_group} must be a list.", index=index, path=path)
                continue
            for check_index, check in enumerate(checks):
                if not isinstance(check, dict):
                    add("error", "INVALID_CONDITION", "Condition must be an object.", index=index, check_index=check_index)
                    continue
                kind = check.get("kind")
                if kind not in {"sha256", "path_absent"}:
                    add("error", "UNKNOWN_CONDITION", f"Unsupported condition kind: {kind!r}", index=index, path=path)
                digest = check.get("equals")
                if kind == "sha256" and (not digest or not _HEX_64_RE.match(str(digest))):
                    add("error", "INVALID_CONDITION_HASH", "sha256 condition must include a valid equals hash.", index=index, path=path)

    privacy = ir.get("privacy") or {}
    if not isinstance(privacy, dict):
        add("error", "INVALID_PRIVACY", "privacy must be an object.")
        privacy = {}
    if privacy.get("raw_personal_memory_included") or (ir.get("semantics") or {}).get("raw_personal_memory_included"):
        add("error", "PRIVATE_BODY_PRESENT", "Context IR cannot contain raw personal-memory payloads.")

    return {
        "ok": not any(item.get("level") == "error" for item in diagnostics),
        "schema_version": schema_version,
        "operation_count": len(operations),
        "file_symbol_count": len(file_symbols),
        "diagnostics": diagnostics,
    }


def render_context_ir(ir_or_capsule: Dict[str, Any]) -> str:
    """Render compact, agent-readable IR for humans and receiving agents."""

    ir = _load_ir(ir_or_capsule)
    validation = validate_context_ir(ir)
    module = ir.get("module") or {}
    lines = [
        f"- Schema: `{ir.get('schema_version') or '(none)'}`",
        f"- Fingerprint: `{ir.get('fingerprint') or '(none)'}`",
        f"- Module: `{module.get('id') or '(unknown)'}`",
        f"- Validation: `{'ok' if validation['ok'] else 'failed'}` with `{len(validation['diagnostics'])}` diagnostic(s)",
        f"- Operations: `{validation['operation_count']}`",
    ]
    for index, op in enumerate(ir.get("operations") or [], start=1):
        anchors = op.get("anchor_hints") or []
        anchor_text = f"; anchors: {', '.join(str(item) for item in anchors[:3])}" if anchors else ""
        lines.append(
            f"  {index}. `{op.get('op')}` `{op.get('path')}` via `{op.get('hunk_ref') or '(no hunk)'}`{anchor_text}"
        )
    if not (ir.get("operations") or []):
        lines.append("  No operations compiled.")
    return "\n".join(lines)


def _repo_relative(repo_root: Path, path: Path) -> str:
    try:
        return os.path.relpath(path, repo_root).replace(os.sep, "/")
    except ValueError:
        return str(path)


def _safe_repo_path(repo_root: Path, rel_path: str) -> Optional[Path]:
    if not rel_path:
        return None
    raw = Path(rel_path)
    if raw.is_absolute() or ".." in raw.parts:
        return None
    try:
        root = repo_root.resolve()
        path = (root / raw).resolve()
        if os.path.commonpath([str(root), str(path)]) != str(root):
            return None
        return path
    except (OSError, ValueError):
        return None


def _condition_hashes(op: Dict[str, Any], group: str) -> List[str]:
    return [
        str(check.get("equals"))
        for check in op.get(group) or []
        if isinstance(check, dict) and check.get("kind") == "sha256" and check.get("equals")
    ]


def _candidate_files(repo_root: Path, rel_path: str, language: str) -> Tuple[List[Path], Dict[str, Any]]:
    del language  # Reserved for future grammar-aware symbol lookup.
    basename = Path(rel_path).name
    if not basename:
        return [], {"scanned_files": 0, "truncated": False}

    candidates: List[Path] = []
    scanned = 0
    truncated = False
    for root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _EXCLUDED_WALK_DIRS and not name.endswith(".egg-info")
        ]
        for filename in filenames:
            scanned += 1
            if scanned > _MAX_CANDIDATE_SCAN_FILES:
                truncated = True
                return candidates, {"scanned_files": scanned, "truncated": truncated}
            if filename != basename:
                continue
            path = (Path(root) / filename).resolve()
            if path.is_file():
                candidates.append(path)
            if len(candidates) >= _MAX_CANDIDATE_MATCHES:
                truncated = True
                return candidates, {"scanned_files": scanned, "truncated": truncated}
    return candidates, {"scanned_files": scanned, "truncated": truncated}


def _anchor_match_count(path: Path, anchors: Iterable[Any]) -> int:
    cleaned = [str(anchor).strip() for anchor in anchors or [] if str(anchor or "").strip()]
    if not cleaned:
        return 0
    try:
        if path.stat().st_size > _MAX_ANCHOR_SCAN_BYTES:
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    return sum(1 for anchor in cleaned if anchor in text)


def _resolve_operation_target(
    repo_root: Path,
    op: Dict[str, Any],
    file_symbol: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    declared_rel = str(op.get("path") or "")
    declared_path = _safe_repo_path(repo_root, declared_rel)
    if declared_path is None:
        return {
            "declared_path": declared_rel,
            "resolved_path": declared_rel,
            "absolute_path": None,
            "exists": False,
            "resolution": "unsafe_path",
            "resolution_confidence": 0.0,
            "diagnostics": [{
                "level": "error",
                "code": "UNSAFE_OPERATION_PATH",
                "path": declared_rel,
                "message": "Operation path is absolute or escapes the target repo.",
            }],
        }

    if declared_path.exists():
        return {
            "declared_path": declared_rel,
            "resolved_path": declared_rel,
            "absolute_path": declared_path,
            "exists": True,
            "resolution": "exact_path",
            "resolution_confidence": 1.0,
            "diagnostics": [],
        }

    pre_hashes = _condition_hashes(op, "preconditions")
    post_hashes = _condition_hashes(op, "postconditions")
    language = str((file_symbol or {}).get("language") or _language_for(declared_rel))
    candidates, scan = _candidate_files(repo_root, declared_rel, language)
    diagnostics: List[Dict[str, Any]] = []
    if scan.get("truncated"):
        diagnostics.append({
            "level": "warning",
            "code": "TARGET_SEARCH_TRUNCATED",
            "path": declared_rel,
            "message": "Target search hit the scan limit before inspecting the full repo.",
            "scanned_files": scan.get("scanned_files"),
        })

    for candidate in candidates:
        digest = file_sha256(candidate)
        if digest and digest in post_hashes:
            return {
                "declared_path": declared_rel,
                "resolved_path": _repo_relative(repo_root, candidate),
                "absolute_path": candidate,
                "exists": True,
                "resolution": "moved_after_hash_match",
                "resolution_confidence": 0.98,
                "diagnostics": diagnostics,
            }
        if digest and digest in pre_hashes:
            return {
                "declared_path": declared_rel,
                "resolved_path": _repo_relative(repo_root, candidate),
                "absolute_path": candidate,
                "exists": True,
                "resolution": "moved_before_hash_match",
                "resolution_confidence": 0.95,
                "diagnostics": diagnostics,
            }

    anchors = list(op.get("anchor_hints") or []) + list((file_symbol or {}).get("anchors") or [])
    best_candidate: Optional[Path] = None
    best_count = 0
    for candidate in candidates:
        count = _anchor_match_count(candidate, anchors)
        if count > best_count:
            best_candidate = candidate
            best_count = count
    if best_candidate is not None and best_count > 0:
        diagnostics.append({
            "level": "warning",
            "code": "TARGET_RESOLVED_BY_ANCHOR",
            "path": declared_rel,
            "resolved_path": _repo_relative(repo_root, best_candidate),
            "message": "Exact path was missing; interpreter resolved a same-name candidate using anchor hints.",
            "anchor_matches": best_count,
        })
        return {
            "declared_path": declared_rel,
            "resolved_path": _repo_relative(repo_root, best_candidate),
            "absolute_path": best_candidate,
            "exists": True,
            "resolution": "anchor_match",
            "resolution_confidence": min(0.85, 0.45 + (best_count * 0.1)),
            "diagnostics": diagnostics,
        }

    return {
        "declared_path": declared_rel,
        "resolved_path": declared_rel,
        "absolute_path": declared_path,
        "exists": False,
        "resolution": "missing",
        "resolution_confidence": 0.0,
        "diagnostics": diagnostics,
    }


def _operation_state(
    repo_root: Path,
    op: Dict[str, Any],
    file_symbol: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rel_path = str(op.get("path") or "")
    resolution = _resolve_operation_target(repo_root, op, file_symbol)
    path = resolution.get("absolute_path")
    target_hash = file_sha256(path) if isinstance(path, Path) else None
    pre_hashes = _condition_hashes(op, "preconditions")
    post_hashes = _condition_hashes(op, "postconditions")
    exists = bool(resolution.get("exists"))
    if post_hashes and target_hash in post_hashes:
        state = "already_applied"
    elif pre_hashes and target_hash in pre_hashes:
        state = "ready"
    elif op.get("op") == "create_file" and not exists:
        state = "ready"
    elif op.get("op") == "delete_file" and not exists:
        state = "already_applied"
    elif not exists:
        state = "blocked"
    else:
        state = "conflict"
    return {
        "path": rel_path,
        "declared_path": resolution.get("declared_path") or rel_path,
        "resolved_path": resolution.get("resolved_path") or rel_path,
        "resolution": resolution.get("resolution"),
        "resolution_confidence": resolution.get("resolution_confidence"),
        "exists": exists,
        "current_sha256": target_hash,
        "state": state,
        "expected_before": pre_hashes[0] if pre_hashes else None,
        "expected_after": post_hashes[0] if post_hashes else None,
        "diagnostics": resolution.get("diagnostics") or [],
    }


def interpret_context_ir(
    *,
    repo: str | os.PathLike[str] | None,
    capsule_or_ir: Dict[str, Any],
    strict: bool = False,
) -> Dict[str, Any]:
    """Interpret compiled context IR on a target checkout."""

    repo_root = _resolve_repo_root(repo)
    ir = _load_ir(capsule_or_ir)
    diagnostics: List[Dict[str, Any]] = []
    validation = validate_context_ir(ir, strict=strict)
    diagnostics.extend(validation["diagnostics"])
    file_symbols = {
        str(symbol.get("symbol")): symbol
        for symbol in ((ir.get("symbol_table") or {}).get("files") or [])
        if isinstance(symbol, dict) and symbol.get("symbol")
    }
    operation_states = [
        _operation_state(repo_root, op, file_symbols.get(str(op.get("target"))))
        for op in ir.get("operations") or []
    ]
    for state in operation_states:
        diagnostics.extend(state.get("diagnostics") or [])
        if state["state"] == "conflict":
            diagnostics.append({
                "level": "warning" if not strict else "error",
                "code": "PRECONDITION_MISMATCH",
                "path": state["path"],
                "resolved_path": state.get("resolved_path"),
                "message": "Target file hash matches neither the compiled before nor after state.",
            })
        elif state["state"] == "blocked":
            diagnostics.append({
                "level": "error",
                "code": "TARGET_PATH_MISSING",
                "path": state["path"],
                "resolved_path": state.get("resolved_path"),
                "message": "Target path is missing and cannot satisfy the operation precondition.",
            })

    states = {state["state"] for state in operation_states}
    blocking_codes = {"UNKNOWN_IR_SCHEMA", "PRIVATE_BODY_PRESENT", "UNSAFE_OPERATION_PATH"}
    if not validation["ok"] or any(diag.get("level") == "error" and diag.get("code") in blocking_codes for diag in diagnostics):
        readiness = "blocked"
    elif states and states <= {"already_applied"}:
        readiness = "already_applied"
    elif "conflict" in states:
        readiness = "conflict"
    elif "blocked" in states:
        readiness = "blocked"
    else:
        readiness = "ready"

    steps: List[Dict[str, Any]] = []
    for index, op in enumerate(ir.get("operations") or [], start=1):
        state = next((item for item in operation_states if item["path"] == op.get("path")), {})
        steps.append({
            "step": index,
            "action": op.get("op"),
            "path": op.get("path"),
            "resolved_path": state.get("resolved_path"),
            "resolution": state.get("resolution"),
            "state": state.get("state"),
            "instruction": _instruction_for_operation(op, state),
            "anchor_hints": op.get("anchor_hints") or [],
            "preconditions": op.get("preconditions") or [],
            "postconditions": op.get("postconditions") or [],
        })

    return {
        "format": INTERPRETER_SCHEMA,
        "repo": str(repo_root),
        "module": ir.get("module") or {},
        "readiness": readiness,
        "validation": validation,
        "operation_states": operation_states,
        "execution_plan": steps,
        "verification_plan": ir.get("verification") or {},
        "diagnostics": list(ir.get("diagnostics") or []) + diagnostics,
        "policy": {
            "auto_apply": False,
            "requires_agent_editing": True,
            "strict": bool(strict),
        },
    }


def _instruction_for_operation(op: Dict[str, Any], state: Dict[str, Any]) -> str:
    path = op.get("path") or ""
    resolved_path = state.get("resolved_path") or path
    resolved_note = f" (resolved on this repo as `{resolved_path}`)" if resolved_path != path else ""
    if state.get("state") == "already_applied":
        return f"`{path}`{resolved_note} already satisfies the compiled postcondition."
    if state.get("state") == "conflict":
        return f"Inspect `{path}`{resolved_note} manually; current content differs from both compiled before and after hashes."
    if state.get("state") == "blocked":
        return f"Resolve missing target path `{path}`{resolved_note} before replaying this operation."
    action = op.get("op")
    if action == "create_file":
        return f"Create `{path}`{resolved_note} using the compact diff recipe and intent from the capsule."
    if action == "delete_file":
        return f"Delete `{path}`{resolved_note} if the target still matches the compiled precondition."
    return f"Modify `{path}`{resolved_note} using compact hunk `{op.get('hunk_ref')}` and anchor hints, then verify postconditions."
