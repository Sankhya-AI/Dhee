"""Digest for `dhee_grep`.

Executes a pattern search via ripgrep (falling back to stdlib `re`) and
produces a compact summary: match count, top-K file:line hits, and a
per-file match-density table. Full raw hit-list is persisted behind a
`G-` pointer.

The digest intentionally mirrors what a developer actually skims first:
"how many matches, in how many files, which files dominate, and a few
concrete hits to orient me." Everything else lives behind the pointer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 3.5

# Caps to keep the digest itself small. The raw hit list is always
# preserved behind the pointer — these only trim the in-context preview.
TOP_HITS_DEFAULT = 8
TOP_FILES_DEFAULT = 8
MAX_SCAN_BYTES = 50 * 1024 * 1024  # 50 MB per file ceiling in fallback mode


@dataclass
class GrepHit:
    path: str
    line: int
    text: str


@dataclass
class GrepDigest:
    pattern: str
    path: str
    match_count: int
    file_count: int
    total_bytes: int
    est_tokens: int
    top_hits: list[GrepHit] = field(default_factory=list)
    top_files: list[tuple[str, int]] = field(default_factory=list)  # (path, count)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)
    engine: str = "rg"

    def render(self, ptr: str) -> str:
        lines: list[str] = [f'<dhee_grep ptr="{ptr}">']
        lines.append(f"pattern={self.pattern}")
        lines.append(f"path={self.path}")
        lines.append(
            f"matches={self.match_count} files={self.file_count} "
            f"raw={self.total_bytes}B ~{self.est_tokens} tokens engine={self.engine}"
        )
        if self.top_files:
            lines.append("top-files:")
            for p, c in self.top_files:
                lines.append(f"  {c:>5}  {p}")
        if self.top_hits:
            lines.append("top-hits:")
            for h in self.top_hits:
                snippet = h.text.rstrip("\n")
                if len(snippet) > 200:
                    snippet = snippet[:197] + "..."
                lines.append(f"  {h.path}:{h.line}: {snippet}")
        if self.truncated:
            lines.append("note: hit list truncated — raw behind ptr preserves full set")
        for n in self.notes:
            lines.append(f"note: {n}")
        lines.append(f'(expand: dhee_expand_result(ptr="{ptr}"))')
        lines.append("</dhee_grep>")
        return "\n".join(lines)


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def _run_rg(
    *,
    pattern: str,
    path: str,
    glob: str | None,
    case_insensitive: bool,
    fixed_string: bool,
    multiline: bool,
    context: int,
) -> tuple[int, str, str]:
    """Invoke ripgrep; return (exit_code, stdout, stderr)."""
    args: list[str] = ["rg", "--line-number", "--with-filename", "--color=never", "--no-heading"]
    if case_insensitive:
        args.append("-i")
    if fixed_string:
        args.append("-F")
    if multiline:
        args.extend(["-U", "--multiline-dotall"])
    if glob:
        args.extend(["--glob", glob])
    if context > 0:
        args.extend(["-C", str(int(context))])
    args.append("--")
    args.append(pattern)
    args.append(path)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", (exc.stderr or "") + "\n[timed out]"


def _parse_rg_lines(stdout: str) -> list[GrepHit]:
    """Parse rg's `path:line:content` output into hits.

    rg prefixes filenames with `{path}:{line}:` for matches. Context lines
    use `-` separator. We keep only the `:` matches (primary hits).
    """
    hits: list[GrepHit] = []
    for raw in stdout.splitlines():
        if not raw:
            continue
        # Match line format: path:line:content (colons in path survive
        # because rg always emits line-number in position 2).
        # Split on first two colons.
        first = raw.find(":")
        if first < 0:
            continue
        second = raw.find(":", first + 1)
        if second < 0:
            continue
        path_part = raw[:first]
        lineno_part = raw[first + 1 : second]
        content = raw[second + 1 :]
        try:
            lineno = int(lineno_part)
        except ValueError:
            continue
        hits.append(GrepHit(path=path_part, line=lineno, text=content))
    return hits


def _fallback_python(
    *,
    pattern: str,
    path: str,
    case_insensitive: bool,
    fixed_string: bool,
) -> list[GrepHit]:
    """Pure-Python fallback when rg is unavailable. Walks `path`,
    treats each readable text file as UTF-8, scans line-by-line."""
    flags = re.IGNORECASE if case_insensitive else 0
    if fixed_string:
        rx = re.compile(re.escape(pattern), flags)
    else:
        try:
            rx = re.compile(pattern, flags)
        except re.error:
            return []
    hits: list[GrepHit] = []
    root = Path(path)
    candidates: list[Path] = [root] if root.is_file() else list(_walk_files(root))
    for f in candidates:
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size > MAX_SCAN_BYTES:
            continue
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, start=1):
                    if rx.search(line):
                        hits.append(GrepHit(path=str(f), line=i, text=line.rstrip("\n")))
        except Exception:
            continue
    return hits


def _walk_files(root: Path):
    if not root.exists():
        return
    if root.is_file():
        yield root
        return
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            yield Path(dirpath) / fn


def _tally_files(hits: list[GrepHit]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for h in hits:
        counts[h.path] = counts.get(h.path, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def digest_grep(
    *,
    pattern: str,
    path: str,
    glob: str | None = None,
    case_insensitive: bool = False,
    fixed_string: bool = False,
    multiline: bool = False,
    context: int = 0,
    top_hits: int = TOP_HITS_DEFAULT,
    top_files: int = TOP_FILES_DEFAULT,
) -> tuple[GrepDigest, str]:
    """Run the search and return (digest, raw_blob)."""
    notes: list[str] = []
    engine = "rg"
    raw_stdout = ""
    raw_stderr = ""

    if _rg_available():
        rc, raw_stdout, raw_stderr = _run_rg(
            pattern=pattern,
            path=path,
            glob=glob,
            case_insensitive=case_insensitive,
            fixed_string=fixed_string,
            multiline=multiline,
            context=context,
        )
        if rc == 124:
            notes.append("ripgrep timed out at 120s")
        if raw_stderr.strip():
            first = raw_stderr.strip().splitlines()[0][:200]
            notes.append(f"stderr[0]={first}")
        hits = _parse_rg_lines(raw_stdout)
    else:
        engine = "python-fallback"
        notes.append("ripgrep not installed; falling back to Python scan")
        hits = _fallback_python(
            pattern=pattern,
            path=path,
            case_insensitive=case_insensitive,
            fixed_string=fixed_string,
        )
        raw_stdout = "\n".join(f"{h.path}:{h.line}:{h.text}" for h in hits)

    tally = _tally_files(hits)
    top_file_list = tally[:top_files]
    top_hit_list = hits[:top_hits]

    raw_blob = raw_stdout if raw_stdout else ""
    if raw_stderr:
        raw_blob = (raw_blob + "\n--- stderr ---\n" + raw_stderr) if raw_blob else ("--- stderr ---\n" + raw_stderr)
    total_bytes = len(raw_blob.encode("utf-8", errors="replace"))
    est_tokens = int(total_bytes / CHARS_PER_TOKEN)

    digest = GrepDigest(
        pattern=pattern,
        path=path,
        match_count=len(hits),
        file_count=len({h.path for h in hits}),
        total_bytes=total_bytes,
        est_tokens=est_tokens,
        top_hits=top_hit_list,
        top_files=top_file_list,
        truncated=len(hits) > len(top_hit_list),
        notes=notes,
        engine=engine,
    )
    return digest, raw_blob
