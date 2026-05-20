"""Persistent SWE repo intelligence for Dhee task contracts.

This module is deliberately deterministic.  It compiles a repository snapshot
into a compact, git-SHA scoped "repo brain" that downstream contracts can use
for localization, verification planning, and proof bundles without stuffing raw
files or logs into the active prompt.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from dhee import repo_link
from dhee.runtime_io import read_json_checked, read_jsonl_checked, write_json_atomic


REPO_INTELLIGENCE_SCHEMA = "dhee.repo_intelligence.v1"
REPO_BRAIN_POINTER_SCHEMA = "dhee.repo_brain_pointer.v1"
LOCALIZATION_SCHEMA = "dhee.repo_localization.v1"
VERIFICATION_CARD_SCHEMA = "dhee.verification_card.v1"
REPO_GRAPH_ARTIFACT_SCHEMA = "dhee.repo_graph_artifact.v1"
CONTEXT_GRAPH_SLICE_SCHEMA = "dhee.context_graph_slice.v1"

MAX_INDEX_FILE_BYTES = 512_000
MAX_SYMBOLS = 2_000
MAX_IMPORT_FILES = 2_000
MAX_CALL_EDGES = 5_000
MAX_CALL_SITES = 5_000
MAX_SYNTAX_SPANS = 4_000
MAX_INCREMENTAL_FILE_LIST = 5_000
MAX_FAILURE_RECORDS = 120
MAX_FAILURE_REFS = 600
MAX_OWNERSHIP_FILES = 1_500
MAX_TEST_OWNERSHIP_EDGES = 3_000
MAX_TEST_LINKS_PER_SOURCE = 12
MAX_REPO_GRAPH_NODES = 4_000
MAX_REPO_GRAPH_EDGES = 12_000
DEFAULT_CONTEXT_GRAPH_QUERY_NODES = 500
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
JS_TS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
TREE_SITTER_LANGUAGE_SPECS = {
    "python": ("tree_sitter_python", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
}
TREE_SITTER_SYMBOL_TYPES = {
    "python": {
        "class_definition": "class",
        "function_definition": "function",
    },
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "variable_declarator": "function",
    },
    "typescript": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "variable_declarator": "function",
    },
    "tsx": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "variable_declarator": "function",
    },
}
LSP_SERVER_CANDIDATES = {
    "python": ("pyright-langserver", "pylsp", "ruff"),
    "javascript": ("typescript-language-server", "tsserver"),
    "typescript": ("typescript-language-server", "tsserver"),
    "tsx": ("typescript-language-server", "tsserver"),
}
LSP_EXECUTABLE_COMMANDS = {
    "pyright-langserver": ("pyright-langserver", "--stdio"),
    "pylsp": ("pylsp",),
    "typescript-language-server": ("typescript-language-server", "--stdio"),
}
LIVE_LSP_MAX_FILES = 8
LIVE_LSP_MAX_SYMBOLS = 40
LIVE_LSP_TIMEOUT_SECONDS = 4.0
LANGUAGE_CONFIG_FILES = {
    "python": ("pyproject.toml", "setup.cfg", "mypy.ini", "pyrightconfig.json", "ruff.toml"),
    "javascript": ("package.json", "jsconfig.json", "tsconfig.json"),
    "typescript": ("package.json", "tsconfig.json"),
    "tsx": ("package.json", "tsconfig.json"),
}
TEST_SUFFIX_HINTS = (
    ".test.",
    ".spec.",
)

_EXCLUDED_DIRS = {
    ".git",
    ".dhee",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".direnv",
    ".tox",
    ".venv",
    ".venv-dhee",
    ".venv-dhee-full",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    "site-packages",
}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "bug",
    "fix",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "please",
    "test",
    "tests",
    "the",
    "this",
    "to",
    "with",
}
_RISKY_NAMES = (
    "auth",
    "secret",
    "token",
    "security",
    "migration",
    "payment",
    "billing",
    "prod",
    "config",
    "firewall",
)
_FAILURE_RE = re.compile(
    r"(?i)(assertionerror|traceback|failed|failure|error|exception|regression|timeout|exit[_ -]?code)"
)
_PATH_LINE_RE = re.compile(
    r"(?P<path>(?:\.?/)?[A-Za-z0-9_./@-]+\.(?:py|js|jsx|ts|tsx))"
    r"(?:(?::(?P<line>\d+)(?::\d+)?)|(?:, line (?P<pyline>\d+)))?"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stable_hash(data: Any, length: int = 16) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_out(repo_root: Path, args: Sequence[str], default: str = "") -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return default
    return proc.stdout.strip()


def resolve_repo_root(repo: str | os.PathLike[str] | None) -> Path:
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


def repo_slug(repo_root: Path) -> str:
    remote = _git_out(repo_root, ["remote", "get-url", "origin"], default="")
    if remote:
        value = remote.rstrip("/")
        value = re.sub(r"\.git$", "", value)
        if ":" in value and "/" in value:
            value = value.split(":", 1)[1]
        else:
            parts = value.split("/")
            value = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        if value:
            return value
    return repo_root.name


def branch_state(repo_root: Path) -> Dict[str, Any]:
    status = _git_out(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"], default="")
    staged: List[str] = []
    unstaged: List[str] = []
    untracked: List[str] = []
    changed: List[str] = []
    for line in status.splitlines():
        if not line:
            continue
        code = line[:2]
        path = (line[3:] if len(line) > 2 and line[2] == " " else line[2:]).strip()
        if " -> " in path:
            _old, path = path.split(" -> ", 1)
        path = path.replace(os.sep, "/")
        changed.append(path)
        if code.startswith("??"):
            untracked.append(path)
        else:
            if code[0] != " ":
                staged.append(path)
            if len(code) > 1 and code[1] != " ":
                unstaged.append(path)
    return {
        "branch": _git_out(repo_root, ["branch", "--show-current"], default=""),
        "head_commit": _git_out(repo_root, ["rev-parse", "HEAD"], default=""),
        "head_short": _git_out(repo_root, ["rev-parse", "--short", "HEAD"], default=""),
        "dirty": bool(changed),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "changed_paths": sorted(set(changed)),
    }


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    for token in re.findall(r"[A-Za-z0-9_]+", str(text or "").lower()):
        if len(token) < 3 or token in _STOP_WORDS:
            continue
        if token not in out:
            out.append(token)
    return out


def _path_text(path: str) -> str:
    return str(path).replace("_", " ").replace("-", " ").replace("/", " ").lower()


def iter_repo_files(repo_root: Path, limit: int = 4_000) -> List[str]:
    listed = _git_out(repo_root, ["ls-files", "--cached", "--others", "--exclude-standard"], default="")
    if listed:
        files = []
        for raw in listed.splitlines():
            rel = raw.strip().replace(os.sep, "/")
            if not rel or any(_is_excluded_dir(part) for part in Path(rel).parts[:-1]):
                continue
            files.append(rel)
            if len(files) >= limit:
                return sorted(files)
        return sorted(files)
    files: List[str] = []
    for root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_excluded_dir(name)
        ]
        for filename in filenames:
            path = Path(root) / filename
            try:
                rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
            except ValueError:
                continue
            files.append(rel)
            if len(files) >= limit:
                return sorted(files)
    return sorted(files)


def _is_excluded_dir(name: str) -> bool:
    return (
        name in _EXCLUDED_DIRS
        or name.endswith(".egg-info")
        or name.startswith(".venv")
        or name.startswith("venv-")
    )


def _file_kind(path: str) -> str:
    if _is_test_file(path):
        return "test"
    if _language_for_path(path):
        return "source"
    if Path(path).name in {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json"}:
        return "manifest"
    return "asset"


def _is_test_file(path: str) -> bool:
    name = Path(path).name
    lowered = str(path).lower()
    if path.startswith("tests/") or "/tests/" in path:
        return Path(path).suffix in SOURCE_SUFFIXES
    if path.endswith(".py"):
        return name.startswith("test_") or name.endswith("_test.py")
    return Path(path).suffix in JS_TS_SUFFIXES and any(hint in lowered for hint in TEST_SUFFIX_HINTS)


def _test_command_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return f"pytest {path}"
    if suffix in JS_TS_SUFFIXES:
        return f"npm test -- {path}"
    return f"pytest {path}"


def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
    }.get(suffix, "")


def _file_manifest(repo_root: Path, files: Sequence[str]) -> List[Dict[str, Any]]:
    manifest: List[Dict[str, Any]] = []
    for rel in files:
        path = repo_root / rel
        try:
            stat = path.stat()
        except OSError:
            continue
        item = {
            "path": rel,
            "kind": _file_kind(rel),
            "language": _language_for_path(rel),
            "bytes": stat.st_size,
            "sha256": "",
            "indexed": bool(_language_for_path(rel)) and stat.st_size <= MAX_INDEX_FILE_BYTES,
        }
        if stat.st_size <= 2_000_000:
            try:
                item["sha256"] = _sha256_file(path)
            except OSError:
                item["sha256"] = ""
        manifest.append(item)
    return manifest


def _module_name_for_path(rel: str) -> str:
    path = Path(rel)
    if path.suffix != ".py":
        return ""
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _build_module_map(py_files: Sequence[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rel in py_files:
        module = _module_name_for_path(rel)
        if module:
            out[module] = rel
    return out


def _resolve_import_module(current_module: str, module: str, level: int) -> str:
    if level <= 0:
        return module
    package_parts = current_module.split(".")[:-1]
    keep = max(0, len(package_parts) - (level - 1))
    prefix = ".".join(package_parts[:keep])
    if prefix and module:
        return f"{prefix}.{module}"
    return prefix or module


def _resolve_module_path(module: str, module_to_path: Dict[str, str]) -> Optional[str]:
    if not module:
        return None
    probe = module
    while probe:
        if probe in module_to_path:
            return module_to_path[probe]
        if "." not in probe:
            break
        probe = probe.rsplit(".", 1)[0]
    return None


def _signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = []
        for base in node.bases[:6]:
            if isinstance(base, ast.Name):
                bases.append(base.id)
            elif isinstance(base, ast.Attribute):
                bases.append(base.attr)
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    args = []
    all_args = list(node.args.posonlyargs) + list(node.args.args)
    defaults = [None] * (len(all_args) - len(node.args.defaults)) + list(node.args.defaults)
    for arg, default in zip(all_args, defaults):
        args.append(arg.arg + ("=..." if default is not None else ""))
    if node.args.vararg:
        args.append("*" + node.args.vararg.arg)
    for arg in node.args.kwonlyargs:
        args.append(arg.arg)
    if node.args.kwarg:
        args.append("**" + node.args.kwarg.arg)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({', '.join(args)})"


class _PythonIndexer(ast.NodeVisitor):
    def __init__(self, rel: str, module: str, module_to_path: Dict[str, str]) -> None:
        self.rel = rel
        self.module = module
        self.module_to_path = module_to_path
        self.symbols: List[Dict[str, Any]] = []
        self.imports: List[Dict[str, Any]] = []
        self.calls: List[Dict[str, Any]] = []
        self._stack: List[Tuple[str, str]] = []

    def _qualname(self, name: str) -> str:
        names = [item[0] for item in self._stack] + [name]
        return ".".join(names)

    def _add_symbol(self, node: ast.AST, kind: str) -> str:
        name = getattr(node, "name", "")
        qualname = self._qualname(name)
        symbol_id = _stable_hash({"path": self.rel, "qualname": qualname, "kind": kind}, 18)
        decorators: List[str] = []
        for decorator in getattr(node, "decorator_list", [])[:8]:
            if isinstance(decorator, ast.Name):
                decorators.append(decorator.id)
            elif isinstance(decorator, ast.Attribute):
                decorators.append(decorator.attr)
            elif isinstance(decorator, ast.Call):
                func = decorator.func
                decorators.append(getattr(func, "id", getattr(func, "attr", "call")))
        self.symbols.append(
            {
                "id": symbol_id,
                "path": self.rel,
                "module": self.module,
                "qualname": qualname,
                    "name": name,
                    "kind": kind,
                    "language": "python",
                    "parser_backend": "python_ast",
                    "start_line": int(getattr(node, "lineno", 0) or 0),
                "end_line": int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
                "signature": _signature(node),
                "decorators": decorators,
                "doc": _trim_doc(ast.get_docstring(node)),
            }
        )
        return symbol_id

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module = alias.name
            resolved_path = _resolve_module_path(module, self.module_to_path)
            self.imports.append(
                {
                    "module": module,
                    "name": None,
                    "alias": alias.asname,
                    "level": 0,
                    "resolved_path": resolved_path,
                    "external": resolved_path is None,
                    "line": int(getattr(node, "lineno", 0) or 0),
                }
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolve_import_module(self.module, str(node.module or ""), int(node.level or 0))
        for alias in node.names:
            full = f"{module}.{alias.name}" if module and alias.name != "*" else module
            resolved_path = _resolve_module_path(full, self.module_to_path) or _resolve_module_path(module, self.module_to_path)
            self.imports.append(
                {
                    "module": module,
                    "name": alias.name,
                    "alias": alias.asname,
                    "level": int(node.level or 0),
                    "resolved_path": resolved_path,
                    "external": resolved_path is None,
                    "line": int(getattr(node, "lineno", 0) or 0),
                }
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol_id = self._add_symbol(node, "class")
        self._stack.append((node.name, symbol_id))
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, "function")

    def _visit_function(self, node: ast.AST, kind: str) -> None:
        symbol_id = self._add_symbol(node, kind)
        self._stack.append((getattr(node, "name", ""), symbol_id))
        self.generic_visit(node)
        self._stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if self._stack:
            callee = _call_name(node.func)
            if callee:
                caller_name, caller_id = self._stack[-1]
                self.calls.append(
                    {
                        "path": self.rel,
                        "caller": caller_name,
                        "caller_id": caller_id,
                        "callee": callee,
                        "line": int(getattr(node, "lineno", 0) or 0),
                    }
                )
        self.generic_visit(node)


def _trim_doc(doc: Optional[str], limit: int = 240) -> str:
    text = " ".join(str(doc or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        root = _call_name(func.value)
        return f"{root}.{func.attr}" if root else func.attr
    return ""


def _index_python(repo_root: Path, files: Sequence[str]) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    py_files = [rel for rel in files if rel.endswith(".py")]
    module_to_path = _build_module_map(py_files)
    symbols: List[Dict[str, Any]] = []
    imports: Dict[str, List[Dict[str, Any]]] = {}
    calls: List[Dict[str, Any]] = []
    for rel in py_files:
        path = repo_root / rel
        try:
            if path.stat().st_size > MAX_INDEX_FILE_BYTES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        indexer = _PythonIndexer(rel, _module_name_for_path(rel), module_to_path)
        indexer.visit(tree)
        symbols.extend(indexer.symbols)
        if indexer.imports:
            imports[rel] = indexer.imports
        calls.extend(indexer.calls)
    return symbols[:MAX_SYMBOLS], dict(list(imports.items())[:MAX_IMPORT_FILES]), calls[:MAX_CALL_SITES]


def _index_js_ts(repo_root: Path, files: Sequence[str]) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]]]:
    symbols: List[Dict[str, Any]] = []
    imports: Dict[str, List[Dict[str, Any]]] = {}
    calls: List[Dict[str, Any]] = []
    js_files = [rel for rel in files if Path(rel).suffix.lower() in JS_TS_SUFFIXES]
    module_to_path = _build_js_module_map(js_files)
    for rel in js_files:
        path = repo_root / rel
        try:
            if path.stat().st_size > MAX_INDEX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_symbols = _js_ts_symbols(rel, text)
        symbols.extend(file_symbols)
        file_imports = _js_ts_imports(rel, text, module_to_path)
        if file_imports:
            imports[rel] = file_imports
        calls.extend(_js_ts_calls(rel, text, file_symbols))
    return symbols[:MAX_SYMBOLS], dict(list(imports.items())[:MAX_IMPORT_FILES]), calls[:MAX_CALL_SITES]


def _index_sources(
    repo_root: Path,
    files: Sequence[str],
    previous: Optional[Dict[str, Any]] = None,
    incremental: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]], List[Dict[str, Any]], Dict[str, Any]]:
    source_files = [path for path in files if bool(_language_for_path(path))]
    reuse_paths = set((incremental or {}).get("unchanged_files") or [])
    previous_call_sites = (previous or {}).get("call_sites")
    can_reuse = bool(previous and isinstance(previous_call_sites, list))
    reusable_paths = {path for path in reuse_paths if path in source_files} if can_reuse else set()
    fresh_files = [path for path in source_files if path not in reusable_paths]

    reused_symbols = [
        dict(symbol)
        for symbol in ((previous or {}).get("symbols") or [])
        if str(symbol.get("path") or "") in reusable_paths
    ]
    reused_imports = {
        path: list(items)
        for path, items in ((previous or {}).get("imports") or {}).items()
        if path in reusable_paths
    }
    reused_calls = [
        dict(call)
        for call in (previous_call_sites or [])
        if str(call.get("path") or "") in reusable_paths
    ]

    py_symbols, py_imports, py_calls = _index_python(repo_root, fresh_files)
    js_symbols, js_imports, js_calls = _index_js_ts(repo_root, fresh_files)
    imports = dict(py_imports)
    imports.update(js_imports)
    imports.update(reused_imports)
    source_reuse = {
        "schema_version": "dhee.repo_source_index_reuse.v1",
        "mode": "incremental_reuse" if reusable_paths else "full_parse",
        "eligible_file_count": len(reuse_paths),
        "reused_file_count": len(reusable_paths),
        "fresh_file_count": len(fresh_files),
        "requires_call_sites": True,
        "reason": "" if can_reuse else "previous brain has no reusable call_sites",
    }
    return (
        (reused_symbols + py_symbols + js_symbols)[:MAX_SYMBOLS],
        dict(list(imports.items())[:MAX_IMPORT_FILES]),
        (reused_calls + py_calls + js_calls)[:MAX_CALL_SITES],
        source_reuse,
    )


def _resolve_call_edges(calls: Sequence[Dict[str, Any]], symbols: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_qual_tail: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for symbol in symbols:
        by_name[str(symbol.get("name") or "")].append(symbol)
        by_qual_tail[str(symbol.get("qualname") or "").split(".")[-1]].append(symbol)
    edges: List[Dict[str, Any]] = []
    for call in calls:
        callee = str(call.get("callee") or "")
        short = callee.split(".")[-1]
        candidates = by_name.get(short) or by_qual_tail.get(short) or []
        resolved = candidates[0] if len(candidates) == 1 else None
        edge = dict(call)
        edge.update(
            {
                "callee_name": short,
                "callee_id": resolved.get("id") if resolved else None,
                "callee_path": resolved.get("path") if resolved else None,
                "resolution": "unique_symbol" if resolved else ("ambiguous" if candidates else "unresolved"),
                "confidence": 0.82 if resolved else (0.38 if candidates else 0.2),
            }
        )
        edges.append(edge)
    return edges


def _build_js_module_map(files: Sequence[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rel in files:
        path = Path(rel)
        stem = str(path.with_suffix("")).replace(os.sep, "/")
        out[stem] = rel
        out["./" + stem] = rel
        out[path.stem] = rel
        if path.name in {"index.js", "index.jsx", "index.ts", "index.tsx"}:
            parent = str(path.parent).replace(os.sep, "/")
            out[parent] = rel
            out["./" + parent] = rel
    return out


def _resolve_js_import_path(current: str, specifier: str, module_to_path: Dict[str, str]) -> Optional[str]:
    spec = str(specifier or "").strip()
    if not spec:
        return None
    if spec.startswith("."):
        base = Path(current).parent / spec
        normalized = str(base).replace(os.sep, "/")
        probes = [normalized]
        probes.extend(normalized + suffix for suffix in JS_TS_SUFFIXES)
        probes.extend(str(Path(normalized) / f"index{suffix}").replace(os.sep, "/") for suffix in JS_TS_SUFFIXES)
    else:
        probes = [spec]
    for probe in probes:
        key = re.sub(r"\.(js|jsx|ts|tsx)$", "", probe)
        if probe in module_to_path:
            return module_to_path[probe]
        if key in module_to_path:
            return module_to_path[key]
        if "./" + key in module_to_path:
            return module_to_path["./" + key]
    return None


def _js_ts_imports(rel: str, text: str, module_to_path: Dict[str, str]) -> List[Dict[str, Any]]:
    imports: List[Dict[str, Any]] = []
    patterns = [
        re.compile(r"^\s*import\s+(?:.+?\s+from\s+)?['\"]([^'\"]+)['\"]", re.MULTILINE),
        re.compile(r"^\s*export\s+.+?\s+from\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
        re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)"),
    ]
    seen = set()
    line_offsets = _line_offsets(text)
    for pattern in patterns:
        for match in pattern.finditer(text):
            spec = match.group(1)
            if (pattern.pattern, spec, match.start()) in seen:
                continue
            seen.add((pattern.pattern, spec, match.start()))
            resolved = _resolve_js_import_path(rel, spec, module_to_path)
            imports.append(
                {
                    "module": spec,
                    "name": None,
                    "alias": None,
                    "level": 0,
                    "resolved_path": resolved,
                    "external": resolved is None,
                    "line": _line_for_offset(line_offsets, match.start()),
                }
            )
    return imports


def _js_ts_symbols(rel: str, text: str) -> List[Dict[str, Any]]:
    patterns = [
        ("class", re.compile(r"^\s*(?:export\s+default\s+|export\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+default\s+|export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^=]*?\)\s*=>", re.MULTILINE)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function\b", re.MULTILINE)),
    ]
    line_offsets = _line_offsets(text)
    out: List[Dict[str, Any]] = []
    seen = set()
    module = str(Path(rel).with_suffix("")).replace(os.sep, ".")
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            name = match.group(1)
            key = (name, match.start())
            if key in seen:
                continue
            seen.add(key)
            start_line = _line_for_offset(line_offsets, match.start())
            symbol_id = _stable_hash({"path": rel, "qualname": name, "kind": kind}, 18)
            out.append(
                {
                    "id": symbol_id,
                    "path": rel,
                    "module": module,
                    "qualname": name,
                    "name": name,
                    "kind": kind,
                    "language": _language_for_path(rel),
                    "parser_backend": "static_regex",
                    "start_line": start_line,
                    "end_line": start_line,
                    "signature": _js_signature_line(text, match.start()),
                    "decorators": [],
                    "doc": "",
                }
            )
    return out


def _js_ts_calls(rel: str, text: str, file_symbols: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    if not file_symbols:
        return calls
    line_offsets = _line_offsets(text)
    symbols_by_line = sorted(file_symbols, key=lambda item: int(item.get("start_line") or 0))
    call_pattern = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")
    ignored = {"if", "for", "while", "switch", "catch", "function", "return"}
    for match in call_pattern.finditer(text):
        callee = match.group(1)
        if callee.split(".", 1)[0] in ignored:
            continue
        line = _line_for_offset(line_offsets, match.start())
        caller = _nearest_symbol_before(symbols_by_line, line)
        if not caller:
            continue
        calls.append(
            {
                "path": rel,
                "caller": caller.get("name"),
                "caller_id": caller.get("id"),
                "callee": callee,
                "line": line,
            }
        )
    return calls


def _line_offsets(text: str) -> List[int]:
    offsets = [0]
    for match in re.finditer(r"\n", text):
        offsets.append(match.end())
    return offsets


def _line_for_offset(offsets: Sequence[int], offset: int) -> int:
    line = 1
    for idx, start in enumerate(offsets):
        if start > offset:
            break
        line = idx + 1
    return line


def _nearest_symbol_before(symbols: Sequence[Dict[str, Any]], line: int) -> Optional[Dict[str, Any]]:
    current = None
    for symbol in symbols:
        if int(symbol.get("start_line") or 0) <= line:
            current = symbol
        else:
            break
    return current


def _js_signature_line(text: str, offset: int, limit: int = 180) -> str:
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", offset)
    if line_end < 0:
        line_end = len(text)
    return " ".join(text[line_start:line_end].strip().split())[:limit]


def _tree_sitter_key_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".tsx":
        return "tsx"
    return _language_for_path(path)


def _load_tree_sitter_parser(language_key: str) -> Tuple[Optional[Any], Dict[str, Any]]:
    spec = TREE_SITTER_LANGUAGE_SPECS.get(language_key)
    if not spec:
        return None, {"available": False, "reason": "language not configured"}
    module_name, attr = spec
    try:
        from tree_sitter import Language, Parser  # type: ignore

        module = importlib.import_module(module_name)
        language = Language(getattr(module, attr)())
        try:
            parser = Parser(language)
        except TypeError:
            parser = Parser()
            parser.set_language(language)
        return parser, {
            "available": True,
            "module": module_name,
            "language_attr": attr,
        }
    except Exception as exc:
        return None, {
            "available": False,
            "module": module_name,
            "language_attr": attr,
            "reason": str(exc),
        }


def _syntax_index(
    repo_root: Path,
    files: Sequence[str],
    previous: Optional[Dict[str, Any]] = None,
    incremental: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    source_files = [path for path in files if bool(_tree_sitter_key_for_path(path))]
    reuse_paths = set((incremental or {}).get("unchanged_files") or [])
    previous_index = (previous or {}).get("syntax_index") or {}
    previous_spans = previous_index.get("spans") if isinstance(previous_index, dict) else None
    previous_calls = previous_index.get("call_sites") if isinstance(previous_index, dict) else None
    can_reuse = bool(previous_spans)
    reusable_paths = {path for path in reuse_paths if path in source_files} if can_reuse else set()
    spans: List[Dict[str, Any]] = [
        dict(span)
        for span in (previous_spans or [])
        if str(span.get("path") or "") in reusable_paths
    ]
    call_sites: List[Dict[str, Any]] = [
        dict(call)
        for call in (previous_calls or [])
        if str(call.get("path") or "") in reusable_paths
    ]
    diagnostics: List[Dict[str, Any]] = []
    parsers: Dict[str, Any] = {}
    grammar_status: Dict[str, Any] = {}
    parsed_files = 0
    failed_files = 0

    for rel in source_files:
        if rel in reusable_paths:
            continue
        language_key = _tree_sitter_key_for_path(rel)
        if language_key not in parsers:
            parser, status = _load_tree_sitter_parser(language_key)
            parsers[language_key] = parser
            grammar_status[language_key] = status
        parser = parsers.get(language_key)
        if parser is None:
            continue
        path = repo_root / rel
        try:
            if path.stat().st_size > MAX_INDEX_FILE_BYTES:
                continue
            source = path.read_bytes()
            tree = parser.parse(source)
        except Exception as exc:
            failed_files += 1
            diagnostics.append({"path": rel, "language": language_key, "error": str(exc)[:180]})
            continue
        parsed_files += 1
        if getattr(tree.root_node, "has_error", False):
            diagnostics.append({"path": rel, "language": language_key, "warning": "tree-sitter parse reported syntax errors"})
        file_spans, file_calls = _tree_sitter_index_for_file(rel, language_key, source, tree.root_node)
        spans.extend(file_spans)
        call_sites.extend(file_calls)
        if len(spans) >= MAX_SYNTAX_SPANS:
            spans = spans[:MAX_SYNTAX_SPANS]
        if len(call_sites) >= MAX_CALL_SITES:
            call_sites = call_sites[:MAX_CALL_SITES]
        if len(spans) >= MAX_SYNTAX_SPANS and len(call_sites) >= MAX_CALL_SITES:
            break

    active_languages = sorted(
        key
        for key, status in grammar_status.items()
        if isinstance(status, dict) and status.get("available")
    )
    syntax = {
        "schema_version": "dhee.syntax_index.v1",
        "backend": "tree_sitter",
        "active": bool(active_languages),
        "languages": grammar_status,
        "spans": spans[:MAX_SYNTAX_SPANS],
        "call_sites": call_sites[:MAX_CALL_SITES],
        "summary": {
            "source_file_count": len(source_files),
            "parsed_file_count": parsed_files,
            "reused_file_count": len(reusable_paths),
            "failed_file_count": failed_files,
            "span_count": len(spans[:MAX_SYNTAX_SPANS]),
            "call_site_count": len(call_sites[:MAX_CALL_SITES]),
        },
        "diagnostics": diagnostics[:40],
    }
    reuse = {
        "schema_version": "dhee.syntax_index_reuse.v1",
        "mode": "incremental_reuse" if reusable_paths else "full_parse",
        "eligible_file_count": len(reuse_paths),
        "reused_file_count": len(reusable_paths),
        "fresh_file_count": max(0, len(source_files) - len(reusable_paths)),
        "reason": "" if can_reuse else "previous brain has no reusable syntax_index spans",
    }
    return syntax, reuse


def _tree_sitter_index_for_file(rel: str, language_key: str, source: bytes, root: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    spans: List[Dict[str, Any]] = []
    calls: List[Dict[str, Any]] = []
    symbol_types = TREE_SITTER_SYMBOL_TYPES.get(language_key) or {}

    def visit(node: Any, parents: Sequence[str]) -> None:
        span = _tree_sitter_span_from_node(rel, language_key, source, node, parents, symbol_types)
        next_parents = list(parents)
        if span:
            spans.append(span)
            if span.get("kind") in {"class", "function", "method"} and span.get("name"):
                next_parents.append(str(span.get("name")))
        call = _tree_sitter_call_from_node(rel, language_key, source, node, next_parents)
        if call:
            calls.append(call)
        for child in getattr(node, "children", []) or []:
            visit(child, next_parents)

    visit(root, [])
    return spans, calls


def _tree_sitter_span_from_node(
    rel: str,
    language_key: str,
    source: bytes,
    node: Any,
    parents: Sequence[str],
    symbol_types: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    node_type = str(getattr(node, "type", "") or "")
    kind = symbol_types.get(node_type)
    if not kind:
        return None
    if node_type == "variable_declarator":
        value = node.child_by_field_name("value")
        if value is None or str(getattr(value, "type", "") or "") not in {"arrow_function", "function", "function_expression"}:
            return None
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = _node_text(source, name_node)
    if not name:
        return None
    qual_parts = list(parents) + [name]
    start_line = int(node.start_point[0]) + 1
    end_line = int(node.end_point[0]) + 1
    span_id = _stable_hash(
        {
            "path": rel,
            "qualname": ".".join(qual_parts),
            "node_type": node_type,
            "start": start_line,
        },
        18,
    )
    return {
        "id": span_id,
        "path": rel,
        "language": "typescript" if language_key == "tsx" else language_key,
        "parser_backend": "tree_sitter",
        "node_type": node_type,
        "kind": kind,
        "name": name,
        "qualname": ".".join(qual_parts),
        "parent": ".".join(parents),
        "start_line": start_line,
        "end_line": end_line,
        "signature": _node_signature(source, node),
    }


def _tree_sitter_call_from_node(
    rel: str,
    language_key: str,
    source: bytes,
    node: Any,
    parents: Sequence[str],
) -> Optional[Dict[str, Any]]:
    node_type = str(getattr(node, "type", "") or "")
    if language_key == "python":
        call_types = {"call"}
    else:
        call_types = {"call_expression"}
    if node_type not in call_types:
        return None
    function_node = node.child_by_field_name("function")
    if function_node is None:
        return None
    callee = _normalize_tree_sitter_callee(_node_text(source, function_node))
    if not callee:
        return None
    caller = ".".join(parents) if parents else ""
    return {
        "path": rel,
        "language": "typescript" if language_key == "tsx" else language_key,
        "parser_backend": "tree_sitter",
        "caller": parents[-1] if parents else None,
        "caller_qualname": caller,
        "caller_id": _stable_hash({"path": rel, "qualname": caller}, 18) if caller else None,
        "callee": callee,
        "line": int(node.start_point[0]) + 1,
        "column": int(node.start_point[1]) + 1,
    }


def _normalize_tree_sitter_callee(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    text = re.sub(r"<[^>]*>$", "", text)
    text = re.sub(r"\?\.", ".", text)
    text = re.sub(r"\s+", "", text)
    return text[:180]


def _node_text(source: bytes, node: Any) -> str:
    return source[int(node.start_byte): int(node.end_byte)].decode("utf-8", errors="replace")


def _node_signature(source: bytes, node: Any, limit: int = 180) -> str:
    start = int(node.start_byte)
    line_start = source.rfind(b"\n", 0, start) + 1
    line_end = source.find(b"\n", start)
    if line_end < 0:
        line_end = len(source)
    return " ".join(source[line_start:line_end].decode("utf-8", errors="replace").strip().split())[:limit]


def _merge_syntax_symbols(symbols: Sequence[Dict[str, Any]], syntax_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged = [dict(symbol) for symbol in symbols]
    seen = {
        (str(symbol.get("path") or ""), str(symbol.get("qualname") or ""), str(symbol.get("kind") or ""))
        for symbol in merged
    }
    for span in syntax_index.get("spans") or []:
        key = (str(span.get("path") or ""), str(span.get("qualname") or ""), str(span.get("kind") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            {
                "id": span.get("id"),
                "path": span.get("path"),
                "module": str(Path(str(span.get("path") or "")).with_suffix("")).replace(os.sep, "."),
                "qualname": span.get("qualname"),
                "name": span.get("name"),
                "kind": span.get("kind"),
                "language": span.get("language"),
                "parser_backend": "tree_sitter",
                "start_line": span.get("start_line"),
                "end_line": span.get("end_line"),
                "signature": span.get("signature") or "",
                "decorators": [],
                "doc": "",
            }
        )
        if len(merged) >= MAX_SYMBOLS:
            break
    return merged[:MAX_SYMBOLS]


def _merge_call_sites(call_sites: Sequence[Dict[str, Any]], syntax_index: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for call in call_sites:
        key = _call_site_key(call)
        if not key:
            continue
        merged[key] = dict(call)
    for call in syntax_index.get("call_sites") or []:
        key = _call_site_key(call)
        if not key:
            continue
        current = merged.get(key)
        if not current or str(current.get("parser_backend") or "") != "tree_sitter":
            merged[key] = dict(call)
    return sorted(
        merged.values(),
        key=lambda item: (str(item.get("path") or ""), int(item.get("line") or 0), str(item.get("callee") or "")),
    )[:MAX_CALL_SITES]


def _call_site_key(call: Dict[str, Any]) -> Optional[Tuple[str, str, int]]:
    path = str(call.get("path") or "")
    callee = str(call.get("callee") or "")
    line = int(call.get("line") or 0)
    if not path or not callee or line <= 0:
        return None
    return path, callee, line


def _dependency_graph(imports: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    local_edges: List[Dict[str, Any]] = []
    reverse: DefaultDict[str, List[str]] = defaultdict(list)
    external: DefaultDict[str, int] = defaultdict(int)
    for source, items in imports.items():
        for item in items:
            resolved = item.get("resolved_path")
            if resolved:
                local_edges.append({"source": source, "target": resolved, "module": item.get("module")})
                reverse[str(resolved)].append(source)
            else:
                root = str(item.get("module") or "").split(".", 1)[0]
                if root:
                    external[root] += 1
    return {
        "local_import_edges": local_edges[:3_000],
        "reverse_local_imports": {key: sorted(set(value))[:40] for key, value in reverse.items()},
        "external_imports": [{"module": key, "count": count} for key, count in sorted(external.items())],
    }


def _setup_commands(files: Sequence[str]) -> List[str]:
    file_set = set(files)
    commands: List[str] = []
    if "pyproject.toml" in file_set or "setup.py" in file_set:
        commands.append('pip install -e ".[dev]"')
    if "requirements.txt" in file_set:
        commands.append("pip install -r requirements.txt")
    if "package.json" in file_set:
        commands.append("npm install")
    return commands[:8]


def _risky_files(files: Sequence[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rel in files:
        lower = rel.lower()
        reasons = [name for name in _RISKY_NAMES if name in lower]
        if reasons:
            out.append({"path": rel, "reasons": reasons})
        if len(out) >= 120:
            break
    return out


def _test_map(
    files: Sequence[str],
    symbols: Sequence[Dict[str, Any]],
    imports: Dict[str, List[Dict[str, Any]]],
    must_run: Sequence[str],
) -> Dict[str, Any]:
    test_files = [path for path in files if _is_test_file(path)]
    source_files = [path for path in files if _file_kind(path) == "source" and bool(_language_for_path(path))]
    symbols_by_path: DefaultDict[str, List[str]] = defaultdict(list)
    test_symbols: List[Dict[str, Any]] = []
    for symbol in symbols:
        path = str(symbol.get("path") or "")
        name = str(symbol.get("name") or "")
        if name:
            symbols_by_path[path].append(name)
        if path in test_files and (name.startswith("test_") or symbol.get("kind") == "class"):
            test_symbols.append(
                {
                    "path": path,
                    "name": name,
                    "qualname": symbol.get("qualname"),
                    "line": symbol.get("start_line"),
                    "kind": symbol.get("kind"),
                }
            )
    source_to_tests: Dict[str, List[Dict[str, Any]]] = {}
    test_to_sources: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    must_text = "\n".join(must_run)
    for source in source_files:
        source_tokens = set(_tokens(_path_text(source) + " " + " ".join(symbols_by_path.get(source, [])[:40])))
        ranked: List[Tuple[float, str, List[str]]] = []
        source_stem = Path(source).stem.replace("_", " ").lower()
        for test in test_files:
            reasons: List[str] = []
            score = 0.0
            test_text = _path_text(test) + " " + " ".join(symbols_by_path.get(test, [])[:80]).lower()
            imported_sources = {str(item.get("resolved_path")) for item in imports.get(test, []) if item.get("resolved_path")}
            if source in imported_sources:
                score += 8.0
                reasons.append("test imports source module")
            if source_stem and source_stem in test_text:
                score += 5.0
                reasons.append("test name/path matches source stem")
            overlap = source_tokens & set(_tokens(test_text))
            if overlap:
                score += min(4.0, len(overlap) * 0.75)
                reasons.append("shared issue/symbol tokens: " + ", ".join(sorted(overlap)[:5]))
            if test in must_text:
                score += 4.0
                reasons.append("explicit must-run command mentions test")
            if score > 0:
                ranked.append((score, test, reasons))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        links = [
            {
                "path": test,
                    "score": round(score, 3),
                    "confidence": round(min(0.95, 0.25 + score / 16.0), 3),
                    "reasons": reasons,
                    "command": _test_command_for_path(test),
                }
            for score, test, reasons in ranked[:MAX_TEST_LINKS_PER_SOURCE]
        ]
        if links:
            source_to_tests[source] = links
            for link in links:
                test_to_sources[link["path"]].append(
                    {"path": source, "score": link["score"], "confidence": link["confidence"]}
                )
    return {
        "test_files": test_files[:500],
        "tests": test_files[:500],
        "test_symbols": test_symbols[:1_000],
        "source_to_tests": source_to_tests,
        "test_to_sources": {key: value[:MAX_TEST_LINKS_PER_SOURCE] for key, value in test_to_sources.items()},
        "must_run": list(must_run),
    }


def _test_ownership_index(
    files: Sequence[str],
    imports: Dict[str, List[Dict[str, Any]]],
    test_map: Dict[str, Any],
    coverage_map: Dict[str, Any],
    failure_index: Dict[str, Any],
    flaky_tests: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    file_set = set(files)
    source_files = {path for path in files if _file_kind(path) == "source" and bool(_language_for_path(path))}
    test_files = {path for path in files if _is_test_file(path)}
    source_to_tests: DefaultDict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    test_to_sources: DefaultDict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    def add(source: str, test: str, score: float, reason: str, evidence: str, *, lines: Optional[Sequence[int]] = None) -> None:
        if source not in source_files or test not in test_files:
            return
        current = source_to_tests[source].setdefault(
            test,
            {
                "path": test,
                "command": _test_command_for_path(test),
                "score": 0.0,
                "reasons": [],
                "evidence_pointers": [],
                "covered_lines": [],
            },
        )
        current["score"] = float(current.get("score") or 0.0) + float(score)
        current["reasons"].append(reason)
        current["evidence_pointers"].append(evidence)
        current["covered_lines"].extend(int(line) for line in (lines or []) if line)

    for test in sorted(test_files):
        for item in imports.get(test, []) or []:
            source = str(item.get("resolved_path") or "")
            if source:
                add(source, test, 10.0, "test imports source module", f"import:{test}->{source}")

    for source, links in (test_map.get("source_to_tests") or {}).items():
        for link in links or []:
            test = str(link.get("path") or "")
            add(
                str(source),
                test,
                float(link.get("score") or 0.0),
                "nearest test-map link",
                f"test_map:{source}->{test}",
            )

    for source, item in (coverage_map.get("files") or {}).items():
        contexts = item.get("coverage_contexts") or []
        lines_by_test: DefaultDict[str, List[int]] = defaultdict(list)
        for context in contexts:
            test = str(context.get("test_path") or "")
            if test:
                line = context.get("line")
                if line:
                    lines_by_test[test].append(int(line))
        for test, lines in lines_by_test.items():
            add(
                str(source),
                test,
                12.0 + min(4.0, len(set(lines)) * 0.25),
                "coverage context executed source lines",
                f"coverage_context:{source}->{test}",
                lines=sorted(set(lines))[:80],
            )

    for source, item in ((failure_index.get("by_file") or {}).items()):
        for command in item.get("commands") or []:
            for test in _test_paths_from_command(command, file_set):
                add(
                    str(source),
                    test,
                    6.0,
                    "failure command links test to source failure evidence",
                    f"failure_index:{source}->{test}",
                    lines=item.get("lines") or [],
                )

    reverse_map = test_map.get("test_to_sources") or {}
    for signal in flaky_tests:
        command = str(signal.get("test_command") or "")
        for test in _test_paths_from_command(command, file_set):
            for link in reverse_map.get(test, []) or []:
                source = str(link.get("path") or "")
                add(
                    source,
                    test,
                    2.0,
                    "flaky-test signal touches source-owned test",
                    f"flaky_test:{command}",
                )

    compact_source_to_tests: Dict[str, List[Dict[str, Any]]] = {}
    for source, tests in source_to_tests.items():
        items = []
        for item in tests.values():
            score = float(item.get("score") or 0.0)
            items.append(
                {
                    "path": item.get("path"),
                    "command": item.get("command"),
                    "score": round(score, 3),
                    "confidence": round(min(0.96, 0.28 + score / 24.0), 3),
                    "reasons": list(dict.fromkeys(item.get("reasons") or []))[:8],
                    "evidence_pointers": list(dict.fromkeys(item.get("evidence_pointers") or []))[:8],
                    "covered_lines": sorted(set(item.get("covered_lines") or []))[:80],
                }
            )
        items.sort(key=lambda row: (-float(row.get("score") or 0.0), str(row.get("path") or "")))
        compact_source_to_tests[source] = items[:MAX_TEST_LINKS_PER_SOURCE]
        for item in compact_source_to_tests[source]:
            test = str(item.get("path") or "")
            if test:
                test_to_sources[test][source] = {
                    "path": source,
                    "score": item.get("score"),
                    "confidence": item.get("confidence"),
                    "reasons": item.get("reasons") or [],
                    "evidence_pointers": item.get("evidence_pointers") or [],
                }

    compact_test_to_sources = {
        test: sorted(items.values(), key=lambda row: (-float(row.get("score") or 0.0), str(row.get("path") or "")))[:MAX_TEST_LINKS_PER_SOURCE]
        for test, items in test_to_sources.items()
    }
    edge_count = sum(len(items) for items in compact_source_to_tests.values())
    return {
        "schema_version": "dhee.test_ownership_index.v1",
        "source_to_tests": compact_source_to_tests,
        "test_to_sources": compact_test_to_sources,
        "summary": {
            "source_file_count": len(compact_source_to_tests),
            "test_file_count": len(compact_test_to_sources),
            "edge_count": min(edge_count, MAX_TEST_OWNERSHIP_EDGES),
        },
    }


def _test_paths_from_command(command: str, files: Sequence[str] | set[str]) -> List[str]:
    file_set = set(files)
    out: List[str] = []
    for path in sorted(file_set):
        if not _is_test_file(path):
            continue
        if path in str(command or ""):
            out.append(path)
    if out:
        return out
    for token in re.split(r"\s+", str(command or "")):
        path = _test_path_from_nodeid(token, file_set)
        if path:
            out.append(path)
    return list(dict.fromkeys(out))


def _historical_failure_signatures(repo_root: Path, goal: str, limit: int = 40) -> List[Dict[str, Any]]:
    tokens = set(_tokens(goal))
    out: List[Dict[str, Any]] = []
    runs_root = repo_link.repo_context_dir(repo_root) / "task_runs"
    for path in sorted(runs_root.glob("**/*.jsonl"), reverse=True):
        if len(out) >= limit:
            break
        checked = read_jsonl_checked(path)
        for record in reversed(checked.get("records") or []):
            blob = json.dumps(record, sort_keys=True, default=str)
            lowered = blob.lower()
            if not _FAILURE_RE.search(lowered):
                continue
            if tokens and not any(token in lowered for token in tokens):
                continue
            out.append(
                {
                    "source": "task_run_event",
                    "ref": f"runtime_event:{os.path.relpath(path, repo_root).replace(os.sep, '/')}",
                    "task_id": record.get("task_id") or record.get("contract_id"),
                    "command": ((record.get("action") or {}).get("command") if isinstance(record.get("action"), dict) else None),
                    "outcome": record.get("outcome") or record.get("decision"),
                    "signature": _compact_failure_signature(blob),
                    "content_hash": _stable_hash(blob, 16),
                }
            )
            if len(out) >= limit:
                break
    try:
        entries = repo_link.list_entries(repo_root)
    except Exception:
        entries = []
    for entry in reversed(entries):
        if len(out) >= limit:
            break
        text = f"{entry.kind} {entry.title} {entry.content}".lower()
        if not _FAILURE_RE.search(text):
            continue
        if tokens and not any(token in text for token in tokens):
            continue
        out.append(
            {
                "source": "repo_context",
                "ref": f"repo_context:{entry.id}",
                "title": entry.title,
                "kind": entry.kind,
                "signature": _compact_failure_signature(entry.content),
                "content_hash": entry.content_hash,
            }
        )
    return out


def _failure_index(repo_root: Path, files: Sequence[str], goal: str, limit: int = MAX_FAILURE_RECORDS) -> Dict[str, Any]:
    file_set = set(files)
    tokens = set(_tokens(goal))
    records: List[Dict[str, Any]] = []
    by_file: Dict[str, Dict[str, Any]] = {}
    runs_root = repo_link.repo_context_dir(repo_root) / "task_runs"
    for path in sorted(runs_root.glob("**/*.jsonl"), reverse=True):
        if len(records) >= limit:
            break
        checked = read_jsonl_checked(path)
        for record in reversed(checked.get("records") or []):
            blob = json.dumps(record, sort_keys=True, default=str)
            lowered = blob.lower()
            if not _FAILURE_RE.search(lowered):
                continue
            refs = _file_line_refs(blob, file_set)
            command = _record_command(record)
            signature = _compact_failure_signature(blob)
            item = {
                "ref": f"runtime_event:{os.path.relpath(path, repo_root).replace(os.sep, '/')}",
                "command": command,
                "outcome": record.get("outcome") or record.get("decision"),
                "signature": signature,
                "path_refs": refs[:20],
                "goal_match": bool(tokens and any(token in lowered for token in tokens)),
                "content_hash": _stable_hash(blob, 16),
            }
            records.append(item)
            for ref in refs:
                file_path = str(ref.get("path") or "")
                if not file_path:
                    continue
                bucket = by_file.setdefault(
                    file_path,
                    {
                        "path": file_path,
                        "failure_count": 0,
                        "lines": [],
                        "commands": [],
                        "signatures": [],
                        "refs": [],
                        "goal_match_count": 0,
                    },
                )
                bucket["failure_count"] = int(bucket.get("failure_count") or 0) + 1
                if ref.get("line"):
                    bucket["lines"].append(int(ref.get("line") or 0))
                if command:
                    bucket["commands"].append(command)
                if signature:
                    bucket["signatures"].append(signature)
                bucket["refs"].append(item["ref"])
                if item["goal_match"]:
                    bucket["goal_match_count"] = int(bucket.get("goal_match_count") or 0) + 1
            if len(records) >= limit:
                break

    lastfailed = repo_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
    checked = read_json_checked(lastfailed)
    data = checked.get("data") if checked.get("ok") else None
    if isinstance(data, dict):
        for nodeid in data:
            refs = _file_line_refs(str(nodeid), file_set)
            for ref in refs:
                file_path = str(ref.get("path") or "")
                if not file_path:
                    continue
                bucket = by_file.setdefault(
                    file_path,
                    {
                        "path": file_path,
                        "failure_count": 0,
                        "lines": [],
                        "commands": [],
                        "signatures": [],
                        "refs": [],
                        "goal_match_count": 0,
                    },
                )
                bucket["failure_count"] = int(bucket.get("failure_count") or 0) + 1
                bucket["commands"].append(f"pytest {nodeid}")
                bucket["signatures"].append(str(nodeid))
                bucket["refs"].append(".pytest_cache/v/cache/lastfailed")

    compact_by_file = {}
    for path, bucket in by_file.items():
        compact_by_file[path] = {
            "path": path,
            "failure_count": int(bucket.get("failure_count") or 0),
            "goal_match_count": int(bucket.get("goal_match_count") or 0),
            "lines": sorted(set(int(line) for line in bucket.get("lines") or [] if line))[:40],
            "commands": list(dict.fromkeys(bucket.get("commands") or []))[:12],
            "signatures": list(dict.fromkeys(bucket.get("signatures") or []))[:8],
            "refs": list(dict.fromkeys(bucket.get("refs") or []))[:12],
        }
    return {
        "schema_version": "dhee.failure_index.v1",
        "records": records[:limit],
        "by_file": compact_by_file,
        "summary": {
            "record_count": len(records[:limit]),
            "file_count": len(compact_by_file),
            "path_ref_count": sum(len(record.get("path_refs") or []) for record in records[:limit]),
        },
    }


def _file_line_refs(text: str, files: Sequence[str] | set[str]) -> List[Dict[str, Any]]:
    file_set = set(files)
    refs: List[Dict[str, Any]] = []
    seen = set()
    for match in _PATH_LINE_RE.finditer(str(text or "")):
        raw_path = str(match.group("path") or "").strip().strip("'\"()[]{}")
        path = raw_path.replace("\\", "/").lstrip("./")
        if path not in file_set:
            suffix_match = [candidate for candidate in file_set if candidate.endswith("/" + path)]
            if len(suffix_match) == 1:
                path = suffix_match[0]
            else:
                continue
        line = match.group("line") or match.group("pyline")
        key = (path, int(line or 0))
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            {
                "path": path,
                "line": int(line) if line else None,
                "evidence": match.group(0)[:220],
            }
        )
        if len(refs) >= MAX_FAILURE_REFS:
            break
    if refs:
        return refs
    lowered = str(text or "").lower()
    for path in sorted(file_set):
        if path.lower() in lowered:
            refs.append({"path": path, "line": None, "evidence": path})
            if len(refs) >= 40:
                break
    return refs


def _compact_failure_signature(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    selected: List[str] = []
    for line in lines:
        if _FAILURE_RE.search(line) or line.startswith(("E ", "FAILED ", "Traceback")):
            selected.append(line[:220])
        if len(selected) >= 4:
            break
    if not selected and lines:
        selected.append(lines[0][:220])
    return " | ".join(selected)[:500]


def _coverage_map(repo_root: Path, files: Sequence[str]) -> Dict[str, Any]:
    coverage_files: Dict[str, Dict[str, Any]] = {}
    sources = set(files)
    xml_path = repo_root / "coverage.xml"
    if xml_path.exists():
        try:
            root = ET.parse(xml_path).getroot()
            for class_el in root.findall(".//class"):
                filename = str(class_el.attrib.get("filename") or "").replace(os.sep, "/")
                filename = filename.lstrip("./")
                if filename not in sources and str(Path(filename)) not in sources:
                    continue
                line_rate = _float(class_el.attrib.get("line-rate"), 0.0)
                branch_rate = _float(class_el.attrib.get("branch-rate"), 0.0)
                uncovered = [
                    int(line.attrib.get("number") or 0)
                    for line in class_el.findall(".//line")
                    if int(line.attrib.get("hits") or 0) == 0
                ][:200]
                coverage_files[filename] = {
                    "path": filename,
                    "source": "coverage.xml",
                    "line_rate": round(line_rate, 4),
                    "branch_rate": round(branch_rate, 4),
                    "uncovered_lines": uncovered,
                }
        except Exception:
            pass
    json_path = repo_root / "coverage" / "coverage-final.json"
    if json_path.exists():
        checked = read_json_checked(json_path)
        data = checked.get("data") if checked.get("ok") else None
        if isinstance(data, dict):
            for raw_path, entry in data.items():
                rel = os.path.relpath(str(raw_path), repo_root).replace(os.sep, "/") if os.path.isabs(str(raw_path)) else str(raw_path).replace(os.sep, "/")
                if rel not in sources:
                    continue
                statements = (entry or {}).get("s") if isinstance(entry, dict) else None
                if not isinstance(statements, dict):
                    continue
                total = len(statements)
                covered = sum(1 for hits in statements.values() if int(hits or 0) > 0)
                coverage_files[rel] = {
                    "path": rel,
                    "source": "coverage/coverage-final.json",
                    "line_rate": round(covered / total, 4) if total else 0.0,
                    "branch_rate": 0.0,
                    "uncovered_lines": [],
                }
    coverage_json_path = repo_root / "coverage.json"
    checked = read_json_checked(coverage_json_path)
    data = checked.get("data") if checked.get("ok") else None
    coverage_py_files = data.get("files") if isinstance(data, dict) else None
    if isinstance(coverage_py_files, dict):
        for raw_path, entry in coverage_py_files.items():
            rel = os.path.relpath(str(raw_path), repo_root).replace(os.sep, "/") if os.path.isabs(str(raw_path)) else str(raw_path).replace(os.sep, "/")
            rel = rel.lstrip("./")
            if rel not in sources:
                continue
            entry = entry if isinstance(entry, dict) else {}
            summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
            contexts = _coverage_context_records(entry.get("contexts"), sources)
            test_contexts = sorted({item["test_path"] for item in contexts if item.get("test_path")})
            current = dict(coverage_files.get(rel) or {"path": rel})
            line_rate = _coverage_line_rate_from_summary(summary, current.get("line_rate"))
            current.update(
                {
                    "path": rel,
                    "source": "coverage.json",
                    "line_rate": line_rate,
                    "branch_rate": _float(summary.get("percent_covered_branches"), current.get("branch_rate") or 0.0) / 100.0
                    if summary.get("percent_covered_branches") is not None else current.get("branch_rate", 0.0),
                    "uncovered_lines": list(entry.get("missing_lines") or current.get("uncovered_lines") or [])[:200],
                    "coverage_contexts": contexts[:200],
                    "test_contexts": test_contexts[:80],
                }
            )
            coverage_files[rel] = current
    return {
        "schema_version": "dhee.coverage_map.v1",
        "files": coverage_files,
        "summary": {
            "covered_file_count": len(coverage_files),
            "mean_line_rate": round(
                sum(float(item.get("line_rate") or 0.0) for item in coverage_files.values()) / len(coverage_files),
                4,
            ) if coverage_files else 0.0,
        },
    }


def _coverage_line_rate_from_summary(summary: Dict[str, Any], default: Any) -> float:
    if summary.get("percent_covered") is not None:
        return round(_float(summary.get("percent_covered"), 0.0) / 100.0, 4)
    covered = summary.get("covered_lines")
    total = summary.get("num_statements") or summary.get("num_lines")
    if total:
        return round(_float(covered, 0.0) / _float(total, 1.0), 4)
    return round(_float(default, 0.0), 4)


def _coverage_context_records(contexts: Any, files: Sequence[str] | set[str]) -> List[Dict[str, Any]]:
    if not isinstance(contexts, dict):
        return []
    out: List[Dict[str, Any]] = []
    file_set = set(files)
    for line, values in contexts.items():
        raw_values = values if isinstance(values, list) else [values]
        for raw in raw_values:
            context = str(raw or "")
            test_path = _test_path_from_nodeid(context, file_set)
            if not test_path:
                continue
            out.append(
                {
                    "line": int(line) if str(line).isdigit() else None,
                    "context": context[:240],
                    "test_path": test_path,
                }
            )
    return out


def _test_path_from_nodeid(value: str, files: Sequence[str] | set[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("|", 1)[0].strip()
    text = text.split(" ", 1)[0].strip()
    candidate = text.split("::", 1)[0].strip().replace("\\", "/").lstrip("./")
    file_set = set(files)
    if candidate in file_set and _is_test_file(candidate):
        return candidate
    suffix_match = [path for path in file_set if _is_test_file(path) and path.endswith("/" + candidate)]
    if len(suffix_match) == 1:
        return suffix_match[0]
    return ""


def _flaky_test_signals(repo_root: Path, limit: int = 80) -> List[Dict[str, Any]]:
    outcomes: DefaultDict[str, Dict[str, Any]] = defaultdict(lambda: {"pass": 0, "fail": 0, "examples": []})
    runs_root = repo_link.repo_context_dir(repo_root) / "task_runs"
    for path in sorted(runs_root.glob("**/*.jsonl"), reverse=True):
        checked = read_jsonl_checked(path)
        for record in checked.get("records") or []:
            command = _record_command(record)
            if not command or "pytest" not in command:
                continue
            outcome = str(record.get("outcome") or record.get("decision") or "").lower()
            text = json.dumps(record, sort_keys=True, default=str).lower()
            bucket = outcomes[command]
            if "pass" in outcome or "success" in outcome or "passed" in text:
                bucket["pass"] += 1
            if _FAILURE_RE.search(text) or "fail" in outcome or "error" in outcome:
                bucket["fail"] += 1
            if len(bucket["examples"]) < 3:
                bucket["examples"].append(
                    {
                        "ref": f"runtime_event:{os.path.relpath(path, repo_root).replace(os.sep, '/')}",
                        "outcome": outcome,
                        "signature": _compact_failure_signature(text),
                    }
                )
    lastfailed = repo_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
    checked = read_json_checked(lastfailed)
    data = checked.get("data") if checked.get("ok") else None
    if isinstance(data, dict):
        for nodeid in data:
            command = f"pytest {nodeid}"
            outcomes[command]["fail"] += 1
            outcomes[command]["examples"].append({"ref": ".pytest_cache/v/cache/lastfailed", "outcome": "lastfailed"})
    signals: List[Dict[str, Any]] = []
    for command, stats in outcomes.items():
        passes = int(stats.get("pass") or 0)
        failures = int(stats.get("fail") or 0)
        if failures <= 0:
            continue
        is_flaky = passes > 0 and failures > 0
        signals.append(
            {
                "test_command": command,
                "status": "flaky" if is_flaky else "recent_failure",
                "pass_count": passes,
                "failure_count": failures,
                "confidence": round(0.72 if is_flaky else 0.48, 3),
                "evidence": stats.get("examples") or [],
            }
        )
    signals.sort(key=lambda item: (-int(item.get("failure_count") or 0), item.get("test_command") or ""))
    return signals[:limit]


def _record_command(record: Dict[str, Any]) -> str:
    action = record.get("action")
    if isinstance(action, dict) and action.get("command"):
        return str(action.get("command") or "")
    for key in ("command", "cmd"):
        if record.get(key):
            return str(record.get(key) or "")
    return ""


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _git_ownership_index(repo_root: Path, files: Sequence[str], max_commits: int = 250) -> Dict[str, Any]:
    file_set = set(files)
    raw = _git_out(repo_root, ["log", f"--max-count={max_commits}", "--numstat", "--format=commit%x09%H%x09%an%x09%ae%x09%ct"], default="")
    by_file: Dict[str, Dict[str, Any]] = {}
    current: Optional[Dict[str, Any]] = None
    for line in raw.splitlines():
        if line.startswith("commit\t"):
            parts = line.split("\t")
            current = {
                "hash": parts[1] if len(parts) > 1 else "",
                "author": parts[2] if len(parts) > 2 else "",
                "email": parts[3] if len(parts) > 3 else "",
                "timestamp": int(parts[4]) if len(parts) > 4 and str(parts[4]).isdigit() else 0,
            }
            continue
        if not current or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, raw_path = parts[0], parts[1], parts[2]
        path = _normalize_git_numstat_path(raw_path)
        if path not in file_set:
            continue
        bucket = by_file.setdefault(
            path,
            {
                "path": path,
                "change_count": 0,
                "added_lines": 0,
                "deleted_lines": 0,
                "authors": defaultdict(int),
                "last_commit": None,
            },
        )
        bucket["change_count"] = int(bucket.get("change_count") or 0) + 1
        bucket["added_lines"] = int(bucket.get("added_lines") or 0) + _int_stat(added_raw)
        bucket["deleted_lines"] = int(bucket.get("deleted_lines") or 0) + _int_stat(deleted_raw)
        if current.get("author"):
            bucket["authors"][str(current.get("author"))] += 1
        if not bucket.get("last_commit"):
            bucket["last_commit"] = {
                "hash": current.get("hash"),
                "author": current.get("author"),
                "email": current.get("email"),
                "timestamp": current.get("timestamp"),
            }
    compact = {}
    for path, bucket in list(by_file.items())[:MAX_OWNERSHIP_FILES]:
        authors = bucket.get("authors") or {}
        compact[path] = {
            "path": path,
            "change_count": int(bucket.get("change_count") or 0),
            "added_lines": int(bucket.get("added_lines") or 0),
            "deleted_lines": int(bucket.get("deleted_lines") or 0),
            "churn_score": int(bucket.get("added_lines") or 0) + int(bucket.get("deleted_lines") or 0),
            "authors": [
                {"name": name, "commit_count": count}
                for name, count in sorted(authors.items(), key=lambda item: (-item[1], item[0]))[:8]
            ],
            "last_commit": bucket.get("last_commit") or {},
        }
    return {
        "schema_version": "dhee.git_ownership_index.v1",
        "by_file": compact,
        "summary": {
            "file_count": len(compact),
            "max_commits_scanned": max_commits,
            "git_log_available": bool(raw),
        },
    }


def _normalize_git_numstat_path(path: str) -> str:
    value = str(path or "").replace("\\", "/")
    if " => " in value:
        value = value.split(" => ", 1)[1]
    value = value.strip("{}")
    return value


def _int_stat(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _lsp_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def _lsp_relative_path_from_uri(repo_root: Path, uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("file://"):
        parsed = urlparse(uri)
        path = Path(unquote(parsed.path))
    else:
        path = Path(uri)
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def _lsp_language_id(language: str, path: str) -> str:
    suffix = Path(path).suffix.lower()
    if language == "typescript" and suffix == ".tsx":
        return "typescriptreact"
    if language == "javascript" and suffix == ".jsx":
        return "javascriptreact"
    return language


def _lsp_send(process: subprocess.Popen[bytes], message: Dict[str, Any]) -> bool:
    if process.stdin is None:
        return False
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    frame = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
    try:
        process.stdin.write(frame)
        process.stdin.flush()
        return True
    except (BrokenPipeError, OSError):
        return False


def _lsp_read_loop(
    stream: Any,
    out: "queue.Queue[Dict[str, Any]]",
    stop: threading.Event,
) -> None:
    while not stop.is_set():
        headers: List[str] = []
        while not stop.is_set():
            line = stream.readline()
            if not line:
                return
            if line in {b"\r\n", b"\n"}:
                break
            headers.append(line.decode("ascii", errors="replace").strip())
        length = 0
        for header in headers:
            if header.lower().startswith("content-length:"):
                try:
                    length = int(header.split(":", 1)[1].strip())
                except ValueError:
                    length = 0
                break
        if length <= 0:
            continue
        body = stream.read(length)
        if not body:
            return
        try:
            decoded = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            decoded = {"jsonrpc": "2.0", "method": "$/decodeError", "params": {"error": str(exc)}}
        out.put(decoded)


def _lsp_stderr_loop(stream: Any, out: "queue.Queue[str]", stop: threading.Event) -> None:
    while not stop.is_set():
        line = stream.readline()
        if not line:
            return
        try:
            out.put(line.decode("utf-8", errors="replace").strip())
        except AttributeError:
            out.put(str(line).strip())


def _compact_lsp_diagnostic(repo_root: Path, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    uri = str(params.get("uri") or "")
    path = _lsp_relative_path_from_uri(repo_root, uri)
    compact = []
    for item in params.get("diagnostics") or []:
        if not isinstance(item, dict):
            continue
        start = ((item.get("range") or {}).get("start") or {})
        compact.append(
            {
                "path": path,
                "line": int(start.get("line") or 0) + 1,
                "character": int(start.get("character") or 0),
                "severity": item.get("severity"),
                "source": item.get("source"),
                "code": item.get("code"),
                "message": str(item.get("message") or "")[:240],
            }
        )
    return compact


def _capture_lsp_notification(repo_root: Path, message: Dict[str, Any], diagnostics: List[Dict[str, Any]]) -> None:
    method = str(message.get("method") or "")
    if method == "textDocument/publishDiagnostics":
        diagnostics.extend(_compact_lsp_diagnostic(repo_root, message.get("params") or {}))


def _respond_to_lsp_server_request(process: subprocess.Popen[bytes], repo_root: Path, message: Dict[str, Any]) -> None:
    if "id" not in message or not message.get("method"):
        return
    method = str(message.get("method") or "")
    params = message.get("params") or {}
    if method == "workspace/configuration":
        items = params.get("items") or []
        result: Any = [{} for _ in items]
    elif method == "workspace/workspaceFolders":
        result = [{"uri": _lsp_file_uri(repo_root), "name": repo_root.name}]
    else:
        result = None
    _lsp_send(process, {"jsonrpc": "2.0", "id": message.get("id"), "result": result})


def _lsp_wait_for_response(
    process: subprocess.Popen[bytes],
    messages: "queue.Queue[Dict[str, Any]]",
    request_id: int,
    deadline: float,
    repo_root: Path,
    diagnostics: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    while time.monotonic() < deadline:
        timeout = max(0.01, min(0.05, deadline - time.monotonic()))
        try:
            message = messages.get(timeout=timeout)
        except queue.Empty:
            continue
        _capture_lsp_notification(repo_root, message, diagnostics)
        if message.get("id") == request_id:
            return message
        if message.get("method") and "id" in message:
            _respond_to_lsp_server_request(process, repo_root, message)
    return None


def _drain_lsp_notifications(
    process: Optional[subprocess.Popen[bytes]],
    messages: "queue.Queue[Dict[str, Any]]",
    repo_root: Path,
    diagnostics: List[Dict[str, Any]],
    deadline: float,
) -> None:
    while time.monotonic() < deadline:
        try:
            message = messages.get_nowait()
        except queue.Empty:
            return
        _capture_lsp_notification(repo_root, message, diagnostics)
        if process is not None and message.get("method") and "id" in message:
            _respond_to_lsp_server_request(process, repo_root, message)


def _lsp_range_start_line(item: Dict[str, Any]) -> int:
    range_data = item.get("range") or ((item.get("location") or {}).get("range") or {})
    start = range_data.get("start") or {}
    return int(start.get("line") or 0) + 1


def _flatten_lsp_document_symbols(
    items: Any,
    path: str,
    *,
    container: str = "",
    limit: int = 80,
) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []
    flattened: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or len(flattened) >= limit:
            continue
        name = str(item.get("name") or "")
        if name:
            symbol_path = path
            location = item.get("location") or {}
            if isinstance(location, dict) and location.get("uri"):
                symbol_path = str(location.get("uri") or path)
            flattened.append(
                {
                    "path": symbol_path,
                    "name": name,
                    "container": container or item.get("containerName") or "",
                    "kind": item.get("kind"),
                    "line": _lsp_range_start_line(item),
                    "detail": str(item.get("detail") or "")[:160],
                }
            )
        children = _flatten_lsp_document_symbols(
            item.get("children"),
            path,
            container=name or container,
            limit=max(0, limit - len(flattened)),
        )
        flattened.extend(children[: max(0, limit - len(flattened))])
    return flattened[:limit]


def _compact_lsp_references(repo_root: Path, result: Any) -> List[Dict[str, Any]]:
    if not isinstance(result, list):
        return []
    refs: List[Dict[str, Any]] = []
    for item in result[:80]:
        if not isinstance(item, dict):
            continue
        range_data = item.get("range") or {}
        start = range_data.get("start") or {}
        refs.append(
            {
                "path": _lsp_relative_path_from_uri(repo_root, str(item.get("uri") or "")),
                "line": int(start.get("line") or 0) + 1,
                "character": int(start.get("character") or 0),
            }
        )
    return refs


def _execute_live_lsp(
    repo_root: Path,
    language: str,
    server_name: str,
    source_paths: Sequence[str],
    symbols: Sequence[Dict[str, Any]],
    *,
    timeout_seconds: float = LIVE_LSP_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    command = LSP_EXECUTABLE_COMMANDS.get(server_name)
    if not command:
        return {
            "enabled": False,
            "attempted": False,
            "ok": False,
            "server": server_name,
            "reason": "detected server has no stdio JSON-RPC execution driver",
        }

    started_at = time.monotonic()
    deadline = started_at + max(0.5, timeout_seconds)
    diagnostics: List[Dict[str, Any]] = []
    document_symbols: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []
    errors: List[str] = []
    messages: "queue.Queue[Dict[str, Any]]" = queue.Queue()
    stderr_lines: "queue.Queue[str]" = queue.Queue()
    stop = threading.Event()
    process: Optional[subprocess.Popen[bytes]] = None

    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(repo_root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdout is None:
            raise RuntimeError("language server stdout unavailable")
        reader = threading.Thread(target=_lsp_read_loop, args=(process.stdout, messages, stop), daemon=True)
        reader.start()
        if process.stderr is not None:
            threading.Thread(target=_lsp_stderr_loop, args=(process.stderr, stderr_lines, stop), daemon=True).start()

        request_id = 1
        root_uri = _lsp_file_uri(repo_root)
        if not _lsp_send(
            process,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "processId": os.getpid(),
                    "clientInfo": {"name": "dhee-repo-brain"},
                    "rootUri": root_uri,
                    "workspaceFolders": [{"uri": root_uri, "name": repo_root.name}],
                    "capabilities": {
                        "textDocument": {
                            "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                            "publishDiagnostics": {"relatedInformation": True},
                            "references": {"dynamicRegistration": False},
                        },
                        "workspace": {"configuration": False},
                    },
                },
            },
        ):
            raise RuntimeError("failed to send initialize request")
        init_response = _lsp_wait_for_response(process, messages, request_id, deadline, repo_root, diagnostics)
        if not init_response:
            errors.append("initialize timed out")
            return {
                "enabled": True,
                "attempted": True,
                "ok": False,
                "server": server_name,
                "mode": "live_lsp",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "diagnostics": diagnostics[:80],
                "document_symbols": [],
                "references": [],
                "errors": errors,
            }
        if init_response.get("error"):
            errors.append(str(init_response.get("error"))[:300])
            return {
                "enabled": True,
                "attempted": True,
                "ok": False,
                "server": server_name,
                "mode": "live_lsp",
                "duration_ms": int((time.monotonic() - started_at) * 1000),
                "diagnostics": diagnostics[:80],
                "document_symbols": [],
                "references": [],
                "errors": errors,
            }
        _lsp_send(process, {"jsonrpc": "2.0", "method": "initialized", "params": {}})

        opened_paths: List[str] = []
        for rel_path in source_paths[:LIVE_LSP_MAX_FILES]:
            if time.monotonic() >= deadline:
                errors.append("live LSP file budget timed out")
                break
            path = repo_root / rel_path
            try:
                if path.stat().st_size > MAX_INDEX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append(f"open failed for {rel_path}: {exc}")
                continue
            uri = _lsp_file_uri(path)
            opened_paths.append(rel_path)
            _lsp_send(
                process,
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": _lsp_language_id(language, rel_path),
                            "version": 1,
                            "text": text,
                        }
                    },
                },
            )
            request_id += 1
            _lsp_send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "textDocument/documentSymbol",
                    "params": {"textDocument": {"uri": uri}},
                },
            )
            response = _lsp_wait_for_response(process, messages, request_id, deadline, repo_root, diagnostics)
            if response and not response.get("error"):
                symbols_for_file = _flatten_lsp_document_symbols(response.get("result"), rel_path)
                for symbol in symbols_for_file:
                    if str(symbol.get("path") or "").startswith("file://"):
                        symbol["path"] = _lsp_relative_path_from_uri(repo_root, str(symbol["path"]))
                document_symbols.extend(symbols_for_file)
            elif response and response.get("error"):
                errors.append(f"documentSymbol failed for {rel_path}: {str(response.get('error'))[:220]}")

        opened_set = set(opened_paths)
        reference_targets = [
            symbol
            for symbol in symbols
            if str(symbol.get("language") or "") == language
            and str(symbol.get("path") or "") in opened_set
            and symbol.get("start_line")
        ][:LIVE_LSP_MAX_SYMBOLS]
        for symbol in reference_targets:
            if time.monotonic() >= deadline:
                errors.append("live LSP reference budget timed out")
                break
            rel_path = str(symbol.get("path") or "")
            uri = _lsp_file_uri(repo_root / rel_path)
            request_id += 1
            _lsp_send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "textDocument/references",
                    "params": {
                        "textDocument": {"uri": uri},
                        "position": {
                            "line": max(0, int(symbol.get("start_line") or 1) - 1),
                            "character": max(0, int(symbol.get("start_col") or symbol.get("col_offset") or 0)),
                        },
                        "context": {"includeDeclaration": True},
                    },
                },
            )
            response = _lsp_wait_for_response(process, messages, request_id, deadline, repo_root, diagnostics)
            if response and not response.get("error"):
                refs = _compact_lsp_references(repo_root, response.get("result"))
                if refs:
                    references.append(
                        {
                            "path": rel_path,
                            "symbol": symbol.get("qualname") or symbol.get("name") or "",
                            "line": symbol.get("start_line"),
                            "reference_count": len(refs),
                            "locations": refs[:20],
                        }
                    )
            elif response and response.get("error"):
                errors.append(f"references failed for {rel_path}: {str(response.get('error'))[:220]}")

        _drain_lsp_notifications(process, messages, repo_root, diagnostics, min(deadline, time.monotonic() + 0.1))
        request_id += 1
        _lsp_send(process, {"jsonrpc": "2.0", "id": request_id, "method": "shutdown", "params": None})
        _lsp_wait_for_response(process, messages, request_id, min(deadline, time.monotonic() + 0.2), repo_root, diagnostics)
        _lsp_send(process, {"jsonrpc": "2.0", "method": "exit", "params": None})
    except Exception as exc:  # pragma: no cover - defensive against external LSP failures
        errors.append(str(exc)[:300])
    finally:
        stop.set()
        if process is not None:
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.kill()
        while not stderr_lines.empty() and len(errors) < 6:
            line = stderr_lines.get_nowait()
            if line:
                errors.append(f"stderr: {line[:220]}")

    ok = bool(document_symbols or references or diagnostics) and not any("initialize timed out" in err for err in errors)
    return {
        "enabled": True,
        "attempted": True,
        "ok": ok,
        "server": server_name,
        "mode": "live_lsp",
        "duration_ms": int((time.monotonic() - started_at) * 1000),
        "file_budget": LIVE_LSP_MAX_FILES,
        "symbol_budget": LIVE_LSP_MAX_SYMBOLS,
        "document_symbol_count": len(document_symbols),
        "reference_count": sum(int(item.get("reference_count") or 0) for item in references),
        "diagnostic_count": len(diagnostics),
        "document_symbols": document_symbols[:160],
        "references": references[:80],
        "diagnostics": diagnostics[:80],
        "errors": errors[:8],
    }


def _lsp_workspace_index(repo_root: Path, files: Sequence[str], symbols: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    languages = sorted({lang for path in files if (lang := _language_for_path(path))})
    configs = _language_project_configs(repo_root, files)
    language_cards: Dict[str, Dict[str, Any]] = {}
    for language in languages:
        server_names = LSP_SERVER_CANDIDATES.get(language) or ()
        servers = []
        for name in server_names:
            path = shutil.which(name)
            if path:
                servers.append({"name": name, "path": path, "stdio_driver": name in LSP_EXECUTABLE_COMMANDS})
        source_paths = [path for path in files if _language_for_path(path) == language]
        document_symbol_requests = [
            {
                "method": "textDocument/documentSymbol",
                "path": path,
            }
            for path in source_paths[:80]
        ]
        reference_requests = [
            {
                "method": "textDocument/references",
                "path": str(symbol.get("path") or ""),
                "symbol": str(symbol.get("qualname") or symbol.get("name") or ""),
                "line": int(symbol.get("start_line") or 0),
            }
            for symbol in symbols
            if str(symbol.get("language") or "") == language and symbol.get("path") and symbol.get("start_line")
        ][:80]
        live = {
            "enabled": False,
            "attempted": False,
            "ok": False,
            "reason": "no supported LSP server executable detected",
        }
        executable_server = next((server for server in servers if server.get("stdio_driver")), None)
        if executable_server:
            live = _execute_live_lsp(
                repo_root,
                language,
                str(executable_server.get("name") or ""),
                source_paths,
                symbols,
            )
        elif servers:
            live = {
                "enabled": False,
                "attempted": False,
                "ok": False,
                "server": servers[0].get("name"),
                "reason": "detected server is not supported by the live stdio executor yet",
            }
        language_cards[language] = {
            "servers": servers,
            "available": bool(servers),
            "config_files": configs.get(language) or [],
            "source_file_count": len(source_paths),
            "request_plan": document_symbol_requests + reference_requests,
            "live": live,
        }
    live_cards = [card.get("live") or {} for card in language_cards.values()]
    summary = {
        "language_count": len(language_cards),
        "detected_server_count": sum(len(card.get("servers") or []) for card in language_cards.values()),
        "request_count": sum(len(card.get("request_plan") or []) for card in language_cards.values()),
        "live_execution_count": sum(1 for card in live_cards if card.get("attempted")),
        "live_success_count": sum(1 for card in live_cards if card.get("ok")),
        "diagnostic_count": sum(int(card.get("diagnostic_count") or 0) for card in live_cards),
        "document_symbol_count": sum(int(card.get("document_symbol_count") or 0) for card in live_cards),
        "reference_count": sum(int(card.get("reference_count") or 0) for card in live_cards),
    }
    has_server = any(card.get("available") for card in language_cards.values())
    has_live = summary["live_execution_count"] > 0
    has_live_success = summary["live_success_count"] > 0
    return {
        "schema_version": "dhee.lsp_workspace_index.v1",
        "mode": "live_enriched" if has_live_success else ("live_attempted" if has_live else ("server_detected" if has_server else "request_plan_only")),
        "summary": summary,
        "languages": language_cards,
        "diagnostics": [] if has_server else [
            "no supported LSP server executable detected; request plan is persisted for a runtime that has pyright/pylsp/ruff/tsserver"
        ],
    }


def _language_project_configs(repo_root: Path, files: Sequence[str]) -> Dict[str, List[str]]:
    file_set = set(files)
    out: Dict[str, List[str]] = {}
    for language, names in LANGUAGE_CONFIG_FILES.items():
        matched = []
        for name in names:
            if name in file_set or (repo_root / name).exists():
                matched.append(name)
        if matched:
            out[language] = matched
    return out


def _engine_card(syntax_index: Optional[Dict[str, Any]] = None, lsp_index: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    lsp_languages = (lsp_index or {}).get("languages") or {}
    lsp_servers = {
        language: [server.get("name") for server in (card.get("servers") or [])]
        for language, card in lsp_languages.items()
    }
    tree_sitter_languages = sorted(
        key
        for key, status in ((syntax_index or {}).get("languages") or {}).items()
        if isinstance(status, dict) and status.get("available")
    )
    tree_sitter_available = bool(tree_sitter_languages) or importlib.util.find_spec("tree_sitter") is not None
    return {
        "indexer": "swe_repo_brain.v3",
        "languages": ["python", "javascript", "typescript"],
        "parsers": {
            "python": "stdlib_ast + tree_sitter",
            "javascript": "static_regex + tree_sitter",
            "typescript": "static_regex + tree_sitter",
        },
        "lsp": {
            "available": any(lsp_servers.values()),
            "detected_servers": lsp_servers,
            "mode": (lsp_index or {}).get("mode") or "request_plan_only",
            "summary": (lsp_index or {}).get("summary") or {},
            "reason": "LSP request plans are persisted; live diagnostics/symbols/references are added when a supported stdio language server is installed.",
        },
        "tree_sitter": {
            "available": tree_sitter_available,
            "active_languages": tree_sitter_languages,
            "mode": "syntax_span_backend" if tree_sitter_languages else "not_installed",
            "reason": "Tree-sitter spans enrich symbols and localization when grammar packages are installed.",
        },
    }


def _load_latest_brain_for_incremental(repo_root: Path) -> Optional[Dict[str, Any]]:
    root = repo_brain_root(repo_root)
    latest = read_json_checked(root / "latest.json", expected_schema=REPO_BRAIN_POINTER_SCHEMA)
    if not latest.get("ok"):
        return None
    target = repo_root / str((latest.get("data") or {}).get("path") or "")
    checked = read_json_checked(target, expected_schema=REPO_INTELLIGENCE_SCHEMA)
    brain = checked.get("data") if checked.get("ok") else None
    return brain if isinstance(brain, dict) else None


def _incremental_index_report(manifest: Sequence[Dict[str, Any]], previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    current = {str(item.get("path")): str(item.get("sha256") or "") for item in manifest}
    previous_manifest = {
        str(item.get("path")): str(item.get("sha256") or "")
        for item in ((previous or {}).get("file_manifest") or [])
    }
    changed = [
        path for path, digest in current.items()
        if path not in previous_manifest or previous_manifest.get(path) != digest
    ]
    deleted = [path for path in previous_manifest if path not in current]
    unchanged = [path for path, digest in current.items() if previous_manifest.get(path) == digest]
    return {
        "schema_version": "dhee.repo_incremental_index.v1",
        "previous_ref": ((previous or {}).get("storage") or {}).get("ref", ""),
        "mode": "full_build_with_incremental_delta",
        "changed_files": sorted(changed),
        "deleted_files": sorted(deleted),
        "unchanged_files": sorted(unchanged)[:MAX_INCREMENTAL_FILE_LIST],
        "unchanged_file_count": len(unchanged),
        "changed_file_count": len(changed),
        "deleted_file_count": len(deleted),
    }


def repo_brain_root(repo_root: Path) -> Path:
    return repo_link.repo_context_dir(repo_root) / "repo_brain"


def _brain_paths(repo_root: Path, state: Dict[str, Any], goal: str) -> Tuple[Path, Path, str]:
    head = str(state.get("head_short") or state.get("head_commit") or "no_head")
    folder = repo_brain_root(repo_root) / head
    name = f"brain_{_stable_hash({'goal': goal, 'head': state.get('head_commit')}, 12)}.json"
    return folder / name, repo_brain_root(repo_root) / "latest.json", f"repo_brain:{head}/{name}"


def build_repo_brain(
    repo: str | os.PathLike[str] | None = None,
    *,
    goal: str = "",
    relevant_files: Optional[Sequence[str]] = None,
    must_run: Optional[Sequence[str]] = None,
    file_limit: int = 4_000,
    persist: bool = True,
) -> Dict[str, Any]:
    """Build and optionally persist a git-SHA scoped SWE repo brain."""

    repo_root = resolve_repo_root(repo)
    repo_link._ensure_repo_skeleton(repo_root)
    state = branch_state(repo_root)
    files = iter_repo_files(repo_root, limit=file_limit)
    manifest = _file_manifest(repo_root, files)
    previous = _load_latest_brain_for_incremental(repo_root)
    incremental = _incremental_index_report(manifest, previous)
    symbols, imports, call_sites, source_reuse = _index_sources(repo_root, files, previous, incremental)
    syntax_index, syntax_reuse = _syntax_index(repo_root, files, previous, incremental)
    symbols = _merge_syntax_symbols(symbols, syntax_index)
    call_sites = _merge_call_sites(call_sites, syntax_index)
    call_graph = _resolve_call_edges(call_sites, symbols)
    lsp_index = _lsp_workspace_index(repo_root, files, symbols)
    incremental["source_index_reuse"] = source_reuse
    incremental["syntax_index_reuse"] = syntax_reuse
    tests = _test_map(files, symbols, imports, list(must_run or []))
    dependency_graph = _dependency_graph(imports)
    coverage = _coverage_map(repo_root, files)
    flaky_tests = _flaky_test_signals(repo_root)
    failure_index = _failure_index(repo_root, files, goal)
    ownership = _git_ownership_index(repo_root, files)
    test_ownership = _test_ownership_index(files, imports, tests, coverage, failure_index, flaky_tests)
    path, latest_path, ref = _brain_paths(repo_root, state, goal)
    lsp_summary = lsp_index.get("summary") or {}
    brain: Dict[str, Any] = {
        "schema_version": REPO_INTELLIGENCE_SCHEMA,
        "kind": "swe_repo_brain",
        "repo": repo_slug(repo_root),
        "generated_at": _now_iso(),
        "head_commit": state.get("head_commit"),
        "head_short": state.get("head_short"),
        "branch": state.get("branch"),
        "dirty": bool(state.get("dirty")),
        "dirty_paths": list(state.get("changed_paths") or []),
        "engine": _engine_card(syntax_index, lsp_index),
        "incremental_index": incremental,
        "goal_hash": _stable_hash(goal or "", 12),
        "file_manifest": manifest,
        "symbols": symbols,
        "imports": imports,
        "call_sites": call_sites,
        "call_graph": call_graph,
        "syntax_index": syntax_index,
        "lsp_index": lsp_index,
        "test_map": tests,
        "coverage_map": coverage,
        "test_ownership": test_ownership,
        "dependency_graph": dependency_graph,
        "setup_commands": _setup_commands(files),
        "flaky_tests": flaky_tests,
        "failure_index": failure_index,
        "git_ownership": ownership,
        "risky_files": _risky_files(files),
        "historical_failure_signatures": _historical_failure_signatures(repo_root, goal),
        "focus": {
            "goal_tokens": _tokens(goal),
            "relevant_files": list(relevant_files or []),
            "must_run": list(must_run or []),
        },
        "metrics": {
            "file_count": len(files),
            "indexed_file_count": sum(1 for item in manifest if item.get("indexed")),
            "symbol_count": len(symbols),
            "import_file_count": len(imports),
            "call_site_count": len(call_sites),
            "call_edge_count": len(call_graph),
            "syntax_span_count": len(syntax_index.get("spans") or []),
            "tree_sitter_call_site_count": len(syntax_index.get("call_sites") or []),
            "tree_sitter_file_count": int((syntax_index.get("summary") or {}).get("parsed_file_count") or 0),
            "lsp_request_count": sum(len((card.get("request_plan") or [])) for card in (lsp_index.get("languages") or {}).values()),
            "lsp_live_execution_count": int(lsp_summary.get("live_execution_count") or 0),
            "lsp_live_success_count": int(lsp_summary.get("live_success_count") or 0),
            "lsp_live_diagnostic_count": int(lsp_summary.get("diagnostic_count") or 0),
            "lsp_live_document_symbol_count": int(lsp_summary.get("document_symbol_count") or 0),
            "lsp_live_reference_count": int(lsp_summary.get("reference_count") or 0),
            "test_file_count": len(tests.get("test_files") or []),
            "coverage_file_count": len(coverage.get("files") or []),
            "test_ownership_edge_count": int((test_ownership.get("summary") or {}).get("edge_count") or 0),
            "failure_record_count": int((failure_index.get("summary") or {}).get("record_count") or 0),
            "failure_file_count": int((failure_index.get("summary") or {}).get("file_count") or 0),
            "ownership_file_count": int((ownership.get("summary") or {}).get("file_count") or 0),
            "local_import_edge_count": len(dependency_graph.get("local_import_edges") or []),
            "external_import_count": len(dependency_graph.get("external_imports") or []),
            "flaky_test_count": len(flaky_tests),
            "historical_failure_count": 0,
        },
    }
    brain["metrics"]["historical_failure_count"] = len(brain["historical_failure_signatures"])
    if persist:
        brain["storage"] = {
            "ref": ref,
            "path": os.path.relpath(path, repo_root).replace(os.sep, "/"),
            "latest_path": os.path.relpath(latest_path, repo_root).replace(os.sep, "/"),
        }
        repo_graph = repo_graph_from_brain(brain)
        graph_path = path.with_name(path.stem + ".repo_graph.json")
        graph_write = write_json_atomic(graph_path, repo_graph)
        brain["repo_graph"] = _repo_graph_summary(repo_graph)
        brain["metrics"]["repo_graph_node_count"] = brain["repo_graph"]["node_count"]
        brain["metrics"]["repo_graph_edge_count"] = brain["repo_graph"]["edge_count"]
        brain["storage"]["repo_graph_path"] = os.path.relpath(graph_path, repo_root).replace(os.sep, "/")
        brain["storage"]["repo_graph_write"] = graph_write
        write_result = write_json_atomic(path, brain)
        pointer = {
            "schema_version": REPO_BRAIN_POINTER_SCHEMA,
            "repo": brain["repo"],
            "ref": ref,
            "path": brain["storage"]["path"],
            "repo_graph_path": brain["storage"]["repo_graph_path"],
            "head_commit": brain.get("head_commit"),
            "head_short": brain.get("head_short"),
            "goal_hash": brain.get("goal_hash"),
            "generated_at": brain.get("generated_at"),
            "metrics": brain.get("metrics"),
        }
        latest_result = write_json_atomic(latest_path, pointer)
        brain["storage"]["write"] = write_result
        brain["storage"]["latest_write"] = latest_result
    else:
        brain["storage"] = {"ref": ref, "path": os.path.relpath(path, repo_root).replace(os.sep, "/")}
        repo_graph = repo_graph_from_brain(brain)
        brain["repo_graph"] = _repo_graph_summary(repo_graph)
        brain["metrics"]["repo_graph_node_count"] = brain["repo_graph"]["node_count"]
        brain["metrics"]["repo_graph_edge_count"] = brain["repo_graph"]["edge_count"]
    return brain


def repo_brain_summary(brain: Dict[str, Any]) -> Dict[str, Any]:
    metrics = brain.get("metrics") or {}
    storage = brain.get("storage") or {}
    return {
        "schema_version": REPO_INTELLIGENCE_SCHEMA,
        "ref": storage.get("ref") or "",
        "path": storage.get("path") or "",
        "head_commit": brain.get("head_short") or brain.get("head_commit"),
        "full_head_commit": brain.get("head_commit"),
        "branch": brain.get("branch"),
        "dirty": bool(brain.get("dirty")),
        "engine": brain.get("engine") or {},
        "file_count": metrics.get("file_count", 0),
        "indexed_file_count": metrics.get("indexed_file_count", 0),
        "symbol_count": metrics.get("symbol_count", 0),
        "import_file_count": metrics.get("import_file_count", 0),
        "call_site_count": metrics.get("call_site_count", 0),
        "call_edge_count": metrics.get("call_edge_count", 0),
        "syntax_span_count": metrics.get("syntax_span_count", 0),
        "tree_sitter_call_site_count": metrics.get("tree_sitter_call_site_count", 0),
        "tree_sitter_file_count": metrics.get("tree_sitter_file_count", 0),
        "lsp_request_count": metrics.get("lsp_request_count", 0),
        "lsp_live_execution_count": metrics.get("lsp_live_execution_count", 0),
        "lsp_live_success_count": metrics.get("lsp_live_success_count", 0),
        "lsp_live_diagnostic_count": metrics.get("lsp_live_diagnostic_count", 0),
        "lsp_live_document_symbol_count": metrics.get("lsp_live_document_symbol_count", 0),
        "lsp_live_reference_count": metrics.get("lsp_live_reference_count", 0),
        "repo_graph_node_count": metrics.get("repo_graph_node_count", 0),
        "repo_graph_edge_count": metrics.get("repo_graph_edge_count", 0),
        "test_count": metrics.get("test_file_count", 0),
        "coverage_file_count": metrics.get("coverage_file_count", 0),
        "test_ownership_edge_count": metrics.get("test_ownership_edge_count", 0),
        "flaky_test_count": metrics.get("flaky_test_count", 0),
        "failure_record_count": metrics.get("failure_record_count", 0),
        "failure_file_count": metrics.get("failure_file_count", 0),
        "ownership_file_count": metrics.get("ownership_file_count", 0),
        "local_import_edge_count": metrics.get("local_import_edge_count", 0),
        "external_import_count": metrics.get("external_import_count", 0),
        "risky_file_count": len(brain.get("risky_files") or []),
        "historical_failure_count": metrics.get("historical_failure_count", 0),
    }


def load_repo_brain(
    repo: str | os.PathLike[str] | None = None,
    *,
    ref: str | None = None,
    quarantine: bool = False,
) -> Dict[str, Any]:
    repo_root = resolve_repo_root(repo)
    root = repo_brain_root(repo_root)
    target: Path
    if ref and ref.startswith("repo_brain:"):
        target = root / ref.split(":", 1)[1]
    elif ref:
        target = root / ref
    else:
        latest = read_json_checked(root / "latest.json", expected_schema=REPO_BRAIN_POINTER_SCHEMA, quarantine=quarantine)
        if not latest.get("ok"):
            return {
                "format": "dhee_repo_brain_get.v1",
                "ok": False,
                "diagnostics": latest.get("diagnostics") or [],
                "brain": None,
            }
        target = repo_root / str((latest.get("data") or {}).get("path") or "")
    checked = read_json_checked(target, expected_schema=REPO_INTELLIGENCE_SCHEMA, quarantine=quarantine)
    return {
        "format": "dhee_repo_brain_get.v1",
        "ok": bool(checked.get("ok")),
        "path": str(target),
        "diagnostics": checked.get("diagnostics") or [],
        "brain": checked.get("data"),
    }


def localize_issue(goal: str, brain: Dict[str, Any], *, limit: int = 12) -> Dict[str, Any]:
    tokens = _tokens(goal)
    file_scores: DefaultDict[str, Dict[str, Any]] = defaultdict(lambda: {"score": 0.0, "reasons": [], "evidence": []})
    symbol_scores: List[Dict[str, Any]] = []
    dirty_paths = set(brain.get("dirty_paths") or [])
    for item in brain.get("file_manifest") or []:
        path = str(item.get("path") or "")
        score, reasons = _score_path(path, tokens)
        if path in dirty_paths:
            score += 6.0
            reasons.append("file is already changed in current branch state")
        if score > 0:
            _add_file_score(file_scores, path, score, reasons, f"repo_file:{path}")
    symbols_by_path: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for symbol in brain.get("symbols") or []:
        path = str(symbol.get("path") or "")
        haystack = " ".join(
            [
                str(symbol.get("name") or ""),
                str(symbol.get("qualname") or ""),
                str(symbol.get("signature") or ""),
                str(symbol.get("doc") or ""),
            ]
        ).lower()
        score, reasons = _score_text(haystack, tokens, path_weight=False)
        if score > 0:
            symbol_score = {
                "path": path,
                "symbol_id": symbol.get("id"),
                "name": symbol.get("name"),
                "qualname": symbol.get("qualname"),
                "kind": symbol.get("kind"),
                "line": symbol.get("start_line"),
                "score": round(score, 3),
                "confidence": round(min(0.95, 0.25 + score / 14.0), 3),
                "reasons": reasons,
                "evidence_pointer": f"symbol:{symbol.get('id')}",
            }
            symbol_scores.append(symbol_score)
            symbols_by_path[path].append(symbol_score)
            _add_file_score(file_scores, path, score * 0.8, ["matched symbol: " + str(symbol.get("qualname") or symbol.get("name"))], f"symbol:{symbol.get('id')}")
    for failure in brain.get("historical_failure_signatures") or []:
        text = json.dumps(failure, sort_keys=True, default=str).lower()
        if tokens and not any(token in text for token in tokens):
            continue
        for path in _paths_in_text(text, brain):
            _add_file_score(file_scores, path, 5.0, ["historical failure signature mentions this path"], str(failure.get("ref") or "failure_signature"))
    failure_by_file = ((brain.get("failure_index") or {}).get("by_file") or {})
    for path, bucket in failure_by_file.items():
        text = json.dumps(bucket, sort_keys=True, default=str).lower()
        path_score, path_reasons = _score_path(path, tokens)
        goal_match_count = int(bucket.get("goal_match_count") or 0) if isinstance(bucket, dict) else 0
        if tokens and not (goal_match_count > 0 or path_score > 0 or any(token in text for token in tokens)):
            continue
        failure_count = int(bucket.get("failure_count") or 0) if isinstance(bucket, dict) else 0
        score = 7.0 + min(8.0, failure_count * 2.0) + min(4.0, goal_match_count * 2.0)
        reasons = ["recent failure evidence references this path"]
        reasons.extend(path_reasons[:3])
        if bucket.get("lines"):
            reasons.append("failure includes line evidence: " + ", ".join(str(line) for line in list(bucket.get("lines") or [])[:5]))
        _add_file_score(file_scores, str(path), score, reasons, "failure_index:" + str(path))
    coverage_files = ((brain.get("coverage_map") or {}).get("files") or {})
    for path, item in coverage_files.items():
        if path in file_scores and item.get("uncovered_lines"):
            _add_file_score(file_scores, str(path), 0.75, ["coverage map has uncovered lines near this source"], "coverage_map:" + str(path))
    ownership_by_file = ((brain.get("git_ownership") or {}).get("by_file") or {})
    for path, item in ownership_by_file.items():
        if path in file_scores:
            churn = int((item or {}).get("churn_score") or 0)
            if churn:
                _add_file_score(file_scores, str(path), 0.25, [f"git ownership/churn evidence available ({churn} changed lines scanned)"], "git_ownership:" + str(path))
    test_map = brain.get("test_map") or {}
    test_ownership = brain.get("test_ownership") or {}
    ranked_files = []
    for path, data in file_scores.items():
        score = float(data.get("score") or 0.0)
        if score <= 0:
            continue
        ranked_files.append(
            {
                "path": path,
                "kind": _file_kind(path),
                "score": round(score, 3),
                "confidence": round(min(0.96, 0.22 + score / 20.0), 3),
                "reasons": list(dict.fromkeys(data.get("reasons") or []))[:8],
                "evidence_pointers": list(dict.fromkeys(data.get("evidence") or []))[:8],
                "symbols": sorted(symbols_by_path.get(path, []), key=lambda item: (-item["score"], str(item.get("qualname"))))[:6],
                "failure_evidence": _localized_failure_evidence(path, brain),
                "coverage": _localized_coverage(path, brain),
                "ownership": _localized_ownership(path, brain),
                "test_ownership": _localized_test_ownership(path, brain),
            }
        )
    ranked_files.sort(key=lambda item: (-float(item.get("score") or 0), item.get("path") or ""))
    symbol_scores.sort(key=lambda item: (-float(item.get("score") or 0), str(item.get("qualname") or "")))
    test_candidates = _localized_tests(ranked_files, test_map, test_ownership)
    confidence = float(ranked_files[0]["confidence"]) if ranked_files else 0.0
    return {
        "schema_version": LOCALIZATION_SCHEMA,
        "engine": "deterministic_multi_signal.v1",
        "goal_tokens": tokens,
        "confidence": round(confidence, 3),
        "status": "localized" if ranked_files else "needs_more_evidence",
        "candidate_files": ranked_files[:limit],
        "candidate_symbols": symbol_scores[:limit],
        "candidate_tests": test_candidates[:limit],
        "signals": {
            "path_tokens": True,
            "symbol_ast": bool(brain.get("symbols")),
            "imports": bool(brain.get("imports")),
            "test_map": bool((brain.get("test_map") or {}).get("source_to_tests")),
            "test_ownership": bool((brain.get("test_ownership") or {}).get("source_to_tests")),
            "tree_sitter": bool((brain.get("syntax_index") or {}).get("active")),
            "lsp_request_plan": bool(brain.get("lsp_index")),
            "historical_failures": bool(brain.get("historical_failure_signatures")),
            "failure_index": bool(((brain.get("failure_index") or {}).get("by_file") or {})),
            "coverage": bool(((brain.get("coverage_map") or {}).get("files") or {})),
            "git_ownership": bool(((brain.get("git_ownership") or {}).get("by_file") or {})),
            "dirty_state": bool(brain.get("dirty")),
        },
        "limitations": [
            "Live LSP enrichment requires a supported language server in the runtime PATH.",
            "Runtime stack traces improve localization when supplied as task-run observations.",
        ],
    }


def _score_text(text: str, tokens: Sequence[str], *, path_weight: bool) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []
    for token in tokens:
        if not token:
            continue
        if token in text:
            score += 4.0 if path_weight else 3.0
            reasons.append(f"matched token `{token}`")
        else:
            compact = token.replace("_", "")
            if compact and compact in text.replace(" ", ""):
                score += 2.0
                reasons.append(f"matched compact token `{token}`")
    return score, reasons


def _score_path(path: str, tokens: Sequence[str]) -> Tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []
    text = _path_text(path)
    filename = _path_text(Path(path).name)
    filename_hits = 0
    for token in tokens:
        if token in filename:
            score += 9.0
            filename_hits += 1
            reasons.append(f"filename matched token `{token}`")
        elif token in text:
            score += 5.0
            reasons.append(f"path matched token `{token}`")
        else:
            compact = token.replace("_", "")
            compact_text = text.replace(" ", "")
            if compact and compact in compact_text:
                score += 2.5
                reasons.append(f"path matched compact token `{token}`")
    if filename_hits >= 2:
        score += 4.0
        reasons.append("filename matched multiple issue tokens")
    return score, reasons


def _add_file_score(
    scores: DefaultDict[str, Dict[str, Any]],
    path: str,
    score: float,
    reasons: Sequence[str],
    evidence: str,
) -> None:
    if not path:
        return
    applied = float(score)
    if evidence.startswith("symbol:"):
        current_symbol_score = float(scores[path].get("symbol_score") or 0.0)
        remaining = max(0.0, 18.0 - current_symbol_score)
        if remaining <= 0:
            return
        applied = min(applied, remaining)
        scores[path]["symbol_score"] = current_symbol_score + applied
    scores[path]["score"] = float(scores[path].get("score") or 0.0) + applied
    scores[path]["reasons"].extend(reason for reason in reasons if reason)
    if evidence:
        scores[path]["evidence"].append(evidence)


def _paths_in_text(text: str, brain: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for item in brain.get("file_manifest") or []:
        path = str(item.get("path") or "")
        if path and path.lower() in text:
            paths.append(path)
    return paths[:20]


def _localized_failure_evidence(path: str, brain: Dict[str, Any]) -> Dict[str, Any]:
    item = (((brain.get("failure_index") or {}).get("by_file") or {}).get(path) or {})
    if not item:
        return {}
    return {
        "failure_count": item.get("failure_count", 0),
        "goal_match_count": item.get("goal_match_count", 0),
        "lines": list(item.get("lines") or [])[:12],
        "commands": list(item.get("commands") or [])[:6],
        "signatures": list(item.get("signatures") or [])[:3],
        "refs": list(item.get("refs") or [])[:6],
    }


def _localized_coverage(path: str, brain: Dict[str, Any]) -> Dict[str, Any]:
    item = (((brain.get("coverage_map") or {}).get("files") or {}).get(path) or {})
    if not item:
        return {}
    return {
        "line_rate": item.get("line_rate"),
        "branch_rate": item.get("branch_rate"),
        "uncovered_lines": list(item.get("uncovered_lines") or [])[:20],
        "source": item.get("source"),
    }


def _localized_ownership(path: str, brain: Dict[str, Any]) -> Dict[str, Any]:
    item = (((brain.get("git_ownership") or {}).get("by_file") or {}).get(path) or {})
    if not item:
        return {}
    return {
        "change_count": item.get("change_count", 0),
        "churn_score": item.get("churn_score", 0),
        "authors": list(item.get("authors") or [])[:4],
        "last_commit": item.get("last_commit") or {},
    }


def _localized_test_ownership(path: str, brain: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = (((brain.get("test_ownership") or {}).get("source_to_tests") or {}).get(path) or [])
    return [
        {
            "path": item.get("path"),
            "command": item.get("command"),
            "confidence": item.get("confidence"),
            "reasons": list(item.get("reasons") or [])[:5],
            "evidence_pointers": list(item.get("evidence_pointers") or [])[:5],
            "covered_lines": list(item.get("covered_lines") or [])[:20],
        }
        for item in items[:6]
    ]


def _localized_tests(
    ranked_files: Sequence[Dict[str, Any]],
    test_map: Dict[str, Any],
    test_ownership: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    tests: Dict[str, Dict[str, Any]] = {}
    for item in ranked_files:
        path = str(item.get("path") or "")
        if _is_test_file(path):
            tests[path] = {
                "path": path,
                "command": _test_command_for_path(path),
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "reason": "test file localized directly",
            }
        for link in (test_map.get("source_to_tests") or {}).get(path, [])[:6]:
            test_path = str(link.get("path") or "")
            if not test_path:
                continue
            current = tests.get(test_path)
            score = float(item.get("score") or 0) + float(link.get("score") or 0)
            if not current or score > float(current.get("score") or 0):
                tests[test_path] = {
                    "path": test_path,
                    "command": str(link.get("command") or _test_command_for_path(test_path)),
                    "score": round(score, 3),
                    "confidence": round(min(0.95, max(float(item.get("confidence") or 0), float(link.get("confidence") or 0))), 3),
                    "reason": "nearest test from source-to-test map",
                    "source": path,
                }
        for link in ((test_ownership or {}).get("source_to_tests") or {}).get(path, [])[:8]:
            test_path = str(link.get("path") or "")
            if not test_path:
                continue
            current = tests.get(test_path)
            score = float(item.get("score") or 0) + float(link.get("score") or 0)
            if not current or score > float(current.get("score") or 0):
                tests[test_path] = {
                    "path": test_path,
                    "command": str(link.get("command") or _test_command_for_path(test_path)),
                    "score": round(score, 3),
                    "confidence": round(min(0.96, max(float(item.get("confidence") or 0), float(link.get("confidence") or 0))), 3),
                    "reason": "owned test from test-ownership index",
                    "source": path,
                    "evidence_pointers": list(link.get("evidence_pointers") or [])[:6],
                }
    return sorted(tests.values(), key=lambda item: (-float(item.get("score") or 0), item.get("path") or ""))


def verification_card_from_brain(
    contract: Dict[str, Any],
    brain: Dict[str, Any],
    localization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    relevant_files = [str(path) for path in (contract.get("relevant_files") or [])]
    must_run = [str(cmd) for cmd in (contract.get("must_run") or contract.get("test_commands") or [])]
    localization = localization or {}
    localized_test_commands = [
        str(item.get("command"))
        for item in localization.get("candidate_tests") or []
        if item.get("command")
    ]
    fail_to_pass = list(dict.fromkeys(must_run + localized_test_commands))[:12]
    nearest_tests = list(
        dict.fromkeys(
            [str(item.get("path")) for item in localization.get("candidate_tests") or [] if item.get("path")]
            + [path for path in relevant_files if _file_kind(path) == "test"]
            + list((brain.get("test_map") or {}).get("test_files") or [])[:8]
        )
    )[:16]
    source_targets = [
        path
        for path in relevant_files
        if _language_for_path(path) and _file_kind(path) != "test"
    ][:12]
    python_source_targets = [path for path in source_targets if path.endswith(".py")]
    import_smoke = [f"python3 -m py_compile {' '.join(python_source_targets)}"] if python_source_targets else []
    package_roots = sorted({Path(path).parts[0] for path in python_source_targets if Path(path).parts})
    static_checks = list(dict.fromkeys(import_smoke + ([f"python3 -m compileall -q {' '.join(package_roots)}"] if package_roots else [])))
    coverage_targets = _coverage_targets(source_targets, brain)
    pass_to_pass = [
        _test_command_for_path(path)
        for path in nearest_tests
        if _test_command_for_path(path) not in fail_to_pass
    ][:8]
    flaky_test_risks = _flaky_test_risks(fail_to_pass + pass_to_pass, brain)
    risky_paths = {str(item.get("path")) for item in brain.get("risky_files") or []}
    diff_risk = "high" if any(path in risky_paths for path in relevant_files) else str(contract.get("risk") or "medium")
    public_api_risk = "medium" if any(Path(path).name == "__init__.py" for path in relevant_files) else "low"
    if any("api" in _path_text(path) or "server" in _path_text(path) for path in relevant_files):
        public_api_risk = "medium"
    return {
        "schema_version": VERIFICATION_CARD_SCHEMA,
        "engine": "deterministic_verifier_plan.v1",
        "fail_to_pass_tests": fail_to_pass,
        "pass_to_pass_tests": pass_to_pass,
        "nearest_tests": nearest_tests,
        "import_smoke_tests": import_smoke,
        "static_checks": static_checks,
        "coverage_targets": coverage_targets,
        "flaky_test_risks": flaky_test_risks,
        "security_checks": [
            "verify no forbidden path changed",
            "verify no secret-like token introduced",
            "verify benchmark contamination status is clean before submit",
            "review risky files before submit when diff_risk is high",
        ],
        "diff_risk": diff_risk,
        "public_api_risk": public_api_risk,
        "coverage_gaps": _verification_gaps(fail_to_pass, pass_to_pass, source_targets, coverage_targets),
        "submit_requirements": [
            "all fail_to_pass_tests observed as passed",
            "at least one pass_to_pass or static/import smoke check runs when diff_risk is medium or high",
            "edit proof obligations satisfied for every EDIT_FILE",
            "contamination status is clean or explicitly quarantined",
            "replay checkpoint exists before submit",
        ],
        "limitations": [
            "This is a verifier plan, not an automatic test runner.",
            "Hidden tests are never assumed or stored as evidence.",
        ],
    }


def _coverage_targets(source_targets: Sequence[str], brain: Dict[str, Any]) -> List[Dict[str, Any]]:
    coverage_files = ((brain.get("coverage_map") or {}).get("files") or {})
    out: List[Dict[str, Any]] = []
    for path in source_targets:
        item = coverage_files.get(path)
        if not item:
            continue
        out.append(
            {
                "path": path,
                "line_rate": item.get("line_rate"),
                "branch_rate": item.get("branch_rate"),
                "uncovered_lines": list(item.get("uncovered_lines") or [])[:40],
                "source": item.get("source"),
            }
        )
    return out


def _flaky_test_risks(commands: Sequence[str], brain: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    command_set = set(commands)
    for signal in brain.get("flaky_tests") or []:
        command = str(signal.get("test_command") or "")
        if command not in command_set:
            continue
        out.append(
            {
                "test_command": command,
                "status": signal.get("status"),
                "pass_count": signal.get("pass_count"),
                "failure_count": signal.get("failure_count"),
                "confidence": signal.get("confidence"),
                "evidence": list(signal.get("evidence") or [])[:3],
            }
        )
    return out


def _verification_gaps(
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
    source_targets: Sequence[str],
    coverage_targets: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[str]:
    gaps: List[str] = []
    if not fail_to_pass:
        gaps.append("no fail-to-pass test command identified")
    if source_targets and not pass_to_pass:
        gaps.append("no separate pass-to-pass regression command identified")
    covered_paths = {str(item.get("path") or "") for item in (coverage_targets or [])}
    missing_coverage = [path for path in source_targets if path not in covered_paths]
    if missing_coverage:
        gaps.append("no coverage evidence for: " + ", ".join(missing_coverage[:5]))
    return gaps


def _repo_graph_summary(graph: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": graph.get("schema_version") or REPO_GRAPH_ARTIFACT_SCHEMA,
        "artifact_id": graph.get("artifact_id"),
        "node_count": len(graph.get("nodes") or []),
        "edge_count": len(graph.get("edges") or []),
        "truncated": bool(graph.get("truncated")),
        "node_types": dict(graph.get("node_types") or {}),
        "edge_types": dict(graph.get("edge_types") or {}),
    }


def repo_graph_from_brain(
    brain: Dict[str, Any],
    *,
    node_limit: int = MAX_REPO_GRAPH_NODES,
    edge_limit: int = MAX_REPO_GRAPH_EDGES,
) -> Dict[str, Any]:
    """Build a durable code/context graph artifact from a repo brain."""

    node_limit = max(1, int(node_limit or MAX_REPO_GRAPH_NODES))
    edge_limit = max(1, int(edge_limit or MAX_REPO_GRAPH_EDGES))
    brain_ref = str((brain.get("storage") or {}).get("ref") or brain.get("head_short") or brain.get("head_commit") or "")
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    seen_edges: set[Tuple[str, str, str]] = set()

    def provenance(source: str, pointer: str = "") -> Dict[str, Any]:
        return {
            "brain_ref": brain_ref,
            "source": source,
            "evidence_pointer": pointer,
        }

    def add_node(node_id: str, node_type: str, label: str, **metadata: Any) -> None:
        if not node_id or node_id in nodes or len(nodes) >= node_limit:
            return
        source_ref = str(metadata.pop("source", "repo_graph"))
        nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            "label": label,
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "", [], {})},
            "provenance": provenance(source_ref),
        }

    def add_edge(source: str, target: str, edge_type: str, **metadata: Any) -> None:
        if not source or not target or len(edges) >= edge_limit:
            return
        key = (source, target, edge_type)
        if key in seen_edges:
            return
        seen_edges.add(key)
        source_ref = str(metadata.pop("source_ref", "repo_graph"))
        edge = {
            "id": "edge_" + _stable_hash({"s": source, "t": target, "type": edge_type, "m": metadata}, 16),
            "source": source,
            "target": target,
            "type": edge_type,
            "confidence": float(metadata.pop("confidence", 1.0) or 1.0),
            "metadata": {key: value for key, value in metadata.items() if value not in (None, "", [], {})},
            "provenance": provenance(source_ref),
        }
        edges.append(edge)

    for item in brain.get("file_manifest") or []:
        path = str(item.get("path") or "")
        if not path:
            continue
        kind = str(item.get("kind") or _file_kind(path))
        node_type = "test" if kind == "test" else ("config" if kind == "manifest" else "file")
        add_node(
            f"file:{path}",
            node_type,
            path,
            path=path,
            language=item.get("language"),
            bytes=item.get("bytes"),
            sha256=item.get("sha256"),
            indexed=item.get("indexed"),
            source="file_manifest",
        )

    symbol_node_by_id: Dict[str, str] = {}
    for symbol in brain.get("symbols") or []:
        symbol_id = str(symbol.get("id") or _stable_hash(symbol, 16))
        node_id = f"symbol:{symbol_id}"
        symbol_node_by_id[symbol_id] = node_id
        path = str(symbol.get("path") or "")
        add_node(
            node_id,
            "symbol",
            str(symbol.get("qualname") or symbol.get("name") or symbol_id),
            path=path,
            name=symbol.get("name"),
            qualname=symbol.get("qualname"),
            kind=symbol.get("kind"),
            language=symbol.get("language"),
            start_line=symbol.get("start_line"),
            end_line=symbol.get("end_line"),
            source="symbols",
        )
        if path:
            add_edge(f"file:{path}", node_id, "contains", source_ref="symbols", confidence=0.99)

    for source, imports in (brain.get("imports") or {}).items():
        for item in imports or []:
            target = str(item.get("resolved_path") or "")
            if target:
                add_edge(
                    f"file:{source}",
                    f"file:{target}",
                    "imports",
                    module=item.get("module"),
                    confidence=0.95,
                    source_ref="imports",
                )

    for edge in (brain.get("dependency_graph") or {}).get("local_import_edges") or []:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            add_edge(f"file:{source}", f"file:{target}", "imports", module=edge.get("module"), confidence=0.98, source_ref="dependency_graph")

    for call in brain.get("call_graph") or []:
        caller_id = str(call.get("caller_id") or "")
        callee_id = str(call.get("callee_id") or "")
        source_node = symbol_node_by_id.get(caller_id) or f"file:{call.get('path')}"
        target_node = symbol_node_by_id.get(callee_id) if callee_id else ""
        if not target_node and call.get("callee_path"):
            target_node = f"file:{call.get('callee_path')}"
        if target_node:
            add_edge(
                source_node,
                target_node,
                "calls",
                callee=call.get("callee_name") or call.get("callee"),
                line=call.get("line"),
                resolution=call.get("resolution"),
                parser_backend=call.get("parser_backend"),
                confidence=float(call.get("confidence") or 0.5),
                source_ref="call_graph",
            )

    for source, links in ((brain.get("test_map") or {}).get("source_to_tests") or {}).items():
        for link in links or []:
            test_path = str(link.get("path") or "")
            if test_path:
                add_edge(
                    f"file:{source}",
                    f"file:{test_path}",
                    "tested_by",
                    command=link.get("command"),
                    reason=link.get("reason"),
                    confidence=float(link.get("confidence") or 0.6),
                    source_ref="test_map",
                )

    for source, links in ((brain.get("test_ownership") or {}).get("source_to_tests") or {}).items():
        for link in links or []:
            test_path = str(link.get("path") or "")
            if test_path:
                add_edge(
                    f"file:{source}",
                    f"file:{test_path}",
                    "tested_by",
                    command=link.get("command"),
                    reasons=list(link.get("reasons") or [])[:4],
                    evidence_pointers=list(link.get("evidence_pointers") or [])[:6],
                    confidence=float(link.get("confidence") or 0.7),
                    source_ref="test_ownership",
                )

    for path, ownership in ((brain.get("git_ownership") or {}).get("by_file") or {}).items():
        for author in (ownership.get("authors") or [])[:3]:
            name = str(author.get("name") or author.get("email") or "")
            if not name:
                continue
            actor_id = "actor:" + _stable_hash(name, 14)
            add_node(actor_id, "actor", name, email=author.get("email"), source="git_ownership")
            add_edge(f"file:{path}", actor_id, "owned_by", commits=author.get("commits"), confidence=0.65, source_ref="git_ownership")

    for path, failure in ((brain.get("failure_index") or {}).get("by_file") or {}).items():
        error_id = "error:" + _stable_hash({"path": path, "messages": failure.get("messages"), "lines": failure.get("lines")}, 16)
        add_node(
            error_id,
            "error",
            str(failure.get("messages", ["failure"])[0] if failure.get("messages") else "failure"),
            path=path,
            lines=list(failure.get("lines") or [])[:20],
            commands=list(failure.get("commands") or [])[:6],
            failure_count=failure.get("failure_count"),
            source="failure_index",
        )
        add_edge(f"file:{path}", error_id, "failed_with", confidence=0.8, source_ref="failure_index")

    node_type_counts: DefaultDict[str, int] = defaultdict(int)
    edge_type_counts: DefaultDict[str, int] = defaultdict(int)
    for node in nodes.values():
        node_type_counts[str(node.get("type") or "unknown")] += 1
    for edge in edges:
        edge_type_counts[str(edge.get("type") or "unknown")] += 1
    graph = {
        "schema_version": REPO_GRAPH_ARTIFACT_SCHEMA,
        "artifact_id": "repo_graph_" + _stable_hash({"brain_ref": brain_ref, "nodes": len(nodes), "edges": len(edges)}, 16),
        "generated_at": _now_iso(),
        "repo": brain.get("repo"),
        "head_commit": brain.get("head_commit"),
        "brain_ref": brain_ref,
        "nodes": list(nodes.values()),
        "edges": edges,
        "node_types": dict(sorted(node_type_counts.items())),
        "edge_types": dict(sorted(edge_type_counts.items())),
        "truncated": len(nodes) >= node_limit or len(edges) >= edge_limit,
        "policy": {
            "raw_file_bodies_excluded": True,
            "provenance_required": True,
            "source": "repo_brain",
        },
    }
    return graph


def context_graph_slice(
    brain: Dict[str, Any],
    query: str,
    *,
    limit: int = DEFAULT_CONTEXT_GRAPH_QUERY_NODES,
    max_hops: int = 3,
) -> Dict[str, Any]:
    """Return a rich multi-hop graph query proving why context matters."""

    graph = repo_graph_from_brain(brain)
    localization = localize_issue(query, brain, limit=12)
    seed_ids: List[str] = []
    proof_items: List[Dict[str, Any]] = []
    for item in localization.get("candidate_files") or []:
        path = str(item.get("path") or "")
        if path:
            seed_ids.append(f"file:{path}")
            proof_items.append(
                {
                    "kind": "file",
                    "id": f"file:{path}",
                    "why": list(item.get("reasons") or [])[:5],
                    "evidence_pointers": list(item.get("evidence_pointers") or item.get("evidence") or [])[:8],
                    "score": item.get("score"),
                }
            )
    for item in localization.get("candidate_tests") or []:
        path = str(item.get("path") or "")
        if path:
            seed_ids.append(f"file:{path}")
            proof_items.append(
                {
                    "kind": "test",
                    "id": f"file:{path}",
                    "why": [item.get("reason")],
                    "command": item.get("command"),
                    "evidence_pointers": list(item.get("evidence_pointers") or [])[:8],
                    "score": item.get("score"),
                }
            )
    for item in localization.get("candidate_symbols") or []:
        symbol_id = str(item.get("symbol_id") or item.get("id") or "")
        if symbol_id:
            seed_ids.append(f"symbol:{symbol_id}")
            proof_items.append(
                {
                    "kind": "symbol",
                    "id": f"symbol:{symbol_id}",
                    "why": list(item.get("reasons") or [])[:5],
                    "score": item.get("score"),
                }
            )

    limit = int(limit or 0)
    max_nodes = limit if limit > 0 else len(graph.get("nodes") or [])
    max_hops = max(1, int(max_hops or 3))
    seed_set = set(seed_ids)
    selected_nodes = set(seed_ids)
    selected_edges_by_id: Dict[str, Dict[str, Any]] = {}
    frontier = set(seed_ids)
    expansion_tiers: List[Dict[str, Any]] = [
        {
            "hop": 0,
            "reason": "localized seeds from repo brain",
            "node_ids": sorted(seed_set),
            "edge_ids": [],
        }
    ]
    relation_priority = {
        "failed_with": 0,
        "tested_by": 1,
        "calls": 2,
        "imports": 3,
        "contains": 4,
        "owned_by": 5,
    }
    graph_edges = sorted(
        graph.get("edges") or [],
        key=lambda edge: (
            relation_priority.get(str(edge.get("type") or ""), 50),
            str(edge.get("source") or ""),
            str(edge.get("target") or ""),
        ),
    )
    for hop in range(1, max_hops + 1):
        if not frontier or len(selected_nodes) >= max_nodes:
            break
        next_frontier: set[str] = set()
        tier_edge_ids: List[str] = []
        for edge in graph_edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            if source not in frontier and target not in frontier:
                continue
            edge_id = str(edge.get("id") or "")
            if edge_id:
                selected_edges_by_id[edge_id] = edge
                tier_edge_ids.append(edge_id)
            for node_id in (source, target):
                if node_id not in selected_nodes:
                    selected_nodes.add(node_id)
                    next_frontier.add(node_id)
            if len(selected_nodes) >= max_nodes:
                break
        expansion_tiers.append(
            {
                "hop": hop,
                "reason": "structural expansion from prior frontier",
                "node_ids": sorted(next_frontier),
                "edge_ids": tier_edge_ids[:200],
            }
        )
        frontier = next_frontier
    node_by_id = {str(node.get("id")): node for node in graph.get("nodes") or []}
    nodes = [node_by_id[node_id] for node_id in selected_nodes if node_id in node_by_id][:max_nodes]
    kept = {str(node.get("id")) for node in nodes}
    edges = [
        edge
        for edge in selected_edges_by_id.values()
        if edge.get("source") in kept and edge.get("target") in kept
    ]
    return {
        "schema_version": CONTEXT_GRAPH_SLICE_SCHEMA,
        "generated_at": _now_iso(),
        "query": query,
        "repo": brain.get("repo"),
        "brain_ref": graph.get("brain_ref"),
        "localization": localization,
        "nodes": nodes,
        "edges": edges,
        "expansion_tiers": expansion_tiers,
        "proof_items": proof_items,
        "summary": {
            "seed_count": len(seed_set),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "max_hops": max_hops,
            "limit": limit,
            "truncated": len(selected_nodes) > len(nodes),
        },
        "policy": {
            "comprehensive_context_first": True,
            "multi_resolution": True,
            "raw_file_bodies_excluded": True,
            "every_item_has_provenance": True,
        },
    }


def context_graph_query(
    brain: Dict[str, Any],
    query: str,
    *,
    limit: int = DEFAULT_CONTEXT_GRAPH_QUERY_NODES,
    max_hops: int = 3,
) -> Dict[str, Any]:
    return context_graph_slice(brain, query, limit=limit, max_hops=max_hops)


def compile_repo_intelligence(
    repo: str | os.PathLike[str] | None = None,
    *,
    goal: str,
    relevant_files: Optional[Sequence[str]] = None,
    must_run: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    brain = build_repo_brain(
        repo,
        goal=goal,
        relevant_files=relevant_files,
        must_run=must_run,
        persist=True,
    )
    localization = localize_issue(goal, brain)
    return {
        "format": "dhee_repo_intelligence_compile.v1",
        "repo_intelligence": repo_brain_summary(brain),
        "repo_brain": brain,
        "localization": localization,
        "verification_card": verification_card_from_brain(
            {
                "goal": goal,
                "relevant_files": list(relevant_files or []),
                "must_run": list(must_run or []),
            },
            brain,
            localization,
        ),
    }
