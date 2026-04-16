"""Markdown → doc-chunk splitter.

Turns a CLAUDE.md (or any heavy markdown reference file) into a list of
semantically-addressable chunks that can be stored as high-strength vector
memories and selectively recalled at session/turn time.

Goal: instead of the host agent paying 2000 tokens for a full CLAUDE.md on
every session, Dhee stores the file as chunks, embeds them once, and
injects only the chunk(s) that match the current task. If nothing matches
above threshold, nothing is injected.

Rules:
- One chunk per leaf section (deepest heading in the hierarchy). Parent
  headings become the chunk's ``heading_path`` breadcrumb.
- Sections that exceed ``max_chars`` are split at paragraph boundaries.
- Fenced code blocks (``` ... ```) are never split mid-fence.
- A file with no headings yields a single chunk with an empty heading_path.
- Heading lines are stripped from chunk bodies — the path is the identity.

The chunker is pure: no I/O, no network, no LLM. Callers handle storage.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable


DEFAULT_MAX_CHARS = 1500
_MIN_CHUNK_CHARS = 20

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^(\s*)(```|~~~)")


@dataclass
class Chunk:
    text: str
    heading_path: tuple[str, ...]
    chunk_index: int
    char_count: int = 0
    source_path: str = ""
    source_sha: str = ""

    def __post_init__(self) -> None:
        if self.char_count == 0:
            self.char_count = len(self.text)

    @property
    def heading_breadcrumb(self) -> str:
        return " › ".join(self.heading_path)

    def to_metadata(self) -> dict:
        return {
            "kind": "doc_chunk",
            "source_path": self.source_path,
            "source_sha": self.source_sha,
            "heading_path": list(self.heading_path),
            "heading_breadcrumb": self.heading_breadcrumb,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
        }

    def to_embedded_text(self) -> str:
        """Text prepared for embedding: breadcrumb + body.

        Prepending the heading trail biases cosine similarity toward
        headings that name the topic. Without this, a chunk about
        "Dhee MCP authentication" retrieves less reliably than its body
        alone suggests.
        """
        if not self.heading_path:
            return self.text
        return f"{self.heading_breadcrumb}\n\n{self.text}"


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_markdown(
    text: str,
    *,
    source_path: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[Chunk]:
    """Split markdown into heading-scoped chunks.

    ``max_chars`` is the soft upper bound per chunk. Sections bigger than
    that are split on paragraph (blank-line) boundaries. Code fences
    stay intact even if that pushes a chunk over the limit — splitting
    inside a fence would corrupt both halves.
    """
    if not text:
        return []

    source_sha = sha256_of(text)
    sections = _split_by_heading(text)
    chunks: list[Chunk] = []
    idx = 0
    for heading_path, body in sections:
        body = body.strip("\n")
        if len(body) < _MIN_CHUNK_CHARS and not heading_path:
            # Pre-heading preamble that's too short to matter on its own.
            continue
        for part in _split_by_size(body, max_chars):
            if len(part.strip()) < _MIN_CHUNK_CHARS:
                continue
            chunks.append(
                Chunk(
                    text=part.strip(),
                    heading_path=tuple(heading_path),
                    chunk_index=idx,
                    source_path=source_path,
                    source_sha=source_sha,
                )
            )
            idx += 1
    return chunks


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _split_by_heading(text: str) -> Iterable[tuple[list[str], str]]:
    """Yield (heading_path, body) pairs, walking the heading hierarchy."""
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []  # (level, title)
    current_body: list[str] = []
    current_path: list[str] = []
    in_fence = False
    fence_marker = ""

    def emit():
        if current_body or current_path:
            yield_path = list(current_path)
            yield_body = "\n".join(current_body)
            return yield_path, yield_body
        return None

    results: list[tuple[list[str], str]] = []

    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            current_body.append(line)
            continue

        if in_fence:
            current_body.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            # Close out the previous section.
            if current_body or current_path:
                results.append((list(current_path), "\n".join(current_body)))
            current_body = []

            level = len(heading.group(1))
            title = heading.group(2).strip()

            # Pop the stack back to parent level.
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current_path = [t for _, t in stack]
            continue

        current_body.append(line)

    # Flush the trailing section.
    if current_body or current_path:
        results.append((list(current_path), "\n".join(current_body)))

    return results


def _split_by_size(body: str, max_chars: int) -> list[str]:
    """Split ``body`` into pieces of roughly ``max_chars`` or less.

    Splits at blank lines. Code fences are kept intact (never split
    mid-fence). A single paragraph larger than ``max_chars`` is emitted
    as-is — splitting mid-paragraph destroys the meaning more than it
    saves tokens, and the embedding will still index it usefully.
    """
    if len(body) <= max_chars:
        return [body]

    blocks = _split_on_blank_lines_respecting_fences(body)
    pieces: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for block in blocks:
        block_len = len(block) + (2 if buf else 0)
        if buf and buf_len + block_len > max_chars:
            pieces.append("\n\n".join(buf))
            buf = [block]
            buf_len = len(block)
        else:
            buf.append(block)
            buf_len += block_len
    if buf:
        pieces.append("\n\n".join(buf))
    return pieces


def _split_on_blank_lines_respecting_fences(body: str) -> list[str]:
    lines = body.splitlines()
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in lines:
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(2)
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            current.append(line)
            continue
        if in_fence:
            current.append(line)
            continue
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current).rstrip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).rstrip())
    return [b for b in blocks if b.strip()]
