"""Coarse intent classification for router calls.

Phase 4 v1 — heuristic only. Each dhee_* handler tags its ptr-store
meta with an intent so the self-evolution loop (Phase 8) can bucket
expansion-rate by (tool, intent) and retune digest depth per bucket.

Why heuristic first: we don't have enough expansion-log data to know
whether the LLM fallback would change any decisions. Every byte of
classifier latency sits in the hot path — we earn the right to add
Gemma when the heuristic buckets start disagreeing with expansion
outcomes. That's a Phase 4 v2 call made on evidence, not intuition.

Taxonomy (stable strings — treated as keys in policy + tune tools):

    Read:  source_code | test | config | doc | data | build | other
    Bash:  (reuses bash_digest classes: git_log, git_diff, pytest,
           listing, grep, generic, etc.)
    Agent: reuses agent_digest.kind (code-review, error-report, etc.)
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

_SOURCE_EXTS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt",
    ".rb", ".php", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".scala", ".m", ".mm", ".ex", ".exs", ".erl", ".dart", ".lua",
})
_DOC_EXTS = frozenset({".md", ".rst", ".txt", ".adoc", ".org"})
_DATA_EXTS = frozenset({".json", ".jsonl", ".csv", ".tsv", ".xml", ".parquet", ".ndjson"})
_CONFIG_EXTS = frozenset({".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".conf"})
_CONFIG_NAMES = frozenset({".env", ".envrc", ".gitignore", ".dockerignore"})
_BUILD_NAMES = frozenset({
    "Makefile", "Dockerfile", "pyproject.toml", "setup.py", "setup.cfg",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "requirements.txt",
    "Pipfile", "Pipfile.lock", "poetry.lock", "build.gradle", "pom.xml",
})
_TEST_DIR_HINTS = ("/tests/", "/test/", "/__tests__/", "/spec/", "/specs/")
_TEST_NAME_PREFIXES = ("test_",)
_TEST_NAME_SUFFIXES = ("_test.py", "_test.go", ".test.ts", ".test.tsx", ".test.js",
                       ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx")


def classify_read(path: str) -> str:
    """Best-effort intent label for a Read call. Never raises."""
    if not path:
        return "other"
    try:
        p = PurePosixPath(path.replace(os.sep, "/"))
        name = p.name
        ext = p.suffix.lower()
        low = str(p).lower()
    except Exception:
        return "other"

    if name in _BUILD_NAMES:
        return "build"
    if name in _CONFIG_NAMES or name.startswith(".env."):
        return "config"
    if (
        any(hint in low for hint in _TEST_DIR_HINTS)
        or any(name.startswith(p) for p in _TEST_NAME_PREFIXES)
        or any(name.endswith(s) for s in _TEST_NAME_SUFFIXES)
    ):
        return "test"
    if ext in _SOURCE_EXTS:
        return "source_code"
    if ext in _DOC_EXTS:
        return "doc"
    if ext in _CONFIG_EXTS:
        return "config"
    if ext in _DATA_EXTS:
        return "data"
    return "other"
