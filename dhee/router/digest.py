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
    focus: list[str] = field(default_factory=list)
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
                if not v:
                    continue
                counts: dict[str, int] = {}
                order: list[str] = []
                for item in v:
                    if item not in counts:
                        order.append(item)
                    counts[item] = counts.get(item, 0) + 1
                rendered = [(f"{s} x{counts[s]}" if counts[s] > 1 else s) for s in order]
                shown = rendered if len(rendered) <= 20 else rendered[:20] + [f"(+{len(rendered)-20} more)"]
                lines.append(f"  {k}: [{', '.join(shown)}]")
        if self.focus and depth != "shallow":
            lines.append("focus:")
            for item in self.focus[:6]:
                for line in str(item).splitlines()[:24]:
                    lines.append(f"  {line}")
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
        ".kt": "kotlin", ".kts": "kotlin",
        ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
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
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(([^)]*)\)|([A-Za-z_$][\w$]*))\s*=>",
    re.MULTILINE,
)
_JS_IMPORT_RE = re.compile(r"""^\s*import\s+[^;'"]*['"]([^'"]+)['"]""", re.MULTILINE)
_TS_INTERFACE_RE = re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
_TS_TYPE_RE = re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=", re.MULTILINE)
_JS_NAMED_EXPORT_RE = re.compile(r"^\s*export\s+\{([^}]+)\}", re.MULTILINE)


def _js_ts_symbols(text: str) -> dict[str, list[str]]:
    functions: list[str] = []
    for m in _JS_FN_RE.finditer(text):
        functions.append(f"{m.group(1)}({m.group(2).strip()})")
    for m in _JS_CONST_FN_RE.finditer(text):
        args = (m.group(2) if m.group(2) is not None else m.group(3) or "").strip()
        functions.append(f"{m.group(1)}({args})")
    classes = [m.group(1) for m in _JS_CLASS_RE.finditer(text)]
    imports = [m.group(1) for m in _JS_IMPORT_RE.finditer(text)]
    types = [f"{m.group(1)} (interface)" for m in _TS_INTERFACE_RE.finditer(text)]
    types.extend(f"{m.group(1)} (type)" for m in _TS_TYPE_RE.finditer(text))
    exports: list[str] = []
    for m in _JS_NAMED_EXPORT_RE.finditer(text):
        for item in m.group(1).split(","):
            name = item.strip().split(" as ", 1)[0].strip()
            if name and name not in exports:
                exports.append(name)
    component_names = [
        item.split("(", 1)[0]
        for item in [*classes, *functions]
        if item and item.split("(", 1)[0][:1].isupper()
    ]
    return {
        "classes": classes,
        "functions": functions,
        "types": types,
        "components": component_names,
        "imports": imports,
        "exports": exports,
    }


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


_JAVA_TYPE_RE = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*(class|interface|enum|record)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)
_JAVA_METHOD_RE = re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)+(?:<[^>]+>\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][\w]*)\s*\(([^;{}]*)\)",
    re.MULTILINE,
)
_JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([^;]+);", re.MULTILINE)


def _java_symbols(text: str) -> dict[str, list[str]]:
    types = [f"{m.group(2)} ({m.group(1)})" for m in _JAVA_TYPE_RE.finditer(text)]
    methods = [f"{m.group(1)}({m.group(2).strip()})" for m in _JAVA_METHOD_RE.finditer(text)]
    imports = [m.group(1).strip() for m in _JAVA_IMPORT_RE.finditer(text)]
    return {"types": types, "methods": methods, "imports": imports}


_SHELL_FN_RE = re.compile(
    r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*(?:\(\))?\s*\{",
    re.MULTILINE,
)
_SHELL_EXPORT_RE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=", re.MULTILINE)


def _shell_symbols(text: str) -> dict[str, list[str]]:
    functions = [f"{m.group(1)}()" for m in _SHELL_FN_RE.finditer(text)]
    variables = [m.group(1) for m in _SHELL_EXPORT_RE.finditer(text)]
    return {"functions": functions, "variables": variables}


_SQL_CREATE_RE = re.compile(
    r"\bcreate\s+(table|view|index|function|procedure)\s+(?:if\s+not\s+exists\s+)?[`\"[]?([A-Za-z_][\w.$]*)",
    re.IGNORECASE,
)


def _sql_symbols(text: str) -> dict[str, list[str]]:
    objects: list[str] = []
    for m in _SQL_CREATE_RE.finditer(text):
        objects.append(f"{m.group(2)} ({m.group(1).lower()})")
    return {"objects": objects}


_LOG_LEVEL_RE = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b", re.IGNORECASE)


def _log_symbols(text: str) -> dict[str, list[str]]:
    counts: dict[str, int] = {}
    signals: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = _LOG_LEVEL_RE.search(line)
        if not m:
            continue
        level = m.group(1).upper()
        if level == "WARNING":
            level = "WARN"
        counts[level] = counts.get(level, 0) + 1
        if level in {"WARN", "ERROR", "FATAL", "CRITICAL"} and len(signals) < 10:
            snippet = line.strip()
            if len(snippet) > 180:
                snippet = snippet[:177] + "..."
            signals.append(f"{i}: {level} {snippet}")
    levels = [f"{level}={count}" for level, count in sorted(counts.items())]
    return {"levels": levels, "signals": signals}


def _generic_symbols(_text: str) -> dict[str, list[str]]:
    return {}


def _head_tail(text: str, lines_each: int = 5) -> tuple[str, str]:
    lines = text.splitlines()
    if len(lines) <= lines_each * 2:
        return text, ""
    head = "\n".join(lines[:lines_each])
    tail = "\n".join(lines[-lines_each:])
    return head, tail


_STOP_TERMS = {
    "about", "after", "again", "before", "build", "class", "code", "debug",
    "dhee", "error", "failed", "failure", "file", "find", "from", "function",
    "into", "line", "module", "need", "needs", "read", "return", "test",
    "tests", "this", "traceback", "where", "with",
}


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(query or "")):
        term = raw.lower()
        if term in _STOP_TERMS:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:16]


def _query_line_numbers(query: str) -> list[int]:
    nums: list[int] = []
    for m in re.finditer(r"(?:line\s+|:)(\d{1,6})\b", str(query or ""), flags=re.IGNORECASE):
        try:
            value = int(m.group(1))
        except ValueError:
            continue
        if value > 0 and value not in nums:
            nums.append(value)
    return nums[:8]


def _numbered_window(lines: list[str], start: int, end: int) -> str:
    start = max(1, start)
    end = min(len(lines), max(start, end))
    width = len(str(end))
    return "\n".join(f"{i:>{width}}| {lines[i - 1]}" for i in range(start, end + 1))


def _line_window(text: str, line_no: int, *, radius: int = 5, label: str = "line") -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    start = max(1, line_no - radius)
    end = min(len(lines), line_no + radius)
    return f"{label} {line_no}:\n{_numbered_window(lines, start, end)}"


def _symbol_focus_python(text: str, terms: list[str]) -> list[str]:
    if not terms:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    out: list[str] = []

    def _matches(name: str) -> bool:
        hay = name.lower()
        return any(term in hay for term in terms)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _matches(getattr(node, "name", "")):
            continue
        start = int(getattr(node, "lineno", 1) or 1)
        end = int(getattr(node, "end_lineno", start + 24) or start + 24)
        end = min(end, start + 44, len(lines))
        kind = "class" if isinstance(node, ast.ClassDef) else "def"
        out.append(f"{kind} {node.name} lines {start}-{end}:\n{_numbered_window(lines, start, end)}")
        if len(out) >= 4:
            break
    return out


def _contract_focus_python(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    lines = text.splitlines()
    items: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            line_no = int(getattr(node, "lineno", 0) or 0)
            if line_no <= 0 or line_no > len(lines):
                continue
            items.append(f"{line_no}| {lines[line_no - 1].strip()}")
        if len(items) >= 24:
            break
    return ["module contract:\n" + "\n".join(items)] if items else []


def _term_focus_lines(text: str, terms: list[str], *, label: str = "matched lines") -> list[str]:
    if not terms:
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        lower = line.lower()
        if any(term in lower for term in terms):
            hits.append((i, line))
        if len(hits) >= 8:
            break
    if not hits:
        return []
    width = len(str(hits[-1][0]))
    body = "\n".join(f"{i:>{width}}| {line}" for i, line in hits)
    return [f"{label}:\n{body}"]


def _failure_landmarks(text: str) -> list[str]:
    lines = text.splitlines()
    rx = re.compile(r"\b(assert|raise|except|error|failed|failure|traceback|todo|fixme)\b", re.IGNORECASE)
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines, start=1):
        if rx.search(line):
            hits.append((i, line))
        if len(hits) >= 8:
            break
    if not hits:
        return []
    width = len(str(hits[-1][0]))
    body = "\n".join(f"{i:>{width}}| {line}" for i, line in hits)
    return ["failure landmarks:\n" + body]


def _build_focus(path: str, text: str, *, query: str = "", task_intent: str = "") -> list[str]:
    intent = str(task_intent or "").strip()
    terms = _query_terms(query)
    focus: list[str] = []
    for line_no in _query_line_numbers(query):
        snippet = _line_window(text, line_no, radius=6, label="referenced line")
        if snippet:
            focus.append(snippet)
    kind = _detect_kind(path, text)
    if kind == "python":
        if intent == "understand_module":
            focus.extend(_contract_focus_python(text))
        focus.extend(_symbol_focus_python(text, terms))
    if intent == "debug_failure":
        focus.extend(_failure_landmarks(text))
    focus.extend(_term_focus_lines(text, terms))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in focus:
        key = item[:400]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item[:1800])
        if len(deduped) >= 6:
            break
    return deduped


def digest_read(
    path: str,
    text: str,
    *,
    depth: str = "normal",
    range_: tuple[int, int] | None = None,
    query: str = "",
    task_intent: str = "",
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
    elif kind == "java":
        symbols = _java_symbols(text)
    elif kind == "shell":
        symbols = _shell_symbols(text)
    elif kind == "sql":
        symbols = _sql_symbols(text)
    elif kind == "log":
        symbols = _log_symbols(text)
    else:
        symbols = _generic_symbols(text)

    head, tail = _head_tail(text, lines_each=5 if depth != "deep" else 10)
    focus = _build_focus(path, text, query=query, task_intent=task_intent)

    return ReadDigest(
        path=path,
        line_count=line_count,
        char_count=char_count,
        est_tokens=int(char_count / CHARS_PER_TOKEN),
        kind=kind,
        range=range_,
        symbols={k: v for k, v in symbols.items() if v},
        focus=focus,
        head=head,
        tail=tail,
        notes=notes,
    )
