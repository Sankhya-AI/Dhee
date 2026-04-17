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

_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$", re.MULTILINE)

_ERROR_RE = re.compile(
    r"\b(error|exception|failed|failure|traceback|fatal)\b",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)


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

    def render(self, ptr: str) -> str:
        lines: list[str] = [f'<dhee_agent ptr="{ptr}">']
        lines.append(
            f"size={self.line_count} lines, {self.char_count} chars, "
            f"~{self.est_tokens} tokens"
        )
        lines.append(f"kind={self.kind}")
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

    # Auto-classify kind if not given.
    if kind is None:
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
    )
