"""Digest for subagent / long-text tool returns.

`dhee_agent` accepts a text blob (typically a subagent's final message)
and returns a factual, compact digest: which files were referenced,
bulleted findings, any error indicators, and head/tail excerpts. Raw is
stored behind a ptr. Honest about what was extracted — never invents
references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


CHARS_PER_TOKEN = 3.5

# file:line patterns — matches path/to/file.py:123 (optionally :col)
_FILE_LINE_RE = re.compile(
    r"(?<![\w/])"                             # not preceded by word/slash
    r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})"  # path with extension
    r":(\d+)"                                  # :lineno
    r"(?::\d+)?"                               # optional :col
)
_FILE_RANGE_RE = re.compile(
    r"(?<![\w/])"
    r"([A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,8})"
    r":(\d+)(?:-(\d+))?"
)

_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$", re.MULTILINE)

_ERROR_RE = re.compile(
    r"\b(error|exception|failed|failure|traceback|fatal)\b",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
_CONFIDENCE_RE = re.compile(r"\bconfidence\s*[:=]\s*([A-Za-z0-9_.%-]+)", re.IGNORECASE)
_FIELD_RE = re.compile(r"^\s*(command|test|observed|expected|minimal repro|repro|skipped|not loaded)\s*[:=-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)

_KIND_ALIASES = {
    "localization": "LocalizationDigest",
    "localizationdigest": "LocalizationDigest",
    "localization_digest": "LocalizationDigest",
    "bugrepro": "BugReproDigest",
    "bugreprodigest": "BugReproDigest",
    "bug_repro": "BugReproDigest",
    "bug_repro_digest": "BugReproDigest",
    "read": "ReadDigest",
    "readdigest": "ReadDigest",
    "read_digest": "ReadDigest",
    "search": "SearchDigest",
    "searchdigest": "SearchDigest",
    "search_digest": "SearchDigest",
}


@dataclass
class AgentDigest:
    char_count: int
    line_count: int
    est_tokens: int
    kind: str
    file_refs: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    error_hits: int = 0
    head: str = ""
    tail: str = ""
    notes: list[str] = field(default_factory=list)
    schema: str = "GenericDigest"
    typed: dict[str, object] = field(default_factory=dict)

    def render(self, ptr: str) -> str:
        lines: list[str] = [f'<dhee_agent ptr="{ptr}">']
        lines.append(
            f"size={self.line_count} lines, {self.char_count} chars, "
            f"~{self.est_tokens} tokens"
        )
        lines.append(f"kind={self.kind}")
        if self.schema and self.schema != "GenericDigest":
            lines.append(f"schema={self.schema}")
            lines.extend(_render_typed_section(self.schema, self.typed))
        if self.headings:
            lines.append("headings:")
            for h in self.headings[:8]:
                lines.append(f"  {h}")
            if len(self.headings) > 8:
                lines.append(f"  (+{len(self.headings)-8} more)")
        if self.file_refs:
            shown = self.file_refs[:15]
            lines.append("file_refs:")
            for r in shown:
                lines.append(f"  {r}")
            if len(self.file_refs) > 15:
                lines.append(f"  (+{len(self.file_refs)-15} more)")
        if self.bullets:
            lines.append("bullets:")
            for b in self.bullets[:10]:
                lines.append(f"  - {b}")
            if len(self.bullets) > 10:
                lines.append(f"  (+{len(self.bullets)-10} more)")
        if self.error_hits:
            lines.append(f"error_signals={self.error_hits}")
        if self.head:
            lines.append("head:")
            for hl in self.head.splitlines()[:6]:
                lines.append(f"  {hl}")
        if self.tail:
            lines.append("tail:")
            for tl in self.tail.splitlines()[-4:]:
                lines.append(f"  {tl}")
        for n in self.notes:
            lines.append(f"note: {n}")
        lines.append(f'(expand: dhee_expand_result(ptr="{ptr}"))')
        lines.append("</dhee_agent>")
        return "\n".join(lines)


def _head_tail(text: str, head_lines: int = 6, tail_lines: int = 4) -> tuple[str, str]:
    lines = text.splitlines()
    if len(lines) <= head_lines + tail_lines:
        return text, ""
    return "\n".join(lines[:head_lines]), "\n".join(lines[-tail_lines:])


def _canonical_schema(kind: str | None, text: str, *, file_refs: list[str], error_hits: int, bullets: list[str]) -> str:
    if kind:
        alias = _KIND_ALIASES.get(kind.replace("-", "_").replace(" ", "").lower())
        if alias:
            return alias
    lowered = text.lower()
    if ("observed" in lowered and "expected" in lowered) or ("minimal repro" in lowered):
        return "BugReproDigest"
    if "ranked" in lowered and file_refs:
        return "SearchDigest"
    if ("skipped" in lowered or "not loaded" in lowered) and file_refs:
        return "ReadDigest"
    if file_refs and ("confidence" in lowered or "evidence" in lowered or "line" in lowered):
        return "LocalizationDigest"
    if error_hits and file_refs:
        return "BugReproDigest"
    if file_refs and bullets:
        return "LocalizationDigest"
    return "GenericDigest"


def _confidence(text: str) -> str:
    match = _CONFIDENCE_RE.search(text)
    return match.group(1).strip() if match else ""


def _fields(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for match in _FIELD_RE.finditer(text):
        key = match.group(1).lower().replace(" ", "_")
        out.setdefault(key, []).append(match.group(2).strip())
    return out


def _locations(text: str, file_refs: list[str]) -> list[dict[str, str]]:
    seen: set[str] = set()
    locations: list[dict[str, str]] = []
    for match in _FILE_RANGE_RE.finditer(text):
        line_range = match.group(2)
        if match.group(3):
            line_range = f"{line_range}-{match.group(3)}"
        key = f"{match.group(1)}:{line_range}"
        if key in seen:
            continue
        seen.add(key)
        locations.append({"path": match.group(1), "range": line_range})
    if not locations:
        for ref in file_refs[:10]:
            path, _, line = ref.partition(":")
            locations.append({"path": path, "range": line})
    return locations[:20]


def _evidence_quotes(bullets: list[str], text: str) -> list[str]:
    quotes = [b for b in bullets if len(b) <= 220]
    if quotes:
        return quotes[:8]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[:4]


def _typed_payload(schema: str, text: str, *, file_refs: list[str], bullets: list[str]) -> dict[str, object]:
    fields = _fields(text)
    if schema == "LocalizationDigest":
        return {
            "locations": _locations(text, file_refs),
            "evidence": _evidence_quotes(bullets, text),
            "confidence": _confidence(text) or "unspecified",
        }
    if schema == "BugReproDigest":
        command = (fields.get("command") or fields.get("test") or [])[:1]
        observed = (fields.get("observed") or [])[:3]
        expected = (fields.get("expected") or [])[:3]
        repro = (fields.get("minimal_repro") or fields.get("repro") or bullets or [])[:3]
        return {
            "command": command[0] if command else "",
            "observed": observed,
            "expected": expected,
            "minimal_repro": repro,
            "file_refs": file_refs[:10],
            "confidence": _confidence(text) or "unspecified",
        }
    if schema == "ReadDigest":
        skipped = (fields.get("skipped") or fields.get("not_loaded") or [])[:6]
        return {
            "file_refs": file_refs[:15],
            "relevant_excerpts": _evidence_quotes(bullets, text),
            "skipped_sections": skipped,
            "confidence": _confidence(text) or "unspecified",
        }
    if schema == "SearchDigest":
        hits = []
        for idx, ref in enumerate(file_refs[:15], start=1):
            hits.append({"rank": idx, "ref": ref})
        if not hits:
            for idx, bullet in enumerate(bullets[:10], start=1):
                hits.append({"rank": idx, "summary": bullet})
        return {
            "ranked_hits": hits,
            "confidence": _confidence(text) or "unspecified",
        }
    return {}


def _render_typed_section(schema: str, payload: dict[str, object]) -> list[str]:
    lines: list[str] = []
    if schema == "LocalizationDigest":
        lines.append("locations:")
        for item in payload.get("locations", [])[:10]:  # type: ignore[index]
            if isinstance(item, dict):
                lines.append(f"  - {item.get('path')}:{item.get('range')}")
        lines.append(f"confidence={payload.get('confidence') or 'unspecified'}")
        evidence = payload.get("evidence", [])
        if evidence:
            lines.append("evidence:")
            for item in evidence[:5]:  # type: ignore[index]
                lines.append(f"  - {item}")
    elif schema == "BugReproDigest":
        if payload.get("command"):
            lines.append(f"command={payload.get('command')}")
        for label in ("observed", "expected", "minimal_repro"):
            values = payload.get(label, [])
            if values:
                lines.append(f"{label}:")
                for item in values[:4]:  # type: ignore[index]
                    lines.append(f"  - {item}")
        lines.append(f"confidence={payload.get('confidence') or 'unspecified'}")
    elif schema == "ReadDigest":
        if payload.get("file_refs"):
            lines.append("file_refs:")
            for item in payload.get("file_refs", [])[:10]:  # type: ignore[index]
                lines.append(f"  {item}")
        if payload.get("relevant_excerpts"):
            lines.append("relevant_excerpts:")
            for item in payload.get("relevant_excerpts", [])[:5]:  # type: ignore[index]
                lines.append(f"  - {item}")
        if payload.get("skipped_sections"):
            lines.append("skipped_sections:")
            for item in payload.get("skipped_sections", [])[:5]:  # type: ignore[index]
                lines.append(f"  - {item}")
    elif schema == "SearchDigest":
        lines.append("ranked_hits:")
        for item in payload.get("ranked_hits", [])[:10]:  # type: ignore[index]
            if isinstance(item, dict) and item.get("ref"):
                lines.append(f"  {item.get('rank')}. {item.get('ref')}")
            elif isinstance(item, dict):
                lines.append(f"  {item.get('rank')}. {item.get('summary')}")
        lines.append(f"confidence={payload.get('confidence') or 'unspecified'}")
    return lines


def digest_agent(text: str, *, kind: str | None = None) -> AgentDigest:
    """Build an AgentDigest from an arbitrary text blob."""
    char_count = len(text)
    line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    if text == "":
        line_count = 0

    # file:line refs — de-dupe, preserve order
    seen: set[str] = set()
    file_refs: list[str] = []
    for m in _FILE_LINE_RE.finditer(text):
        ref = f"{m.group(1)}:{m.group(2)}"
        if ref in seen:
            continue
        seen.add(ref)
        file_refs.append(ref)

    headings = [
        f"{'#' * len(m.group(1))} {m.group(2)}"
        for m in _HEADING_RE.finditer(text)
    ]

    bullets: list[str] = []
    for m in _BULLET_RE.finditer(text):
        b = m.group(1).strip()
        if b and len(b) <= 200:
            bullets.append(b)

    error_hits = len(_ERROR_RE.findall(text))

    head, tail = _head_tail(text)

    schema = _canonical_schema(kind, text, file_refs=file_refs, error_hits=error_hits, bullets=bullets)
    typed = _typed_payload(schema, text, file_refs=file_refs, bullets=bullets)

    # Auto-classify kind if not given, preserving legacy labels for generic output.
    if schema != "GenericDigest":
        kind = schema
    elif kind is None:
        if "```" in text and file_refs:
            kind = "code-review"
        elif error_hits and file_refs:
            kind = "error-report"
        elif headings:
            kind = "structured-summary"
        elif file_refs:
            kind = "code-survey"
        elif bullets:
            kind = "bulleted-findings"
        else:
            kind = "prose"

    return AgentDigest(
        char_count=char_count,
        line_count=line_count,
        est_tokens=int(char_count / CHARS_PER_TOKEN),
        kind=kind,
        file_refs=file_refs,
        headings=headings,
        bullets=bullets,
        error_hits=error_hits,
        head=head,
        tail=tail,
        schema=schema,
        typed=typed,
    )
