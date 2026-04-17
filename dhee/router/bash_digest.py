"""Command-class-aware digest for `dhee_bash`.

The goal is to summarise shell output at the *semantics* level of the
command that produced it, not just truncate. A pytest run wants pass/fail
counts + first error. A git log wants commit count + head/tail. A find
or ls wants count + head. Everything else falls back to head+tail+elide.

The classifier is regex-based on the command string — fast, honest about
its confidence, never guesses a class if the signal is weak.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

CHARS_PER_TOKEN = 3.5


@dataclass
class BashDigest:
    cmd: str
    exit_code: int
    cls: str  # "git_log", "pytest", "listing", "grep", "generic", ...
    duration_ms: int
    stdout_bytes: int
    stderr_bytes: int
    est_tokens: int
    summary: list[str] = field(default_factory=list)
    head: str = ""
    tail: str = ""
    notes: list[str] = field(default_factory=list)

    def render(self, ptr: str) -> str:
        lines: list[str] = [f'<dhee_bash ptr="{ptr}">']
        lines.append(f"cmd={self.cmd}")
        lines.append(
            f"exit={self.exit_code} duration={self.duration_ms}ms "
            f"stdout={self.stdout_bytes}B stderr={self.stderr_bytes}B "
            f"~{self.est_tokens} tokens"
        )
        lines.append(f"class={self.cls}")
        if self.summary:
            lines.append("summary:")
            for s in self.summary:
                lines.append(f"  {s}")
        if self.head:
            lines.append("stdout-head:")
            for hl in self.head.splitlines()[:8]:
                lines.append(f"  {hl}")
        if self.tail:
            lines.append("stdout-tail:")
            for tl in self.tail.splitlines()[-5:]:
                lines.append(f"  {tl}")
        for n in self.notes:
            lines.append(f"note: {n}")
        lines.append(f'(expand: dhee_expand_result(ptr="{ptr}"))')
        lines.append("</dhee_bash>")
        return "\n".join(lines)


def _classify(cmd: str) -> str:
    """Cheap regex classifier. Returns a class string."""
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    if not tokens:
        return "generic"
    # Skip env prefixes like "FOO=bar cmd ..."
    i = 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("="):
        i += 1
    head = tokens[i:] if i < len(tokens) else tokens
    if not head:
        return "generic"
    first = head[0].rsplit("/", 1)[-1]

    if first == "git":
        sub = head[1] if len(head) > 1 else ""
        if sub == "log":
            return "git_log"
        if sub in ("diff", "show"):
            return "git_diff"
        if sub == "status":
            return "git_status"
        return "git_other"
    if first in ("pytest", "py.test"):
        return "pytest"
    if first in ("npm", "pnpm", "yarn") and len(head) > 1 and head[1] in ("test", "run"):
        return "npm_run"
    if first in ("ls", "find", "fd", "tree"):
        return "listing"
    if first in ("grep", "rg", "ack"):
        return "grep"
    if first in ("cat", "head", "tail", "less"):
        return "file_dump"
    if first in ("wc",):
        return "wc"
    if first in ("make", "cargo", "go"):
        return "build"
    return "generic"


def _head_tail(text: str, head_lines: int = 8, tail_lines: int = 5) -> tuple[str, str]:
    lines = text.splitlines()
    if len(lines) <= head_lines + tail_lines:
        return text, ""
    return "\n".join(lines[:head_lines]), "\n".join(lines[-tail_lines:])


_GIT_COMMIT_RE = re.compile(r"^commit [0-9a-f]{7,40}", re.MULTILINE)


def _summarise_git_log(stdout: str) -> list[str]:
    commits = _GIT_COMMIT_RE.findall(stdout)
    # oneline format: no "commit " prefix — detect SHA at line start
    if not commits:
        oneline = [l for l in stdout.splitlines() if re.match(r"^[0-9a-f]{7,40}\s", l)]
        if oneline:
            return [f"commits={len(oneline)} (oneline format)"]
    return [f"commits={len(commits)}"] if commits else ["no commits parsed"]


_PYTEST_SUMMARY_RE = re.compile(
    r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|warning|warnings)",
    re.IGNORECASE,
)
_PYTEST_FAILED_HDR_RE = re.compile(
    r"^(FAILED|ERROR)\s+(\S+)", re.MULTILINE
)


def _summarise_pytest(stdout: str, stderr: str) -> list[str]:
    combined = stdout + "\n" + stderr
    counts: dict[str, int] = {}
    for m in _PYTEST_SUMMARY_RE.finditer(combined):
        counts[m.group(2).lower()] = int(m.group(1))
    items = [f"{k}={v}" for k, v in counts.items()]
    first_fail = _PYTEST_FAILED_HDR_RE.search(combined)
    if first_fail:
        items.append(f"first_fail={first_fail.group(2)}")
    return items or ["no pytest summary parsed"]


def _summarise_listing(stdout: str) -> list[str]:
    lines = [l for l in stdout.splitlines() if l.strip()]
    return [f"entries={len(lines)}"]


def _summarise_grep(stdout: str) -> list[str]:
    lines = [l for l in stdout.splitlines() if l.strip()]
    files: set[str] = set()
    for l in lines:
        # grep -n output: path:line:content — take up to first colon
        if ":" in l:
            files.add(l.split(":", 1)[0])
    return [f"matches={len(lines)}", f"files_matched={len(files)}"]


def _summarise_generic(stdout: str, stderr: str) -> list[str]:
    sl = stdout.splitlines()
    el = stderr.splitlines()
    return [f"stdout_lines={len(sl)}", f"stderr_lines={len(el)}"]


def digest_bash(
    *,
    cmd: str,
    exit_code: int,
    duration_ms: int,
    stdout: str,
    stderr: str,
) -> BashDigest:
    """Build a BashDigest for a command's output."""
    cls = _classify(cmd)
    stdout_bytes = len(stdout.encode("utf-8", errors="replace"))
    stderr_bytes = len(stderr.encode("utf-8", errors="replace"))
    est_tokens = int((stdout_bytes + stderr_bytes) / CHARS_PER_TOKEN)

    if cls == "git_log":
        summary = _summarise_git_log(stdout)
    elif cls == "pytest" or cls == "npm_run":
        summary = _summarise_pytest(stdout, stderr)
    elif cls == "listing":
        summary = _summarise_listing(stdout)
    elif cls == "grep":
        summary = _summarise_grep(stdout)
    else:
        summary = _summarise_generic(stdout, stderr)

    head, tail = _head_tail(stdout, head_lines=8, tail_lines=5)

    notes: list[str] = []
    if stderr.strip() and cls != "pytest":
        stderr_preview = stderr.strip().splitlines()[0][:200]
        notes.append(f"stderr[0]={stderr_preview}")

    return BashDigest(
        cmd=cmd,
        exit_code=exit_code,
        cls=cls,
        duration_ms=duration_ms,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        est_tokens=est_tokens,
        summary=summary,
        head=head,
        tail=tail,
        notes=notes,
    )
