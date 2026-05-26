"""Calm terminal output helpers for Dhee.

This module is inspired by Clypi's small CLI primitives (styling,
visible-width alignment, and themeable output). It is intentionally
implemented as a Python 3.9-safe, dependency-free Dhee layer instead of
vendoring Clypi's Python 3.11 command framework.

MIT attribution for the adapted ideas/patterns:

Copyright 2025 Daniel Melchor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Optional, TextIO


_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

_FG = {
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "default": "39",
    "dim": "90",
    # One restrained Dhee accent. ANSI 256-color orange/amber; used sparingly.
    "amber": "38;5;208",
}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text))


def visible_width(text: str) -> int:
    return len(strip_ansi(str(text)))


def _color_enabled(file: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("DHEE_NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    is_tty = getattr(file, "isatty", lambda: False)
    try:
        return bool(is_tty())
    except Exception:
        return False


def style(
    text: Any,
    *,
    fg: Optional[str] = None,
    bold: bool = False,
    dim: bool = False,
    enabled: bool = True,
) -> str:
    raw = str(text)
    if not enabled:
        return raw
    codes = []
    if bold:
        codes.append("1")
    if dim:
        codes.append("2")
    if fg:
        codes.append(str(_FG.get(fg, _FG["default"])))
    if not codes:
        return raw
    return f"\033[{';'.join(codes)}m{raw}\033[0m"


def pad_right(text: str, width: int) -> str:
    return str(text) + (" " * max(0, width - visible_width(str(text))))


@dataclass(frozen=True)
class DheeTheme:
    accent: str = "amber"
    ok: str = "default"
    warn: str = "amber"
    error: str = "red"
    muted: str = "dim"


class CalmPrinter:
    """Small, stable-output printer for Dhee CLI commands."""

    def __init__(
        self,
        *,
        file: Optional[TextIO] = None,
        color: Optional[bool] = None,
        theme: DheeTheme = DheeTheme(),
        label_width: int = 12,
    ) -> None:
        self.file = file or sys.stdout
        self.color = _color_enabled(self.file) if color is None else bool(color)
        self.theme = theme
        self.label_width = label_width

    def write(self, text: str = "") -> None:
        print(text, file=self.file)

    def styled(self, text: Any, *, fg: Optional[str] = None, bold: bool = False, dim: bool = False) -> str:
        return style(text, fg=fg, bold=bold, dim=dim, enabled=self.color)

    def title(self, text: str, *, subtitle: str = "") -> None:
        self.write(self.styled(text, fg=self.theme.accent, bold=True))
        if subtitle:
            self.write(f"  {self.styled(subtitle, fg=self.theme.muted, dim=True)}")
        self.write()

    def row(self, label: str, value: Any = "", *, muted: bool = False) -> None:
        key = pad_right(str(label), self.label_width)
        value_text = str(value)
        if muted:
            value_text = self.styled(value_text, fg=self.theme.muted, dim=True)
        self.write(f"  {self.styled(key, fg=self.theme.muted, dim=True)} {value_text}")

    def status(self, label: str, state: str, detail: Any = "") -> None:
        state_key = str(state or "").lower()
        color = {
            "ok": self.theme.ok,
            "done": self.theme.ok,
            "created": self.theme.ok,
            "updated": self.theme.ok,
            "skip": self.theme.warn,
            "skipped": self.theme.warn,
            "warn": self.theme.warn,
            "error": self.theme.error,
        }.get(state_key, self.theme.muted)
        key = pad_right(str(label), self.label_width)
        state_text = pad_right(state_key or "-", 8)
        line = f"  {self.styled(key, fg=self.theme.muted, dim=True)} {self.styled(state_text, fg=color)}"
        if detail:
            line += f" {detail}"
        self.write(line)

    def paragraph(self, text: str, *, indent: str = "  ") -> None:
        for line in str(text).splitlines() or [""]:
            self.write(f"{indent}{line}")

    def next(self, commands: Iterable[str]) -> None:
        self.write()
        self.write(self.styled("Next", fg=self.theme.accent, bold=True))
        for command in commands:
            self.write(f"  {command}")


def state_from_file_result(result: dict[str, Any]) -> str:
    if result.get("created"):
        return "created"
    if result.get("updated"):
        return "updated"
    return "unchanged"


def render_init(info: dict[str, Any], *, file: Optional[TextIO] = None, color: Optional[bool] = None) -> None:
    """Render the human `dhee init` result in Dhee's calm house style."""

    ui = CalmPrinter(file=file, color=color)
    repo_root = str(info.get("repo_root") or "")
    kind = str(info.get("kind") or "git_repo")
    ui.title("Dhee init", subtitle="workspace opted into shared context")
    ui.row("workspace", repo_root)
    ui.row("kind", kind.replace("_", " "))
    ui.row("repo id", str(info.get("repo_id") or ""))
    if info.get("source_url"):
        suffix = " (cloned)" if info.get("cloned") else ""
        ui.row("source", f"{info.get('source_url')}{suffix}")
    elif info.get("git_remote_url"):
        ui.row("git remote", str(info.get("git_remote_url")))

    hooks = info.get("hooks") or []
    ui.status("git hooks", "ok" if hooks else "skip", ", ".join(hooks) if hooks else "none")

    claude = info.get("claude_md") or {}
    ui.status("CLAUDE.md", state_from_file_result(claude), str(claude.get("path") or ""))

    agents = info.get("agents_md") or {}
    ui.status("AGENTS.md", state_from_file_result(agents), str(agents.get("path") or ""))

    ingest = info.get("ingest") or {}
    ingest_status = str(ingest.get("status") or "skipped")
    if ingest_status == "ok":
        detail_parts = [
            f"indexed {ingest.get('files_indexed', 0)}",
            f"unchanged {ingest.get('files_unchanged', 0)}",
            f"chunks +{ingest.get('chunks_stored', 0)}",
        ]
        replaced = int(ingest.get("chunks_replaced", 0) or 0)
        files_pruned = int(ingest.get("files_pruned", 0) or 0)
        chunks_pruned = int(ingest.get("chunks_pruned", 0) or 0)
        if replaced:
            detail_parts.append(f"replaced {replaced}")
        if files_pruned or chunks_pruned:
            detail_parts.append(f"pruned {files_pruned} file(s) / {chunks_pruned} chunk(s)")
        ui.status("markdown", "ok", ", ".join(detail_parts))
    elif ingest_status == "skipped":
        reason = str(ingest.get("reason") or "")
        if reason == "memory_unavailable":
            detail = "provider/API key not configured; run `dhee onboard`"
        elif reason == "skip_ingest":
            detail = "--skip-ingest"
        else:
            detail = reason or "not run"
        ui.status("markdown", "skip", detail)
    elif ingest_status == "error":
        ui.status("markdown", "error", f"{ingest.get('reason', 'unknown')}: {ingest.get('detail', '')}")
    else:
        ui.status("markdown", ingest_status)

    ui.row("linked", f"{info.get('linked_repos', 0)} workspace(s) on this machine")

    first_light = info.get("first_light") or {}
    hits = first_light.get("hits") or []
    ui.write()
    if hits:
        ui.write(ui.styled("First light", fg=ui.theme.accent, bold=True))
        for hit in hits:
            text = (str(hit.get("text") or "").strip().splitlines() or [""])[0]
            head = (text[:140] + "...") if len(text) > 140 else text
            score = float(hit.get("score", 0.0) or 0.0)
            ui.write(f"  [{score:.2f}] {head}")
            src = str(hit.get("source_path") or "")
            if src:
                from pathlib import Path

                src_short = "/".join(Path(src).parts[-2:])
                ui.write(f"         {ui.styled(src_short, fg=ui.theme.muted, dim=True)}")
    elif first_light.get("status") == "skipped":
        ui.status("first light", "skip", str(first_light.get("reason") or "skipped"))
    else:
        ui.status("first light", "skip", "no cross-repo learnings yet")

    ui.next(
        [
            "dhee status",
            'dhee recall "<query>"',
            "dhee inbox",
        ]
    )
