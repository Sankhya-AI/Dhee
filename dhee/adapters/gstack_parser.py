"""Pure parsers for gstack's on-disk memory surfaces.

gstack stores its memory under ``${GSTACK_HOME:-$HOME/.gstack}/projects/<slug>/``:

* ``learnings.jsonl`` — one JSON object per line, one learning per row
* ``timeline.jsonl`` — one JSON object per line, one skill-fire event per row
* ``<branch>-reviews.jsonl`` — one JSON object per line, one review finding per row
* ``checkpoints/<timestamp>-<slug>.md`` — YAML frontmatter + four markdown sections

These parsers are pure: they take file contents and yield normalised dicts
ready for ``dhee.adapters.gstack`` to hand to ``Dhee.remember``. No I/O
beyond the caller's ``path.read_text``. No side effects.

Unknown fields on JSONL rows are preserved verbatim on the ``raw`` field
so future gstack schema drift does not silently discard data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

# Checkpoint filenames are `YYYYMMDD-HHMMSS-<slug>.md` or
# `YYYYMMDD-HHMMSS-<slug>-<suffix>.md` when there is a same-second
# collision. gstack's own sanitiser caps slugs at 60 chars.
_CHECKPOINT_NAME_RE = re.compile(
    r"^(?P<ts>\d{8}-\d{6})-(?P<slug>[a-z0-9.-]{1,60})(?:-(?P<suffix>[a-z0-9]{1,8}))?\.md$"
)

# Markdown section headings emitted by context-save/SKILL.md.tmpl.
# We match these four headings case-insensitively so light drift (e.g.
# `### summary`) does not drop a section.
_CHECKPOINT_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("summary", ("summary",)),
    ("decisions", ("decisions made", "decisions")),
    ("remaining", ("remaining work", "remaining")),
    ("notes", ("notes",)),
)

# Insight-level prompt-injection patterns. Mirrors the regex list in
# ``bin/gstack-learnings-log`` so we do not ingest atoms gstack itself
# would have rejected if its own defences were current.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions|context|rules)", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"always\s+output\s+no\s+findings", re.I),
    re.compile(r"skip\s+(all\s+)?(security|review|checks)", re.I),
    re.compile(r"override[:\s]", re.I),
    re.compile(r"\bsystem\s*:", re.I),
    re.compile(r"\bassistant\s*:", re.I),
    re.compile(r"\buser\s*:", re.I),
    re.compile(r"do\s+not\s+(report|flag|mention)", re.I),
    re.compile(r"approve\s+(all|every|this)", re.I),
)


@dataclass
class GstackAtom:
    """Normalised payload ready for ``Dhee.remember``."""

    kind: str  # "learning" | "timeline" | "review" | "checkpoint_section"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_key: str = ""  # stable identity for dedup within a single file


def has_injection(text: str) -> bool:
    """True when text matches any prompt-injection pattern from gstack's own denylist."""

    if not text:
        return False
    return any(pat.search(text) for pat in _INJECTION_PATTERNS)


def _safe_json_loads(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_learnings(lines: Iterable[str], *, slug: str) -> Iterator[GstackAtom]:
    """Yield one ``GstackAtom`` per valid learning row.

    Drops rows that fail schema validation, are missing required fields,
    or contain insight-level injection patterns. Unknown fields ride
    through on ``metadata["raw"]``.
    """

    allowed_types = {"pattern", "pitfall", "preference", "architecture", "tool", "operational"}
    for lineno, raw_line in enumerate(lines, start=1):
        obj = _safe_json_loads(raw_line)
        if obj is None:
            continue

        key = str(obj.get("key") or "").strip()
        insight = str(obj.get("insight") or "").strip()
        type_ = str(obj.get("type") or "").strip()
        if not key or not insight or type_ not in allowed_types:
            continue
        if has_injection(insight):
            continue

        confidence = obj.get("confidence")
        try:
            confidence_int = int(confidence) if confidence is not None else 5
        except (TypeError, ValueError):
            confidence_int = 5
        confidence_int = max(1, min(10, confidence_int))

        source = str(obj.get("source") or "observed")
        trusted = bool(obj.get("trusted", source == "user-stated"))
        ts = str(obj.get("ts") or "")
        files = obj.get("files") if isinstance(obj.get("files"), list) else []

        content = f"[{type_}:{key}] {insight}"
        yield GstackAtom(
            kind="learning",
            content=content,
            metadata={
                "source": "gstack",
                "gstack_slug": slug,
                "gstack_kind": "learning",
                "gstack_type": type_,
                "gstack_key": key,
                "gstack_confidence": confidence_int,
                "gstack_source": source,
                "gstack_trusted": trusted,
                "gstack_ts": ts,
                "gstack_files": [str(f) for f in files],
                "gstack_skill": str(obj.get("skill") or ""),
                "raw": obj,
            },
            source_key=f"learning:{slug}:{lineno}",
        )


def parse_timeline(lines: Iterable[str], *, slug: str) -> Iterator[GstackAtom]:
    """Yield one ``GstackAtom`` per timeline event.

    Timeline events are low-signal on their own so we coerce into short
    prose suitable for embedding: ``"<skill> <event> on <branch> (<outcome>)"``.
    """

    for lineno, raw_line in enumerate(lines, start=1):
        obj = _safe_json_loads(raw_line)
        if obj is None:
            continue
        skill = str(obj.get("skill") or "").strip()
        event = str(obj.get("event") or "").strip()
        if not skill or event not in {"started", "completed"}:
            continue
        branch = str(obj.get("branch") or "").strip()
        outcome = str(obj.get("outcome") or "").strip()
        duration = obj.get("duration_s")
        ts = str(obj.get("ts") or "")

        tail_bits: list[str] = []
        if branch:
            tail_bits.append(f"branch={branch}")
        if outcome:
            tail_bits.append(f"outcome={outcome}")
        if duration:
            tail_bits.append(f"duration_s={duration}")
        tail = " ".join(tail_bits)

        content = f"/{skill} {event}"
        if tail:
            content = f"{content} ({tail})"

        yield GstackAtom(
            kind="timeline",
            content=content,
            metadata={
                "source": "gstack",
                "gstack_slug": slug,
                "gstack_kind": "timeline",
                "gstack_skill": skill,
                "gstack_event": event,
                "gstack_branch": branch,
                "gstack_outcome": outcome,
                "gstack_duration_s": duration,
                "gstack_ts": ts,
                "raw": obj,
            },
            source_key=f"timeline:{slug}:{lineno}",
        )


def parse_reviews(lines: Iterable[str], *, slug: str, branch: str) -> Iterator[GstackAtom]:
    """Yield one ``GstackAtom`` per review finding row."""

    for lineno, raw_line in enumerate(lines, start=1):
        obj = _safe_json_loads(raw_line)
        if obj is None:
            continue
        summary = str(obj.get("summary") or obj.get("finding") or obj.get("message") or "").strip()
        if not summary:
            continue
        if has_injection(summary):
            continue

        severity = str(obj.get("severity") or "").strip()
        file_ = str(obj.get("file") or "").strip()
        line = obj.get("line")
        reviewer = str(obj.get("reviewer") or obj.get("role") or "").strip()
        ts = str(obj.get("ts") or "")

        prefix_bits = [p for p in (reviewer, severity) if p]
        prefix = f"[{' '.join(prefix_bits)}] " if prefix_bits else ""
        locator = ""
        if file_:
            locator = f" ({file_}{':' + str(line) if line else ''})"
        content = f"{prefix}{summary}{locator}"

        yield GstackAtom(
            kind="review",
            content=content,
            metadata={
                "source": "gstack",
                "gstack_slug": slug,
                "gstack_kind": "review",
                "gstack_branch": branch,
                "gstack_severity": severity,
                "gstack_file": file_,
                "gstack_line": line,
                "gstack_reviewer": reviewer,
                "gstack_ts": ts,
                "raw": obj,
            },
            source_key=f"review:{slug}:{branch}:{lineno}",
        )


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter, body). Frontmatter is best-effort YAML-lite."""

    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    rest = text[end + 4 :].lstrip("\n")
    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            current_list_key = None
            continue
        if line.startswith("  - ") and current_list_key:
            meta.setdefault(current_list_key, []).append(line[4:].strip())
            continue
        if ":" not in line:
            current_list_key = None
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            current_list_key = key
            meta[key] = []
        else:
            current_list_key = None
            meta[key] = value
    return meta, rest


def _split_sections(body: str) -> dict[str, str]:
    """Split checkpoint body into {section_label: text} by H3 headings."""

    section_map = {alias.lower(): label for label, aliases in _CHECKPOINT_SECTIONS for alias in aliases}
    current_label: str | None = None
    sections: dict[str, list[str]] = {}
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            heading = stripped[4:].strip().lower().rstrip(":")
            current_label = section_map.get(heading)
            if current_label is not None:
                sections.setdefault(current_label, [])
            continue
        if current_label is None:
            continue
        sections[current_label].append(line)
    return {label: "\n".join(lines).strip() for label, lines in sections.items() if any(l.strip() for l in lines)}


def parse_checkpoint(
    path: Path,
    text: str,
    *,
    slug: str,
) -> tuple[str, list[GstackAtom]]:
    """Parse one checkpoint markdown file into per-section atoms.

    Returns ``(checkpoint_id, atoms)``. ``checkpoint_id`` is derived from
    the filename (timestamp + slug fragment) and is used as the
    ``parent_checkpoint_id`` on each section atom so sibling sections can
    be rehydrated together.
    """

    name = path.name
    match = _CHECKPOINT_NAME_RE.match(name)
    ts = match.group("ts") if match else ""
    file_slug = match.group("slug") if match else name.removesuffix(".md")
    suffix = match.group("suffix") if match and match.group("suffix") else ""
    checkpoint_id = f"{slug}:{ts}:{file_slug}" + (f":{suffix}" if suffix else "")

    frontmatter, body = _parse_frontmatter(text)
    title = ""
    for line in body.splitlines():
        if line.startswith("## Working on:"):
            title = line.removeprefix("## Working on:").strip()
            break

    sections = _split_sections(body)
    branch = str(frontmatter.get("branch") or "")
    status = str(frontmatter.get("status") or "")
    timestamp = str(frontmatter.get("timestamp") or ts)

    atoms: list[GstackAtom] = []
    for label in ("summary", "decisions", "remaining", "notes"):
        payload = sections.get(label)
        if not payload:
            continue
        if has_injection(payload):
            continue
        content = f"[checkpoint:{label}] {title} — {payload}" if title else f"[checkpoint:{label}] {payload}"
        atoms.append(
            GstackAtom(
                kind="checkpoint_section",
                content=content,
                metadata={
                    "source": "gstack",
                    "gstack_slug": slug,
                    "gstack_kind": "checkpoint_section",
                    "gstack_section": label,
                    "gstack_title": title,
                    "gstack_branch": branch,
                    "gstack_status": status,
                    "gstack_ts": timestamp,
                    "gstack_checkpoint_id": checkpoint_id,
                    "gstack_checkpoint_path": str(path),
                    "parent_checkpoint_id": checkpoint_id,
                },
                source_key=f"checkpoint:{checkpoint_id}:{label}",
            )
        )
    return checkpoint_id, atoms
