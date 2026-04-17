"""File-content digest for `dhee_read`.

Goal: a short factual summary that the LLM can act on without re-reading
the whole file. Must be honest about what was extracted — never
hallucinate symbols the digest didn't actually find.

Depth knobs:
    shallow  — line/char counts + kind + top-level symbol names only
    normal   — shallow + head (5 lines) + tail (5 lines) + compact signatures
    deep     — normal + larger head/tail windows
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

CHARS_PER_TOKEN = 3.5


@dataclass
class ReadDigest:
    path: str
    line_count: int
    char_count: int
    est_tokens: int
    kind: str
    range: tuple[int, int] | None = None  # 1-indexed inclusive
    symbols: dict[str, list[str]] = field(default_factory=dict)
    head: str = ""
    tail: str = ""
    notes: list[str] = field(default_factory=list)

    def render(self, ptr: str, depth: str = "normal") -> str:
        lines: list[str] = [f'<dhee_read ptr="{ptr}">']
        lines.append(f"path={self.path}")
        if self.range:
            a, b = self.range
            lines.append(
                f"range={a}-{b} ({self.line_count} lines, "
                f"{self.char_count} chars, ~{self.est_tokens} tokens)"
            )
        else:
            lines.append(
                f"size={self.line_count} lines, {self.char_count} chars, "
                f"~{self.est_tokens} tokens"
            )
        lines.append(f"kind={self.kind}")
        if self.symbols:
            lines.append("symbols:")
            for k, v in self.symbols.items():
                if v:
                    shown = v if len(v) <= 20 else v[:20] + [f"(+{len(v)-20} more)"]
                    lines.append(f"  {k}: [{', '.join(shown)}]")
        if depth != "shallow":
            if self.head:
                lines.append("head:")
                for hl in self.head.splitlines()[: (10 if depth == "deep" else 5)]:
                    lines.append(f"  {hl}")
            if self.tail:
                lines.append("tail:")
                for tl in self.tail.splitlines()[-(10 if depth == "deep" else 5):]:
                    lines.append(f"  {tl}")
        for n in self.notes:
            lines.append(f"note: {n}")
        lines.append(f"(expand: dhee_expand_result(ptr=\"{ptr}\"))")
        lines.append("</dhee_read>")
        return "\n".join(lines)


def _detect_kind(path: str, text: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    mapping = {
        ".py": "python",
        ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
        ".jsx": "jsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".rb": "ruby",
        ".md": "markdown", ".mdx": "markdown",
        ".json": "json",
        ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml",
        ".sh": "shell", ".bash": "shell", ".zsh": "shell",
        ".html": "html", ".htm": "html",
        ".css": "css",
        ".sql": "sql",
        ".log": "log",
        ".txt": "text",
        ".csv": "csv",
        ".jsonl": "jsonl",
    }
    return mapping.get(ext, "text")


def _python_symbols(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Extract classes, function signatures, and imports from Python source."""
    notes: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        notes.append(f"python parse failed at line {e.lineno}; falling back to truncation only")
        return {}, notes

    classes: list[str] = []
    functions: list[str] = []
    imports: list[str] = []

    def _sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        args = []
        posonly = getattr(fn.args, "posonlyargs", []) or []
        for a in posonly + fn.args.args:
            args.append(a.arg)
        if fn.args.vararg:
            args.append(f"*{fn.args.vararg.arg}")
        for a in fn.args.kwonlyargs:
            args.append(a.arg)
        if fn.args.kwarg:
            args.append(f"**{fn.args.kwarg.arg}")
        prefix = "async " if isinstance(fn, ast.AsyncFunctionDef) else ""
        return f"{prefix}{fn.name}({', '.join(args)})"

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes.append(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(f"{node.name}.{_sig(sub)}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_sig(node))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            dots = "." * node.level
            for alias in node.names:
                imports.append(f"{dots}{mod}.{alias.name}".lstrip("."))

    return {
        "classes": classes,
        "functions": functions,
        "imports": imports,
    }, notes


def _markdown_symbols(text: str) -> dict[str, list[str]]:
    headings: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            level = len(m.group(1))
            headings.append(f"{'  ' * (level-1)}{'#' * level} {m.group(2)}")
    return {"headings": headings[:40]}


def _json_symbols(text: str) -> tuple[dict[str, list[str]], list[str]]:
    notes: list[str] = []
    try:
        obj = json.loads(text)
    except Exception as e:
        notes.append(f"json parse failed: {e}; treating as text")
        return {}, notes

    def _describe(v: Any) -> str:
        if isinstance(v, dict):
            return f"object({len(v)} keys)"
        if isinstance(v, list):
            return f"array({len(v)})"
        return type(v).__name__

    if isinstance(obj, dict):
        return {"top_level_keys": [f"{k}: {_describe(obj[k])}" for k in obj]}, notes
    if isinstance(obj, list):
        return {"array": [f"len={len(obj)}", f"item0={_describe(obj[0]) if obj else 'empty'}"]}, notes
    return {"scalar": [type(obj).__name__]}, notes


_JS_FN_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_JS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_JS_CONST_FN_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
    re.MULTILINE,
)
_JS_IMPORT_RE = re.compile(r"""^\s*import\s+[^;'"]*['"]([^'"]+)['"]""", re.MULTILINE)


def _js_ts_symbols(text: str) -> dict[str, list[str]]:
    functions: list[str] = []
    for m in _JS_FN_RE.finditer(text):
        functions.append(f"{m.group(1)}({m.group(2).strip()})")
    for m in _JS_CONST_FN_RE.finditer(text):
        functions.append(f"{m.group(1)}({m.group(2).strip()})")
    classes = [m.group(1) for m in _JS_CLASS_RE.finditer(text)]
    imports = [m.group(1) for m in _JS_IMPORT_RE.finditer(text)]
    return {"classes": classes, "functions": functions, "imports": imports}


_GO_FN_RE = re.compile(
    r"^func\s+(?:\([^)]+\)\s+)?([A-Za-z_][\w]*)\s*\(([^)]*)\)", re.MULTILINE
)
_GO_TYPE_RE = re.compile(r"^type\s+([A-Za-z_][\w]*)\s+(struct|interface)\b", re.MULTILINE)
_GO_IMPORT_RE = re.compile(r"""^\s*(?:import\s+)?"([^"]+)"\s*$""", re.MULTILINE)


def _go_symbols(text: str) -> dict[str, list[str]]:
    functions = [f"{m.group(1)}({m.group(2).strip()})" for m in _GO_FN_RE.finditer(text)]
    types = [f"{m.group(1)} ({m.group(2)})" for m in _GO_TYPE_RE.finditer(text)]
    imports = list({m.group(1) for m in _GO_IMPORT_RE.finditer(text)})
    return {"types": types, "functions": functions, "imports": imports}


_RUST_FN_RE = re.compile(
    r"^\s*(?:pub\s+(?:\([^)]+\)\s+)?)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)\s*(?:<[^>]*>)?\s*\(([^)]*)\)",
    re.MULTILINE,
)
_RUST_STRUCT_RE = re.compile(
    r"^\s*(?:pub\s+)?(struct|enum|trait)\s+([A-Za-z_][\w]*)", re.MULTILINE
)
_RUST_USE_RE = re.compile(r"^\s*use\s+([^;]+);", re.MULTILINE)


def _rust_symbols(text: str) -> dict[str, list[str]]:
    functions = [f"{m.group(1)}({m.group(2).strip()})" for m in _RUST_FN_RE.finditer(text)]
    types = [f"{m.group(2)} ({m.group(1)})" for m in _RUST_STRUCT_RE.finditer(text)]
    imports = [m.group(1).strip() for m in _RUST_USE_RE.finditer(text)]
    return {"types": types, "functions": functions, "imports": imports}


def _generic_symbols(_text: str) -> dict[str, list[str]]:
    return {}


def _head_tail(text: str, lines_each: int = 5) -> tuple[str, str]:
    lines = text.splitlines()
    if len(lines) <= lines_each * 2:
        return text, ""
    head = "\n".join(lines[:lines_each])
    tail = "\n".join(lines[-lines_each:])
    return head, tail


def digest_read(
    path: str,
    text: str,
    *,
    depth: str = "normal",
    range_: tuple[int, int] | None = None,
) -> ReadDigest:
    """Build a ReadDigest for a file's contents."""
    line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    if text == "":
        line_count = 0
    char_count = len(text)
    kind = _detect_kind(path, text)

    symbols: dict[str, list[str]] = {}
    notes: list[str] = []

    if kind == "python":
        symbols, notes = _python_symbols(text)
    elif kind == "markdown":
        symbols = _markdown_symbols(text)
    elif kind in ("json",):
        symbols, notes = _json_symbols(text)
    elif kind in ("javascript", "typescript", "jsx"):
        symbols = _js_ts_symbols(text)
    elif kind == "go":
        symbols = _go_symbols(text)
    elif kind == "rust":
        symbols = _rust_symbols(text)
    else:
        symbols = _generic_symbols(text)

    head, tail = _head_tail(text, lines_each=5 if depth != "deep" else 10)

    return ReadDigest(
        path=path,
        line_count=line_count,
        char_count=char_count,
        est_tokens=int(char_count / CHARS_PER_TOKEN),
        kind=kind,
        range=range_,
        symbols={k: v for k, v in symbols.items() if v},
        head=head,
        tail=tail,
        notes=notes,
    )
