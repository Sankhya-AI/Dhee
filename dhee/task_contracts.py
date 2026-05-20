"""Deterministic task contracts and Chotu action plans.

Dhee's compiler role is to turn a noisy user request plus repo state into a
bounded, machine-checkable action contract.  This module is intentionally
heuristic and deterministic: it does not ask an LLM to write a plan.
"""

from __future__ import annotations

import hashlib
import ast
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dhee import repo_intelligence as repo_brain_mod
from dhee import repo_link


TASK_CONTRACT_SCHEMA = "dhee.task_contract.v1"
ACTION_PLAN_SCHEMA = "dhee.chotu_action_plan.v1"
ACTION_BYTECODE_SCHEMA = "dhee.chotu_action_bytecode.v1"
CONTRACT_COMPILER_SCHEMA = "dhee.contract_compiler.v1"
CONTEXT_LEDGER_SCHEMA = "dhee.context_ledger.v1"
REPO_INTELLIGENCE_SCHEMA = "dhee.repo_intelligence.v1"
VERIFICATION_CARD_SCHEMA = "dhee.verification_card.v1"
CONTAMINATION_STATUS_SCHEMA = "dhee.contamination_status.v1"
TASK_INTERPRETATION_SCHEMA = "dhee.task_contract_interpretation.v1"
TASK_CONTRACT_KIND = "task_contract"

ACTION_TYPES = {
    "READ_FILE",
    "SEARCH_CODE",
    "LSP_SYMBOL",
    "RUN_TEST",
    "EDIT_FILE",
    "ASK_USER",
    "SPAWN_SUBAGENT",
    "WRITE_MEMORY_NOTE",
    "SUBMIT_PATCH",
}

DEFAULT_CONTEXT_BUDGET = {
    "state_card_tokens": 1500,
    "retrieved_memory_tokens": 3000,
    "repo_context_tokens": 6000,
    "tool_output_tokens": 2000,
}
DEFAULT_FORBIDDEN_PATHS = [".env", ".env.*", "secrets/", "prod-config/"]
DEFAULT_FORBIDDEN_ACTIONS = [
    "git reset --hard",
    "git checkout --",
    "rm -rf",
    "write secrets",
    "edit generated capsule imports without request",
]
DEFAULT_SUCCESS_CRITERIA = [
    "target tests pass",
    "no unrelated files changed",
    "diff is reviewable",
    "memory note created if failure pattern is reusable",
]
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
_LOCAL_PATH_RE = re.compile(r"(/Users/[^\s\"']+|/home/[^\s\"']+|[A-Za-z]:\\\\[^\s\"']+)")
_EXCLUDED_DIRS = {
    ".git",
    ".dhee",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    ".direnv",
    ".tox",
    ".venv",
    ".venv-dhee",
    ".venv-dhee-full",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".next",
    "site-packages",
}
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "bug",
    "fix",
    "for",
    "in",
    "is",
    "it",
    "of",
    "on",
    "please",
    "test",
    "tests",
    "the",
    "this",
    "to",
    "with",
}
_ACTION_OPERAND_FIELDS = (
    "path",
    "command",
    "query",
    "scope",
    "symbol",
    "question",
    "role",
    "task",
    "category",
    "summary",
)
_ACTION_SEMANTICS = {
    "SEARCH_CODE": {
        "phase": "discover",
        "op": "repo.search",
        "capabilities": ["repo.search"],
        "effects": ["context.matches_observed"],
    },
    "READ_FILE": {
        "phase": "inspect",
        "op": "fs.read",
        "capabilities": ["fs.read"],
        "effects": ["context.file_observed"],
    },
    "LSP_SYMBOL": {
        "phase": "inspect",
        "op": "lsp.symbol",
        "capabilities": ["lsp.lookup"],
        "effects": ["context.symbol_observed"],
    },
    "RUN_TEST": {
        "phase": "verify",
        "op": "shell.test",
        "capabilities": ["shell.test"],
        "effects": ["verification.test_observed"],
    },
    "EDIT_FILE": {
        "phase": "mutate",
        "op": "fs.patch",
        "capabilities": ["fs.write"],
        "effects": ["repo.diff_mutated"],
    },
    "ASK_USER": {
        "phase": "clarify",
        "op": "user.ask",
        "capabilities": ["user.ask"],
        "effects": ["context.user_input_observed"],
    },
    "SPAWN_SUBAGENT": {
        "phase": "delegate",
        "op": "agent.spawn",
        "capabilities": ["agent.spawn"],
        "effects": ["context.parallel_result_observed"],
    },
    "WRITE_MEMORY_NOTE": {
        "phase": "learn",
        "op": "memory.write",
        "capabilities": ["memory.write"],
        "effects": ["memory.lesson_recorded"],
    },
    "SUBMIT_PATCH": {
        "phase": "submit",
        "op": "patch.submit",
        "capabilities": ["patch.submit"],
        "effects": ["handoff.patch_submitted"],
    },
}
_COMPILER_PASSES = [
    {"name": "issue_parse", "kind": "analysis", "purpose": "Normalize the user issue into goal, constraints, and ambiguity signals."},
    {"name": "repo_index", "kind": "analysis", "purpose": "Build a git-SHA scoped repo brain: symbols, imports, tests, dependencies, and risk signals."},
    {"name": "env_probe", "kind": "analysis", "purpose": "Infer deterministic setup and execution commands without running arbitrary code."},
    {"name": "test_discovery", "kind": "analysis", "purpose": "Select fail-to-pass, pass-to-pass, nearest, smoke, static, and security checks."},
    {"name": "localization", "kind": "analysis", "purpose": "Localize candidate files and symbols with evidence pointers and confidence."},
    {"name": "context_pack", "kind": "budgeting", "purpose": "Compile ranked context items with why, pointer, token cost, freshness, confidence, and expected utility."},
    {"name": "patch_strategy", "kind": "planning", "purpose": "Emit allowed patch families and edit proof obligations, not free-form plans."},
    {"name": "verification_plan", "kind": "verification", "purpose": "Create a VerificationCard the runtime can check before submit."},
    {"name": "replay_plan", "kind": "verification", "purpose": "Define branchable checkpoints for localization, edit, failed-test, and submit boundaries."},
    {"name": "memory_policy", "kind": "safety", "purpose": "Permit pointer-backed lessons only after verification and contamination checks."},
]


def _stable_hash(data: Any, length: int = 16) -> str:
    raw = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, default=str)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_text(text: str) -> str:
    value = str(text or "")
    home = str(Path.home())
    if home:
        value = value.replace(home, "$HOME")
    value = _LOCAL_PATH_RE.sub("<local-path>", value)
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("<redacted-secret>", value)
    return value


def _sanitize_obj(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_obj(item) for key, item in value.items()}
    return value


def _resolve_repo_root(repo: str | os.PathLike[str] | None) -> Path:
    base = Path(repo or os.getcwd()).expanduser().resolve()
    proc = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve()
    return base


def _git_out(repo_root: Path, args: Sequence[str], default: str = "") -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return default
    return proc.stdout.strip()


def _repo_slug(repo_root: Path) -> str:
    remote = _git_out(repo_root, ["remote", "get-url", "origin"], default="")
    if remote:
        value = remote.rstrip("/")
        value = re.sub(r"\.git$", "", value)
        if ":" in value and "/" in value:
            value = value.split(":", 1)[1]
        else:
            parts = value.split("/")
            value = "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        if value:
            return value
    return repo_root.name


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    for token in re.findall(r"[A-Za-z0-9_]+", str(text or "").lower()):
        if len(token) < 3 or token in _STOP_WORDS:
            continue
        if token not in out:
            out.append(token)
    return out


def _path_tokens(path: str) -> str:
    return str(path).replace("_", " ").replace("-", " ").replace("/", " ").lower()


def _branch_state(repo_root: Path) -> Dict[str, Any]:
    status = _git_out(repo_root, ["status", "--porcelain=v1", "--untracked-files=all"], default="")
    staged: List[str] = []
    unstaged: List[str] = []
    untracked: List[str] = []
    changed: List[str] = []
    for line in status.splitlines():
        if not line:
            continue
        code = line[:2]
        path = (line[3:] if len(line) > 2 and line[2] == " " else line[2:]).strip()
        if " -> " in path:
            _old, path = path.split(" -> ", 1)
        changed.append(path)
        if code.startswith("??"):
            untracked.append(path)
        else:
            if code[0] != " ":
                staged.append(path)
            if len(code) > 1 and code[1] != " ":
                unstaged.append(path)
    return {
        "branch": _git_out(repo_root, ["branch", "--show-current"], default=""),
        "head_commit": _git_out(repo_root, ["rev-parse", "--short", "HEAD"], default=""),
        "dirty": bool(changed),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "changed_paths": sorted(set(changed)),
    }


def _iter_repo_files(repo_root: Path, limit: int = 4_000) -> List[str]:
    listed = _git_out(repo_root, ["ls-files", "--cached", "--others", "--exclude-standard"], default="")
    if listed:
        files: List[str] = []
        for raw in listed.splitlines():
            rel = raw.strip().replace(os.sep, "/")
            if not rel or any(_is_excluded_dir(part) for part in Path(rel).parts[:-1]):
                continue
            files.append(rel)
            if len(files) >= limit:
                return sorted(files)
        return sorted(files)
    files: List[str] = []
    for root, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_excluded_dir(name)
        ]
        for filename in filenames:
            path = Path(root) / filename
            try:
                rel = os.path.relpath(path, repo_root).replace(os.sep, "/")
            except ValueError:
                continue
            files.append(rel)
            if len(files) >= limit:
                return sorted(files)
    return sorted(files)


def _is_excluded_dir(name: str) -> bool:
    return (
        name in _EXCLUDED_DIRS
        or name.endswith(".egg-info")
        or name.startswith(".venv")
        or name.startswith("venv-")
    )


def _score_file(path: str, tokens: Sequence[str]) -> int:
    haystack = _path_tokens(path)
    name = _path_tokens(Path(path).name)
    score = 0
    for token in tokens:
        if token in name:
            score += 4
        elif token in haystack:
            score += 2
    if path.startswith("tests/"):
        score += 1
    return score


def _relevant_files(repo_root: Path, goal: str, branch_state: Dict[str, Any], limit: int = 12) -> List[str]:
    tokens = _tokens(goal)
    scored: List[Tuple[int, str]] = []
    for path in _iter_repo_files(repo_root):
        score = _score_file(path, tokens)
        if score > 0:
            scored.append((score, path))
    for path in branch_state.get("changed_paths") or []:
        if path and not str(path).startswith(".dhee/"):
            scored.append((100, str(path)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    out: List[str] = []
    for _score, path in scored:
        if path not in out:
            out.append(path)
        if len(out) >= limit:
            break
    return out


def _affected_modules(relevant_files: Sequence[str], branch_state: Dict[str, Any]) -> List[str]:
    modules: List[str] = []
    for path in list(relevant_files) + list(branch_state.get("changed_paths") or []):
        if not path or str(path).startswith(".dhee/"):
            continue
        parts = Path(path).parts
        if len(parts) > 1:
            module = "/".join(parts[:2]) if parts[0] not in {"tests", "docs"} else parts[0]
        else:
            module = parts[0]
        if module not in modules:
            modules.append(module)
    return modules[:12]


def _known_architecture(repo_root: Path) -> Dict[str, Any]:
    files = set(_iter_repo_files(repo_root, limit=1_000))
    package_roots = [
        path
        for path in sorted({item.split("/", 1)[0] for item in files if "/" in item})
        if (repo_root / path / "__init__.py").exists()
    ]
    entrypoints = [
        path
        for path in ("dhee/mcp_server.py", "dhee/mcp_slim.py", "dhee/cli.py", "pyproject.toml", "package.json")
        if (repo_root / path).exists()
    ]
    return {
        "language": "python" if "pyproject.toml" in files or any(item.endswith(".py") for item in files) else "unknown",
        "test_framework": "pytest" if "pytest.ini" in files or "pyproject.toml" in files or any(item.startswith("tests/test_") for item in files) else "unknown",
        "package_roots": package_roots[:8],
        "entrypoints": entrypoints,
    }


def _infer_test_commands(repo_root: Path, goal: str, relevant_files: Sequence[str], must_run: Optional[Iterable[str]]) -> List[str]:
    if must_run:
        return [str(cmd) for cmd in must_run if str(cmd).strip()]
    tokens = _tokens(goal)
    files = _iter_repo_files(repo_root, limit=4_000)
    commands: List[str] = []
    for path in files:
        if not path.startswith("tests/") or not path.endswith(".py"):
            continue
        if any(token in _path_tokens(path) for token in tokens):
            commands.append(f"pytest {path}")
    for rel in relevant_files:
        stem = Path(rel).stem
        if not stem or stem == "__init__":
            continue
        expected = f"tests/test_{stem}.py"
        if expected in files:
            command = f"pytest {expected}"
            if command not in commands:
                commands.append(command)
    if not commands:
        commands.append("pytest")
    return commands[:6]


def _default_allowed_write_paths(repo_root: Path, affected_modules: Sequence[str]) -> List[str]:
    paths: List[str] = []
    for module in affected_modules:
        first = module.split("/", 1)[0]
        candidate = f"{first}/" if (repo_root / first).is_dir() else module
        if candidate not in paths and not candidate.startswith(".dhee"):
            paths.append(candidate)
    if (repo_root / "tests").is_dir() and "tests/" not in paths:
        paths.append("tests/")
    if not paths:
        if (repo_root / "dhee").is_dir():
            paths.append("dhee/")
        if (repo_root / "tests").is_dir():
            paths.append("tests/")
    return paths or ["."]


def _infer_risk(goal: str, branch_state: Dict[str, Any], recent_failures: Sequence[Dict[str, Any]]) -> str:
    risky = {"auth", "security", "secret", "token", "migration", "database", "prod", "delete", "billing"}
    tokens = set(_tokens(goal))
    if tokens & risky:
        return "high"
    if branch_state.get("dirty") or recent_failures:
        return "medium"
    return "low"


def _repo_memory_pointers(repo_root: Path, goal: str, explicit: Optional[Iterable[Dict[str, Any]]] = None, limit: int = 8) -> List[Dict[str, Any]]:
    if explicit is not None:
        out: List[Dict[str, Any]] = []
        for item in explicit:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            enriched.setdefault("why_included", "Explicitly supplied to the task compiler.")
            enriched.setdefault("evidence_pointer", enriched.get("ref") or enriched.get("id") or "explicit")
            enriched.setdefault("token_cost", _estimate_tokens(enriched.get("title") or enriched.get("content") or ""))
            enriched.setdefault("freshness", "explicit")
            enriched.setdefault("confidence", 0.8)
            enriched.setdefault("expected_utility", 0.7)
            out.append(enriched)
            if len(out) >= limit:
                break
        return out
    tokens = _tokens(goal)
    pointers: List[Dict[str, Any]] = []
    try:
        entries = repo_link.list_entries(repo_root)
    except Exception:
        entries = []
    for entry in reversed(entries):
        text = f"{entry.kind} {entry.title} {entry.content}".lower()
        if tokens and not any(token in text for token in tokens):
            continue
        pointers.append({
            "kind": entry.kind,
            "id": entry.id,
            "title": entry.title,
            "ref": f"repo_context:{entry.id}",
            "evidence_pointer": f"repo_context:{entry.id}",
            "content_hash": entry.content_hash,
            "why_included": "Repo-shared context matched the compiled task tokens.",
            "token_cost": _estimate_tokens(f"{entry.title}\n{entry.content}"),
            "freshness": getattr(entry, "updated_at", None) or getattr(entry, "created_at", None) or "unknown",
            "confidence": 0.72,
            "expected_utility": 0.68,
        })
        if len(pointers) >= limit:
            break
    return pointers


def _estimate_tokens(value: Any) -> int:
    text = str(value or "")
    if not text:
        return 0
    return max(1, int(len(text) / 3.8))


def _repo_brain_root(repo_root: Path) -> Path:
    return repo_link.repo_context_dir(repo_root) / "repo_brain"


def _python_symbol_index(repo_root: Path, files: Sequence[str]) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]], List[Dict[str, Any]]]:
    symbols: List[Dict[str, Any]] = []
    imports: Dict[str, List[str]] = {}
    call_edges: List[Dict[str, Any]] = []
    for rel in files:
        if not str(rel).endswith(".py"):
            continue
        path = repo_root / rel
        if not path.exists() or path.stat().st_size > 512_000:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        imported: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append({
                    "path": rel,
                    "name": node.name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line": getattr(node, "lineno", 0),
                })
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        target = ""
                        if isinstance(child.func, ast.Name):
                            target = child.func.id
                        elif isinstance(child.func, ast.Attribute):
                            target = child.func.attr
                        if target:
                            call_edges.append({
                                "path": rel,
                                "caller": node.name,
                                "callee": target,
                                "line": getattr(child, "lineno", 0),
                            })
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names if alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = "." * int(node.level or 0) + str(node.module or "")
                if module:
                    imported.append(module)
        if imported:
            imports[rel] = sorted(set(imported))
    return symbols[:500], imports, call_edges[:1_000]


def _test_map(files: Sequence[str], relevant_files: Sequence[str], must_run: Sequence[str]) -> Dict[str, Any]:
    tests = [path for path in files if str(path).startswith("tests/") and str(path).endswith(".py")]
    source_to_tests: Dict[str, List[str]] = {}
    for rel in relevant_files:
        stem = Path(rel).stem
        candidates = [test for test in tests if stem and stem in _path_tokens(test)]
        if candidates:
            source_to_tests[rel] = candidates[:8]
    return {
        "tests": tests[:300],
        "source_to_tests": source_to_tests,
        "must_run": list(must_run),
    }


def _setup_commands(files: Sequence[str]) -> List[str]:
    commands: List[str] = []
    file_set = set(files)
    if "pyproject.toml" in file_set or "setup.py" in file_set:
        commands.append('pip install -e ".[dev]"')
    if "requirements.txt" in file_set:
        commands.append("pip install -r requirements.txt")
    if "package.json" in file_set:
        commands.append("npm install")
    return commands[:6]


def _risky_files(files: Sequence[str]) -> List[Dict[str, Any]]:
    risky_names = ("auth", "secret", "token", "security", "migration", "payment", "billing", "prod", "config")
    out: List[Dict[str, Any]] = []
    for rel in files:
        lower = str(rel).lower()
        reasons = [name for name in risky_names if name in lower]
        if reasons:
            out.append({"path": rel, "reasons": reasons})
        if len(out) >= 80:
            break
    return out


def _historical_failure_signatures(repo_root: Path, goal: str, limit: int = 20) -> List[Dict[str, Any]]:
    tokens = _tokens(goal)
    try:
        entries = repo_link.list_entries(repo_root)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for entry in reversed(entries):
        text = f"{entry.kind} {entry.title} {entry.content}".lower()
        if "fail" not in text and "error" not in text and "regression" not in text:
            continue
        if tokens and not any(token in text for token in tokens):
            continue
        out.append({
            "ref": f"repo_context:{entry.id}",
            "title": entry.title,
            "kind": entry.kind,
            "content_hash": entry.content_hash,
        })
        if len(out) >= limit:
            break
    return out


def _compile_repo_intelligence(
    repo_root: Path,
    *,
    goal: str,
    relevant_files: Sequence[str],
    must_run: Sequence[str],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    compiled = repo_brain_mod.compile_repo_intelligence(
        repo_root,
        goal=goal,
        relevant_files=relevant_files,
        must_run=must_run,
    )
    return (
        dict(compiled.get("repo_intelligence") or {}),
        dict(compiled.get("repo_brain") or {}),
        dict(compiled.get("localization") or {}),
    )


def _merge_relevant_files(initial: Sequence[str], localization: Dict[str, Any], limit: int = 16) -> List[str]:
    merged: List[str] = []
    for item in localization.get("candidate_files") or []:
        path = str(item.get("path") or "")
        if path and path not in merged:
            merged.append(path)
    for path in initial:
        if path and path not in merged:
            merged.append(str(path))
    return merged[:limit]


def _merge_test_commands(initial: Sequence[str], localization: Dict[str, Any], limit: int = 8) -> List[str]:
    merged: List[str] = []
    for command in initial:
        if command and str(command) not in merged:
            merged.append(str(command))
    for item in localization.get("candidate_tests") or []:
        command = str(item.get("command") or "")
        if command and command not in merged:
            merged.append(command)
    return merged[:limit]


def _context_item(
    *,
    kind: str,
    title: str,
    evidence_pointer: str,
    why_included: str,
    token_cost: int,
    freshness: str,
    confidence: float,
    expected_utility: float,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "evidence_pointer": evidence_pointer,
        "why_included": why_included,
        "token_cost": int(token_cost),
        "freshness": freshness,
        "confidence": round(float(confidence), 3),
        "expected_utility": round(float(expected_utility), 3),
        "metadata": metadata or {},
    }


def _context_ledger(contract: Dict[str, Any], repo_intelligence: Dict[str, Any]) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    branch = contract.get("current_branch_state") or {}
    items.append(_context_item(
        kind="branch_state",
        title=f"Branch {branch.get('branch') or '(detached)'} at {branch.get('head_commit') or 'unknown'}",
        evidence_pointer="git:branch_state",
        why_included="Every supervised action must know the branch, dirty state, and rollback point.",
        token_cost=_estimate_tokens(branch),
        freshness=str(contract.get("created_at") or "compile_time"),
        confidence=0.95,
        expected_utility=0.9,
        metadata={"dirty": bool(branch.get("dirty")), "changed_paths": branch.get("changed_paths") or []},
    ))
    items.append(_context_item(
        kind="repo_intelligence",
        title="Git-SHA scoped repo brain",
        evidence_pointer=str(repo_intelligence.get("ref") or ""),
        why_included="Symbols, imports, test map, dependencies, risky files, and failure signatures constrain localization.",
        token_cost=_estimate_tokens(repo_intelligence),
        freshness=f"head:{repo_intelligence.get('head_commit') or 'unknown'}",
        confidence=0.82,
        expected_utility=0.86,
        metadata={key: repo_intelligence.get(key) for key in ("symbol_count", "test_count", "risky_file_count", "historical_failure_count")},
    ))
    localization = contract.get("localization") or {}
    if localization:
        items.append(_context_item(
            kind="localization",
            title=f"Localization {localization.get('status') or 'unknown'}",
            evidence_pointer=str(repo_intelligence.get("ref") or "repo_brain:latest"),
            why_included="Multi-signal localization ranks candidate files, symbols, tests, and evidence pointers before edit planning.",
            token_cost=_estimate_tokens(localization),
            freshness=f"head:{repo_intelligence.get('head_commit') or 'unknown'}",
            confidence=float(localization.get("confidence") or 0.0),
            expected_utility=0.9 if localization.get("status") == "localized" else 0.55,
            metadata={
                "engine": localization.get("engine"),
                "candidate_file_count": len(localization.get("candidate_files") or []),
                "candidate_test_count": len(localization.get("candidate_tests") or []),
            },
        ))
    for path in contract.get("relevant_files") or []:
        localized = next(
            (
                item for item in localization.get("candidate_files") or []
                if str(item.get("path") or "") == str(path)
            ),
            {},
        )
        items.append(_context_item(
            kind="file",
            title=str(path),
            evidence_pointer=f"repo_file:{path}",
            why_included="Localized by issue tokens, symbols, dirty state, historical failures, or source-to-test mapping.",
            token_cost=_estimate_tokens(path),
            freshness=f"head:{branch.get('head_commit') or 'unknown'}",
            confidence=float(localized.get("confidence") or 0.74),
            expected_utility=0.82 if localized else 0.72,
            metadata={
                "allowed_write": _path_under_allowed(str(path), contract.get("allowed_write_paths") or []),
                "localization_score": localized.get("score"),
                "localization_reasons": localized.get("reasons") or [],
            },
        ))
    for command in contract.get("must_run") or []:
        items.append(_context_item(
            kind="test_command",
            title=str(command),
            evidence_pointer=f"command:{_stable_hash(command, 10)}",
            why_included="Required verifier command compiled from the issue and nearby tests.",
            token_cost=_estimate_tokens(command),
            freshness="compile_time",
            confidence=0.8,
            expected_utility=0.88,
            metadata={"must_run": True},
        ))
    for pointer in contract.get("memory_pointers") or []:
        items.append(_context_item(
            kind=f"memory:{pointer.get('kind') or 'repo_context'}",
            title=str(pointer.get("title") or pointer.get("id") or "memory pointer"),
            evidence_pointer=str(pointer.get("evidence_pointer") or pointer.get("ref") or pointer.get("id") or ""),
            why_included=str(pointer.get("why_included") or "Matched task tokens."),
            token_cost=int(pointer.get("token_cost") or 1),
            freshness=str(pointer.get("freshness") or "unknown"),
            confidence=float(pointer.get("confidence") or 0.6),
            expected_utility=float(pointer.get("expected_utility") or 0.5),
            metadata={"content_hash": pointer.get("content_hash")},
        ))
    return {
        "schema_version": CONTEXT_LEDGER_SCHEMA,
        "budget": contract.get("context_budget") or {},
        "total_token_cost": sum(int(item.get("token_cost") or 0) for item in items),
        "items": sorted(items, key=lambda item: (-float(item.get("expected_utility") or 0), int(item.get("token_cost") or 0))),
        "policy": {
            "top_k_memory_injection": False,
            "raw_evidence_expansion": "by_pointer_only",
            "include_requires_why_and_pointer": True,
        },
    }


def _verification_card(
    contract: Dict[str, Any],
    repo_intelligence: Dict[str, Any],
    repo_brain: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if repo_brain:
        return repo_brain_mod.verification_card_from_brain(
            contract,
            repo_brain,
            contract.get("localization") or {},
        )
    relevant_tests = [
        path for path in contract.get("relevant_files") or []
        if str(path).startswith("tests/") and str(path).endswith(".py")
    ]
    nearest_tests = sorted(set(relevant_tests + (repo_intelligence.get("test_map") or {}).get("tests", [])[:6]))
    smoke_targets = [
        path for path in contract.get("relevant_files") or []
        if str(path).endswith(".py") and not str(path).startswith("tests/")
    ][:8]
    import_smoke = [f"python3 -m py_compile {' '.join(smoke_targets)}"] if smoke_targets else []
    public_api_risk = "medium" if any(Path(str(path)).name == "__init__.py" for path in contract.get("relevant_files") or []) else "low"
    risky_paths = {item.get("path") for item in (repo_intelligence.get("risky_files") or [])}
    diff_risk = "high" if any(path in risky_paths for path in contract.get("relevant_files") or []) else contract.get("risk", "medium")
    failure_text = json.dumps(contract.get("recent_failures") or [], sort_keys=True, default=str)
    return {
        "schema_version": VERIFICATION_CARD_SCHEMA,
        "fail_to_pass_tests": list(contract.get("must_run") or []),
        "pass_to_pass_tests": [cmd for cmd in list(contract.get("must_run") or []) if cmd not in failure_text],
        "nearest_tests": nearest_tests[:12],
        "import_smoke_tests": import_smoke,
        "static_checks": import_smoke,
        "security_checks": [
            "verify no forbidden path changed",
            "verify no secret-like token introduced",
            "verify benchmark contamination status is clean before submit",
        ],
        "diff_risk": diff_risk,
        "public_api_risk": public_api_risk,
        "submit_requirements": [
            "all fail_to_pass_tests observed as passed",
            "edit proof obligations satisfied for every EDIT_FILE",
            "contamination status is clean or explicitly quarantined",
            "replay checkpoint exists before submit",
        ],
    }


def _contamination_status(goal: str, memory_pointers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    text = str(goal or "").lower()
    benchmark_mode = bool(os.environ.get("DHEE_BENCHMARK_MODE")) or any(token in text for token in ("swe-bench", "swe bench", "benchmark", "eval"))
    risky_refs = [
        pointer for pointer in memory_pointers
        if any(marker in f"{pointer.get('title')} {pointer.get('kind')}".lower() for marker in ("gold", "solution", "hidden test", "eval"))
    ]
    status = "quarantined" if benchmark_mode and risky_refs else "clean"
    return {
        "schema_version": CONTAMINATION_STATUS_SCHEMA,
        "benchmark_mode": benchmark_mode,
        "status": status,
        "rules": [
            "no gold patches",
            "no hidden tests",
            "no issue-to-solution memory",
            "no prior evaluated solution recall",
            "all memories carry provenance",
            "eval memories are quarantined",
        ],
        "quarantined_refs": [pointer.get("evidence_pointer") or pointer.get("ref") for pointer in risky_refs],
    }


def _lifecycle(
    *,
    precondition: str,
    execution: Dict[str, Any],
    observation: str,
    postcondition: str,
    memory_update: str,
) -> Dict[str, Any]:
    return {
        "precondition": precondition,
        "execution": execution,
        "observation": observation,
        "postcondition": postcondition,
        "memory_update": memory_update,
    }


def _action(action_type: str, reason: str, lifecycle: Dict[str, Any], **payload: Any) -> Dict[str, Any]:
    return {
        "type": action_type,
        **payload,
        "reason": reason,
        **lifecycle,
    }


def _action_operands(action: Dict[str, Any]) -> Dict[str, Any]:
    operands: Dict[str, Any] = {}
    for field in _ACTION_OPERAND_FIELDS:
        value = action.get(field)
        if value not in (None, "", [], {}):
            operands[field] = value
    if action.get("timeout_sec") and action.get("type") == "RUN_TEST":
        operands["timeout_sec"] = action.get("timeout_sec")
    return operands


def _action_target(action: Dict[str, Any]) -> str:
    operands = _action_operands(action)
    for field in ("path", "command", "query", "symbol", "question", "category", "summary"):
        if operands.get(field):
            return str(operands[field])
    return str(action.get("type") or "")


def _action_id(action: Dict[str, Any], index: int) -> str:
    return "act_" + _stable_hash({
        "index": index,
        "type": action.get("type"),
        "operands": _action_operands(action),
    }, 14)


def _compile_action_bytecode(actions: Sequence[Dict[str, Any]], contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lower lifecycle actions into a tiny portable bytecode graph."""

    lowered: List[Dict[str, Any]] = []
    for index, raw in enumerate(actions, start=1):
        action = dict(raw)
        action_type = str(action.get("type") or "")
        semantics = _ACTION_SEMANTICS.get(action_type, {})
        operands = _action_operands(action)
        action_id = str(action.get("action_id") or _action_id(action, index))
        arg_hash = _stable_hash({"type": action_type, "operands": operands}, 12)
        action.update({
            "step": index,
            "action_id": action_id,
            "phase": semantics.get("phase", "unknown"),
            "capabilities": list(semantics.get("capabilities") or []),
            "effects": list(semantics.get("effects") or []),
            "operands": operands,
            "requires": [],
            "soft_requires": [],
            "bytecode": {
                "schema_version": ACTION_BYTECODE_SCHEMA,
                "op": semantics.get("op", action_type.lower()),
                "arg_hash": arg_hash,
                "operands": operands,
                "requires": [],
                "soft_requires": [],
                "emits": list(semantics.get("effects") or []),
            },
        })
        lowered.append(action)

    first_search_id = next((action["action_id"] for action in lowered if action.get("type") == "SEARCH_CODE"), None)
    read_by_path = {
        str(action.get("path")): action["action_id"]
        for action in lowered
        if action.get("type") == "READ_FILE" and action.get("path")
    }
    run_test_ids = [
        action["action_id"]
        for action in lowered
        if action.get("type") == "RUN_TEST"
    ]

    for action in lowered:
        hard: List[str] = []
        soft: List[str] = []
        action_type = action.get("type")
        if action_type == "READ_FILE" and first_search_id:
            soft.append(first_search_id)
        elif action_type == "EDIT_FILE":
            read_id = read_by_path.get(str(action.get("path") or ""))
            if read_id:
                hard.append(read_id)
        elif action_type == "RUN_TEST":
            for path in contract.get("relevant_files") or []:
                read_id = read_by_path.get(str(path))
                if read_id and read_id not in soft:
                    soft.append(read_id)
        elif action_type == "WRITE_MEMORY_NOTE":
            soft.extend(run_test_ids)
        elif action_type == "SUBMIT_PATCH":
            hard.extend(run_test_ids)
        action["requires"] = hard
        action["soft_requires"] = soft
        action["bytecode"]["requires"] = hard
        action["bytecode"]["soft_requires"] = soft

    return lowered


def _compiler_manifest(contract: Dict[str, Any], actions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    action_digest = _stable_hash([
        {
            "action_id": action.get("action_id"),
            "type": action.get("type"),
            "operands": action.get("operands") or _action_operands(action),
            "requires": action.get("requires") or [],
        }
        for action in actions
    ], 20)
    return {
        "schema_version": CONTRACT_COMPILER_SCHEMA,
        "compiler": "dhee.context-compiler",
        "version": 1,
        "deterministic": True,
        "source_language": "messy_user_task+repo_state",
        "target_runtime": "dhee.contract_supervisor",
        "passes": list(_COMPILER_PASSES),
        "artifact_hash": action_digest,
        "constraints": {
            "auto_execute": False,
            "validate_before_execute": True,
            "raw_evidence_by_pointer": True,
            "personal_context_bodies_excluded": True,
        },
        "stats": {
            "action_count": len(actions),
            "hard_dependency_edges": sum(len(action.get("requires") or []) for action in actions),
            "soft_dependency_edges": sum(len(action.get("soft_requires") or []) for action in actions),
            "must_run_count": len(contract.get("must_run") or contract.get("test_commands") or []),
        },
    }


def _compile_actions(contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    goal = contract["goal"]
    repo_root = contract["repo_root"]
    relevant_files = contract.get("relevant_files") or []
    must_run = contract.get("must_run") or []
    tokens = _tokens(goal)
    query = " ".join(tokens) if tokens else goal
    actions: List[Dict[str, Any]] = [
        _action(
            "SEARCH_CODE",
            "Locate implementation and tests before reading or editing.",
            _lifecycle(
                precondition="Repo is available and readable.",
                execution={"tool": "dhee_grep", "query": query, "scope": repo_root},
                observation="Compact match summary plus pointer to full results.",
                postcondition="Relevant implementation and test files are identified or the task is marked under-specified.",
                memory_update="Do not write memory yet; only note a reusable repo pattern after confirmation.",
            ),
            query=query,
            scope=".",
        )
    ]

    for path in relevant_files[:6]:
        actions.append(
            _action(
                "READ_FILE",
                "Read the concrete file before proposing edits.",
                _lifecycle(
                    precondition=f"`{path}` exists in the target repo.",
                    execution={"tool": "dhee_read", "path": path},
                    observation="Pointer-backed file excerpt with line references.",
                    postcondition="Relevant symbols, invariants, and edit boundaries are known.",
                    memory_update="No memory write unless the file reveals a reusable architecture rule.",
                ),
                path=path,
            )
        )

    if not relevant_files:
        actions.append(
            _action(
                "ASK_USER",
                "The compiler could not identify a bounded file/module scope.",
                _lifecycle(
                    precondition="Search produced no strong repo-local targets.",
                    execution={"prompt": "Ask for the failing command, file, or error message."},
                    observation="User supplies missing scope or confirms broad investigation.",
                    postcondition="Task can be recompiled with concrete targets.",
                    memory_update="Store nothing unless the missing-scope pattern repeats.",
                ),
                question="Which failing command, file, or error should Dhee target first?",
                blocking=True,
            )
        )

    for command in must_run[:6]:
        actions.append(
            _action(
                "RUN_TEST",
                "Classify current failure before and after edits.",
                _lifecycle(
                    precondition="Dependency environment exists and command is safe for the sandbox.",
                    execution={"tool": "dhee_bash", "command": command, "timeout_sec": 120},
                    observation="Compact failing stacktrace and pointer to full log.",
                    postcondition="Failure is classified as target failure, unrelated failure, or environment failure.",
                    memory_update="Store failure signature if it is reusable across future tasks.",
                ),
                command=command,
                timeout_sec=120,
            )
        )

    actions.append(
        _action(
            "WRITE_MEMORY_NOTE",
            "Capture reusable lessons without bloating the active prompt.",
            _lifecycle(
                precondition="A repeated failure pattern, architectural invariant, or repo workflow was confirmed.",
                execution={"category": "failure_pattern"},
                observation="Short note with evidence pointers, not raw logs.",
                postcondition="Future contracts can retrieve the lesson by pointer.",
                memory_update="Write compact lesson only after verification.",
            ),
            category="failure_pattern",
            content="If this task reveals a reusable failure signature, store the minimal signature plus test command and fix boundary.",
        )
    )
    actions.append(
        _action(
            "SUBMIT_PATCH",
            "Finish only after contract success criteria are satisfied.",
            _lifecycle(
                precondition="Edits are complete, tests have run, and unrelated diffs were avoided.",
                execution={"summary": "Summarize changed behavior and tests run."},
                observation="Reviewable patch summary with test results.",
                postcondition="User can review or ship the patch.",
                memory_update="Checkpoint decisions, files touched, and any reusable lesson pointers.",
            ),
            summary="Submit a scoped patch for the compiled task contract.",
            tests=must_run,
        )
    )
    return actions


def _task_contract_root(repo_root: Path) -> Path:
    return repo_link.repo_context_dir(repo_root) / "task_contracts"


def _safe_repo_path(repo_root: Path, rel_path: str) -> Optional[Path]:
    raw = Path(str(rel_path or ""))
    if not rel_path or raw.is_absolute() or ".." in raw.parts:
        return None
    try:
        root = repo_root.resolve()
        path = (root / raw).resolve()
        if os.path.commonpath([str(root), str(path)]) != str(root):
            return None
        return path
    except (OSError, ValueError):
        return None


def _is_forbidden_path(path: str, forbidden_paths: Iterable[str]) -> bool:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    for pattern in forbidden_paths or []:
        item = str(pattern or "").replace("\\", "/").lstrip("./")
        if not item:
            continue
        if item.endswith("/"):
            if normalized.startswith(item):
                return True
            continue
        if item.endswith(".*"):
            prefix = item[:-1]
            if normalized.startswith(prefix):
                return True
            continue
        if normalized == item or normalized.startswith(item.rstrip("/") + "/"):
            return True
    return False


def _path_under_allowed(path: str, allowed_paths: Iterable[str]) -> bool:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    allowed = [str(item or "").replace("\\", "/").lstrip("./") for item in allowed_paths or []]
    if not allowed or "." in allowed:
        return True
    for item in allowed:
        if not item:
            continue
        if item.endswith("/"):
            if normalized.startswith(item):
                return True
        elif normalized == item or normalized.startswith(item.rstrip("/") + "/"):
            return True
    return False


def _command_is_safe(command: str) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    lowered = text.lower()
    dangerous = ("rm -rf", "git reset --hard", "git checkout --", "sudo ", "curl | sh", "chmod 777")
    if any(item in lowered for item in dangerous):
        return False
    safe_prefixes = ("pytest", "python -m pytest", "python3 -m pytest", "npm test", "npm run test", "pnpm test", "uv run pytest")
    return lowered.startswith(safe_prefixes)


def _resolve_contract(compiled_or_contract: Dict[str, Any]) -> Dict[str, Any]:
    if "contract" in compiled_or_contract and "actions" in compiled_or_contract:
        compiled = dict(compiled_or_contract)
        contract = compiled.get("contract") or {}
        actions = compiled.get("actions") or []
        if actions and any(not action.get("action_id") or not action.get("bytecode") for action in actions if isinstance(action, dict)):
            compiled["actions"] = _compile_action_bytecode(actions, contract)
            compiled["actions_schema"] = ACTION_BYTECODE_SCHEMA
        if compiled.get("actions") and not compiled.get("compiler"):
            compiled["compiler"] = _compiler_manifest(contract, compiled.get("actions") or [])
        return compiled
    if compiled_or_contract.get("schema_version") == TASK_CONTRACT_SCHEMA:
        wrapper = {
            "format": "dhee_task_contract_compile.v1",
            "contract": compiled_or_contract,
            "actions_schema": ACTION_BYTECODE_SCHEMA,
            "actions": compiled_or_contract.get("actions") or [],
        }
        wrapper["actions"] = _compile_action_bytecode(wrapper["actions"], compiled_or_contract) if wrapper["actions"] else []
        if wrapper["actions"]:
            wrapper["compiler"] = _compiler_manifest(compiled_or_contract, wrapper["actions"])
        wrapper["validation"] = validate_task_contract(wrapper)
        return wrapper
    return compiled_or_contract


def compile_task_contract(
    goal: str,
    *,
    repo: str | os.PathLike[str] | None = None,
    mode: str = "patch",
    risk: Optional[str] = None,
    allowed_write_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
    must_run: Optional[Iterable[str]] = None,
    success_criteria: Optional[Iterable[str]] = None,
    context_budget: Optional[Dict[str, int]] = None,
    memory_pointers: Optional[Iterable[Dict[str, Any]]] = None,
    recent_failures: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compile a messy task request into a deterministic action contract."""

    if not str(goal or "").strip():
        raise ValueError("goal is required")
    repo_root = _resolve_repo_root(repo)
    branch_state = _branch_state(repo_root)
    initial_relevant_files = _relevant_files(repo_root, goal, branch_state)
    initial_test_commands = _infer_test_commands(repo_root, goal, initial_relevant_files, must_run)
    repo_intelligence, repo_brain, localization = _compile_repo_intelligence(
        repo_root,
        goal=str(goal).strip(),
        relevant_files=initial_relevant_files,
        must_run=initial_test_commands,
    )
    repo_intelligence["localization_status"] = localization.get("status")
    repo_intelligence["localization_confidence"] = localization.get("confidence")
    relevant_files = _merge_relevant_files(initial_relevant_files, localization)
    test_commands = initial_test_commands if must_run else _merge_test_commands(initial_test_commands, localization)
    affected_modules = _affected_modules(relevant_files, branch_state)
    failures = [dict(item) for item in (recent_failures or []) if isinstance(item, dict)]
    contract = {
        "schema_version": TASK_CONTRACT_SCHEMA,
        "task_id": "task_" + datetime.now(timezone.utc).strftime("%Y_%m_%d_") + _stable_hash({
            "goal": goal,
            "repo": str(repo_root),
            "head": branch_state.get("head_commit"),
        }, 8),
        "created_at": _now_iso(),
        "goal": str(goal).strip(),
        "repo": _repo_slug(repo_root),
        "repo_root": str(repo_root),
        "mode": mode or "patch",
        "risk": risk or _infer_risk(goal, branch_state, failures),
        "affected_modules": affected_modules,
        "known_architecture": _known_architecture(repo_root),
        "recent_failures": failures,
        "test_commands": test_commands,
        "relevant_files": relevant_files,
        "allowed_write_paths": list(allowed_write_paths or _default_allowed_write_paths(repo_root, affected_modules)),
        "forbidden_paths": list(forbidden_paths or DEFAULT_FORBIDDEN_PATHS),
        "forbidden_actions": DEFAULT_FORBIDDEN_ACTIONS,
        "success_criteria": list(success_criteria or DEFAULT_SUCCESS_CRITERIA),
        "rollback_plan": [
            "Review `git diff --stat` before and after edits.",
            "Keep edits inside allowed_write_paths.",
            "If tests regress outside target scope, stop and report the failing command.",
        ],
        "memory_pointers": _repo_memory_pointers(repo_root, goal, memory_pointers),
        "current_branch_state": branch_state,
        "context_budget": dict(context_budget or DEFAULT_CONTEXT_BUDGET),
    }
    contract["must_run"] = contract["test_commands"]
    contract["repo_intelligence"] = repo_intelligence
    contract["localization"] = localization
    contract["compiled_context"] = _context_ledger(contract, repo_intelligence)
    contract["verification_card"] = _verification_card(contract, repo_intelligence, repo_brain)
    contract["contamination_status"] = _contamination_status(contract["goal"], contract["memory_pointers"])
    contract["patch_families"] = [
        {
            "name": "minimal_fix",
            "intent": "Smallest behavior change that satisfies fail-to-pass tests.",
            "risk": "low",
            "requires_isolated_worktree": False,
        },
        {
            "name": "semantic_fix",
            "intent": "Correct the underlying invariant while preserving public API behavior.",
            "risk": "medium",
            "requires_isolated_worktree": False,
        },
        {
            "name": "edge_case_fix",
            "intent": "Address boundary conditions revealed by nearby tests or failure signatures.",
            "risk": "medium",
            "requires_isolated_worktree": False,
        },
        {
            "name": "regression_safe_fix",
            "intent": "Prefer broader pass-to-pass verification before submit.",
            "risk": "medium",
            "requires_isolated_worktree": True,
        },
        {
            "name": "alternative_hypothesis",
            "intent": "Branch and test a competing localization only when evidence is weak.",
            "risk": "high",
            "requires_isolated_worktree": True,
        },
    ]
    contract["edit_proof_obligations"] = [
        "file was read",
        "edit span localized",
        "invariant stated",
        "related test selected",
        "rollback point exists",
    ]
    contract["replay_plan"] = {
        "checkpoints": ["after_localization", "before_edit", "after_failing_test", "before_submit"],
        "storage": ".dhee/context/task_runs/<task_id>/checkpoints/",
        "failed_attempts_are_assets": True,
    }
    contract["memory_policy"] = {
        "generic_memory_injection": False,
        "survived_lessons_only": True,
        "raw_logs_by_pointer_only": True,
        "skill_promotion_requires_ab_test": True,
    }
    actions = _compile_action_bytecode(_compile_actions(contract), contract)
    validation = validate_task_contract({"contract": contract, "actions": actions})
    return {
        "format": "dhee_task_contract_compile.v1",
        "contract": contract,
        "compiler": _compiler_manifest(contract, actions),
        "actions_schema": ACTION_BYTECODE_SCHEMA,
        "actions": actions,
        "validation": validation,
    }


def _write_task_contract(compiled: Dict[str, Any], out_dir: Path) -> Dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = _sanitize_obj(compiled)
    md = _sanitize_text(render_task_contract(data))
    json_path = out_dir / "contract.json"
    md_path = out_dir / "contract.md"
    json_path.write_text(_json_dumps(data) + "\n", encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path), "dir": str(out_dir)}


def create_task_contract(
    goal: str,
    *,
    repo: str | os.PathLike[str] | None = None,
    out: str | os.PathLike[str] | None = None,
    mode: str = "patch",
    risk: Optional[str] = None,
    allowed_write_paths: Optional[Iterable[str]] = None,
    forbidden_paths: Optional[Iterable[str]] = None,
    must_run: Optional[Iterable[str]] = None,
    success_criteria: Optional[Iterable[str]] = None,
    context_budget: Optional[Dict[str, int]] = None,
    memory_pointers: Optional[Iterable[Dict[str, Any]]] = None,
    recent_failures: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compile and persist a portable task contract under .dhee/context."""

    repo_root = _resolve_repo_root(repo)
    repo_link._ensure_repo_skeleton(repo_root)
    compiled = compile_task_contract(
        goal,
        repo=repo_root,
        mode=mode,
        risk=risk,
        allowed_write_paths=allowed_write_paths,
        forbidden_paths=forbidden_paths,
        must_run=must_run,
        success_criteria=success_criteria,
        context_budget=context_budget,
        memory_pointers=memory_pointers,
        recent_failures=recent_failures,
    )
    compiled = _sanitize_obj(compiled)
    task_id = str((compiled.get("contract") or {}).get("task_id") or ("task_" + _stable_hash(compiled, 16)))
    target_dir = Path(out).expanduser().resolve() if out else _task_contract_root(repo_root) / task_id
    paths = _write_task_contract(compiled, target_dir)
    md = Path(paths["markdown"]).read_text(encoding="utf-8")
    rel_dir = os.path.relpath(target_dir, repo_root) if str(target_dir).startswith(str(repo_root)) else str(target_dir)
    entry = repo_link.add_entry(
        repo_root,
        kind=TASK_CONTRACT_KIND,
        title=f"Task contract {task_id}",
        content=md,
        meta={
            "task_id": task_id,
            "contract_dir": rel_dir,
            "goal": (compiled.get("contract") or {}).get("goal"),
            "mode": (compiled.get("contract") or {}).get("mode"),
            "risk": (compiled.get("contract") or {}).get("risk"),
            "must_run": (compiled.get("contract") or {}).get("must_run") or [],
            "portable": True,
        },
    )
    return {
        "format": "dhee_task_contract_create.v1",
        "contract": compiled["contract"],
        "compiler": compiled.get("compiler"),
        "actions_schema": compiled.get("actions_schema"),
        "actions": compiled.get("actions") or [],
        "validation": compiled.get("validation") or validate_task_contract(compiled),
        "paths": paths,
        "entry": entry.to_json(),
    }


def list_task_contracts(*, repo: str | os.PathLike[str] | None = None) -> List[Dict[str, Any]]:
    repo_root = _resolve_repo_root(repo)
    root = _task_contract_root(repo_root)
    if not root.exists():
        return []
    out: List[Dict[str, Any]] = []
    for json_path in sorted(root.glob("*/contract.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        contract = data.get("contract") or {}
        out.append({
            "task_id": contract.get("task_id"),
            "goal": contract.get("goal"),
            "mode": contract.get("mode"),
            "risk": contract.get("risk"),
            "must_run": contract.get("must_run") or [],
            "path": str(json_path.parent),
            "created_at": contract.get("created_at"),
        })
    return out


def get_task_contract(
    task_id: str,
    *,
    repo: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    repo_root = _resolve_repo_root(repo)
    root = _task_contract_root(repo_root)
    matches = [
        path
        for path in root.glob("*/contract.json")
        if path.parent.name == task_id or path.parent.name.startswith(str(task_id))
    ]
    if not matches:
        raise FileNotFoundError(f"Task contract {task_id!r} not found")
    json_path = matches[0]
    md_path = json_path.with_name("contract.md")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return {
        "format": "dhee_task_contract_get.v1",
        "compiled": data,
        "contract": (data.get("contract") or {}),
        "compiler": data.get("compiler"),
        "actions": data.get("actions") or [],
        "markdown": md_path.read_text(encoding="utf-8") if md_path.exists() else render_task_contract(data),
        "paths": {"json": str(json_path), "markdown": str(md_path), "dir": str(json_path.parent)},
    }


def _read_contract_source(path: str | os.PathLike[str]) -> Tuple[Dict[str, Any], str, Path]:
    source = Path(path).expanduser().resolve()
    if source.is_dir():
        json_path = source / "contract.json"
        md_path = source / "contract.md"
        source_dir = source
    elif source.suffix == ".json":
        json_path = source
        md_path = source.with_name("contract.md")
        source_dir = source.parent
    elif source.suffix == ".md":
        json_path = source.with_name("contract.json")
        md_path = source
        source_dir = source.parent
    else:
        raise ValueError("Import path must be a task contract directory, contract.json, or contract.md")
    if not json_path.exists():
        raise FileNotFoundError(f"Missing contract.json near {source}")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else render_task_contract(data)
    return data, md, source_dir


def import_task_contract(
    path: str | os.PathLike[str],
    *,
    repo: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    repo_root = _resolve_repo_root(repo)
    repo_link._ensure_repo_skeleton(repo_root)
    data, md, _source_dir = _read_contract_source(path)
    data = _sanitize_obj(_resolve_contract(data))
    validation = validate_task_contract(data)
    if not validation["ok"]:
        codes = ", ".join(str(item.get("code")) for item in validation["diagnostics"] if item.get("level") == "error")
        raise ValueError(f"Task contract import rejected: invalid contract ({codes or 'validation failed'})")
    data["validation"] = validation
    contract = data.get("contract") or {}
    task_id = str(contract.get("task_id") or ("task_" + _stable_hash(data, 16)))
    contract["task_id"] = task_id
    data["contract"] = contract
    dest = _task_contract_root(repo_root) / task_id
    paths = _write_task_contract(data, dest)
    entry = repo_link.add_entry(
        repo_root,
        kind=TASK_CONTRACT_KIND,
        title=f"Imported task contract {task_id}",
        content=_sanitize_text(md),
        meta={
            "task_id": task_id,
            "contract_dir": os.path.relpath(dest, repo_root),
            "goal": contract.get("goal"),
            "imported": True,
            "portable": True,
        },
    )
    return {
        "format": "dhee_task_contract_import.v1",
        "contract": contract,
        "compiler": data.get("compiler"),
        "actions": data.get("actions") or [],
        "validation": validation,
        "paths": paths,
        "entry": entry.to_json(),
    }


def _load_task_contract(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
) -> Dict[str, Any]:
    if isinstance(task_contract, dict):
        return _resolve_contract(task_contract)
    value = str(task_contract)
    source = Path(value).expanduser()
    if source.exists():
        data, _md, _source_dir = _read_contract_source(source)
        return _resolve_contract(data)
    return get_task_contract(value, repo=repo)["compiled"]


def _action_state(repo_root: Path, contract: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
    action_type = str(action.get("type") or "")
    allowed_paths = contract.get("allowed_write_paths") or []
    forbidden_paths = contract.get("forbidden_paths") or []
    diagnostics: List[Dict[str, Any]] = []
    state = "ready"
    target = action.get("path") or action.get("command") or action.get("query") or action.get("category") or action.get("summary")

    if action_type == "READ_FILE":
        path = str(action.get("path") or "")
        resolved = _safe_repo_path(repo_root, path)
        if resolved is None:
            state = "blocked"
            diagnostics.append({"level": "error", "code": "UNSAFE_READ_PATH", "path": path, "message": "READ_FILE path is absolute or escapes the repo."})
        elif not resolved.exists():
            state = "blocked"
            diagnostics.append({"level": "error", "code": "READ_PATH_MISSING", "path": path, "message": "Required read target is missing in this checkout."})
        elif _is_forbidden_path(path, forbidden_paths):
            state = "blocked"
            diagnostics.append({"level": "error", "code": "READ_PATH_FORBIDDEN", "path": path, "message": "Action targets a forbidden path."})
    elif action_type == "EDIT_FILE":
        path = str(action.get("path") or "")
        resolved = _safe_repo_path(repo_root, path)
        if resolved is None:
            state = "blocked"
            diagnostics.append({"level": "error", "code": "UNSAFE_EDIT_PATH", "path": path, "message": "EDIT_FILE path is absolute or escapes the repo."})
        elif _is_forbidden_path(path, forbidden_paths):
            state = "blocked"
            diagnostics.append({"level": "error", "code": "EDIT_PATH_FORBIDDEN", "path": path, "message": "Action targets a forbidden path."})
        elif not _path_under_allowed(path, allowed_paths):
            state = "blocked"
            diagnostics.append({"level": "error", "code": "EDIT_PATH_OUTSIDE_ALLOWED", "path": path, "message": "Action is outside allowed_write_paths."})
    elif action_type == "RUN_TEST":
        command = str(action.get("command") or "")
        if not _command_is_safe(command):
            state = "blocked"
            diagnostics.append({"level": "error", "code": "UNSAFE_TEST_COMMAND", "command": command, "message": "RUN_TEST command is empty or outside the safe test command allowlist."})
    elif action_type == "ASK_USER" and action.get("blocking"):
        state = "needs_input"
        diagnostics.append({"level": "warning", "code": "BLOCKING_USER_INPUT", "message": "Action requires user input before execution."})
    elif action_type in {"WRITE_MEMORY_NOTE", "SUBMIT_PATCH"}:
        state = "deferred"
    elif action_type == "SEARCH_CODE":
        if not str(action.get("query") or "").strip():
            state = "blocked"
            diagnostics.append({"level": "error", "code": "EMPTY_SEARCH_QUERY", "message": "SEARCH_CODE action has no query."})

    return {
        "action_id": action.get("action_id"),
        "type": action_type,
        "phase": action.get("phase"),
        "requires": action.get("requires") or [],
        "soft_requires": action.get("soft_requires") or [],
        "capabilities": action.get("capabilities") or [],
        "effects": action.get("effects") or [],
        "target": target,
        "state": state,
        "diagnostics": diagnostics,
        "precondition": action.get("precondition"),
        "postcondition": action.get("postcondition"),
    }


def interpret_task_contract(
    task_contract: str | os.PathLike[str] | Dict[str, Any],
    *,
    repo: str | os.PathLike[str] | None = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Interpret a compiled task contract on the receiving machine."""

    repo_root = _resolve_repo_root(repo)
    compiled = _sanitize_obj(_load_task_contract(task_contract, repo=repo_root))
    validation = validate_task_contract(compiled)
    contract = compiled.get("contract") or {}
    actions = compiled.get("actions") or []
    diagnostics = list(validation.get("diagnostics") or [])
    current_repo = _repo_slug(repo_root)
    if contract.get("repo") and current_repo != contract.get("repo"):
        diagnostics.append({
            "level": "error" if strict else "warning",
            "code": "REPO_ID_MISMATCH",
            "message": "Compiled contract repo id differs from this checkout.",
            "compiled_repo": contract.get("repo"),
            "target_repo": current_repo,
        })
    current_branch_state = _branch_state(repo_root)
    if current_branch_state.get("dirty"):
        diagnostics.append({
            "level": "warning",
            "code": "TARGET_WORKTREE_DIRTY",
            "message": "Target worktree is dirty; receiving agent must avoid mixing unrelated edits.",
            "changed_paths": current_branch_state.get("changed_paths") or [],
        })
    for path in contract.get("allowed_write_paths") or []:
        if _is_forbidden_path(str(path), contract.get("forbidden_paths") or []):
            diagnostics.append({
                "level": "error",
                "code": "ALLOWED_PATH_FORBIDDEN",
                "path": path,
                "message": "Contract has an allowed_write_path that overlaps forbidden_paths.",
            })

    action_states = [_action_state(repo_root, contract, action) for action in actions]
    for state in action_states:
        diagnostics.extend(state.get("diagnostics") or [])

    states = {state.get("state") for state in action_states}
    if not validation["ok"] or any(item.get("level") == "error" for item in diagnostics):
        readiness = "blocked"
    elif "needs_input" in states:
        readiness = "needs_input"
    elif states and states <= {"deferred"}:
        readiness = "deferred"
    else:
        readiness = "ready"

    execution_plan: List[Dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        state = action_states[index - 1] if index - 1 < len(action_states) else {}
        execution_plan.append({
            "step": index,
            "action_id": action.get("action_id"),
            "type": action.get("type"),
            "phase": action.get("phase"),
            "state": state.get("state"),
            "target": state.get("target"),
            "requires": action.get("requires") or [],
            "soft_requires": action.get("soft_requires") or [],
            "capabilities": action.get("capabilities") or [],
            "effects": action.get("effects") or [],
            "reason": action.get("reason"),
            "precondition": action.get("precondition"),
            "execution": action.get("execution"),
            "observation": action.get("observation"),
            "postcondition": action.get("postcondition"),
            "memory_update": action.get("memory_update"),
        })

    return {
        "format": TASK_INTERPRETATION_SCHEMA,
        "repo": str(repo_root),
        "compiled_repo": contract.get("repo"),
        "target_repo": current_repo,
        "task_id": contract.get("task_id"),
        "goal": contract.get("goal"),
        "readiness": readiness,
        "validation": validation,
        "current_branch_state": current_branch_state,
        "action_states": action_states,
        "execution_plan": execution_plan,
        "diagnostics": diagnostics,
        "policy": {
            "auto_execute": False,
            "requires_agent_tool_execution": True,
            "strict": bool(strict),
        },
    }


def validate_task_contract(compiled: Dict[str, Any]) -> Dict[str, Any]:
    diagnostics: List[Dict[str, Any]] = []
    contract = compiled.get("contract") if isinstance(compiled, dict) else None
    actions = compiled.get("actions") if isinstance(compiled, dict) else None
    if not isinstance(contract, dict):
        diagnostics.append({"level": "error", "code": "MISSING_CONTRACT", "message": "Compiled task is missing contract."})
        contract = {}
    for field in (
        "task_id",
        "goal",
        "repo",
        "mode",
        "risk",
        "allowed_write_paths",
        "forbidden_paths",
        "must_run",
        "success_criteria",
        "context_budget",
    ):
        if field not in contract:
            diagnostics.append({"level": "error", "code": "MISSING_CONTRACT_FIELD", "field": field, "message": f"Task contract missing {field}."})
    if not isinstance(actions, list) or not actions:
        diagnostics.append({"level": "error", "code": "MISSING_ACTIONS", "message": "Compiled task needs at least one typed action."})
        actions = []
    action_ids = {str(action.get("action_id")) for action in actions if isinstance(action, dict) and action.get("action_id")}
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            diagnostics.append({"level": "error", "code": "INVALID_ACTION", "index": index, "message": "Action must be an object."})
            continue
        action_type = action.get("type")
        if action_type not in ACTION_TYPES:
            diagnostics.append({"level": "error", "code": "UNKNOWN_ACTION_TYPE", "index": index, "message": f"Unknown action type {action_type!r}."})
        if not action.get("action_id"):
            diagnostics.append({"level": "warning", "code": "MISSING_ACTION_ID", "index": index, "message": "Action has no stable action_id; import will lower legacy actions when possible."})
        if not action.get("bytecode"):
            diagnostics.append({"level": "warning", "code": "MISSING_ACTION_BYTECODE", "index": index, "message": "Action has no bytecode metadata; runtime enforcement will fall back to structural matching."})
        for dep in action.get("requires") or []:
            if not isinstance(dep, str) or not dep.startswith("act_"):
                diagnostics.append({"level": "error", "code": "INVALID_ACTION_DEPENDENCY", "index": index, "dependency": dep, "message": "Hard dependency must reference a stable action_id."})
            elif dep not in action_ids:
                diagnostics.append({"level": "error", "code": "UNKNOWN_ACTION_DEPENDENCY", "index": index, "dependency": dep, "message": "Hard dependency does not reference a compiled action."})
        for field in ("precondition", "execution", "observation", "postcondition", "memory_update"):
            if field not in action:
                diagnostics.append({"level": "error", "code": "MISSING_ACTION_LIFECYCLE", "index": index, "field": field, "message": f"Action missing {field}."})
        if action_type == "RUN_TEST":
            if not action.get("command") or not action.get("timeout_sec"):
                diagnostics.append({"level": "error", "code": "INVALID_RUN_TEST_ACTION", "index": index, "message": "RUN_TEST requires command and timeout_sec."})
        if action_type == "READ_FILE" and not action.get("path"):
            diagnostics.append({"level": "error", "code": "INVALID_READ_FILE_ACTION", "index": index, "message": "READ_FILE requires path."})
        if action_type == "SEARCH_CODE" and not action.get("query"):
            diagnostics.append({"level": "error", "code": "INVALID_SEARCH_CODE_ACTION", "index": index, "message": "SEARCH_CODE requires query."})
    return {
        "ok": not any(item.get("level") == "error" for item in diagnostics),
        "diagnostics": diagnostics,
        "action_count": len(actions),
    }


def render_task_contract(compiled: Dict[str, Any]) -> str:
    contract = compiled.get("contract") or {}
    lines = [
        f"# Task Contract: {contract.get('task_id') or '(unknown)'}",
        "",
        f"- Goal: {contract.get('goal') or ''}",
        f"- Repo: `{contract.get('repo') or ''}`",
        f"- Mode: `{contract.get('mode') or ''}`",
        f"- Risk: `{contract.get('risk') or ''}`",
        f"- Allowed writes: {', '.join(f'`{item}`' for item in contract.get('allowed_write_paths') or []) or '(none)'}",
        f"- Forbidden paths: {', '.join(f'`{item}`' for item in contract.get('forbidden_paths') or []) or '(none)'}",
        "",
        "## Must Run",
    ]
    lines.extend(f"- `{cmd}`" for cmd in contract.get("must_run") or ["pytest"])
    repo_intelligence = contract.get("repo_intelligence") or {}
    if repo_intelligence:
        lines.extend([
            "",
            "## Repo Brain",
            f"- Ref: `{repo_intelligence.get('ref') or ''}`",
            f"- Symbols: `{repo_intelligence.get('symbol_count') or 0}`",
            f"- Calls: `{repo_intelligence.get('call_edge_count') or 0}`",
            f"- Tests: `{repo_intelligence.get('test_count') or 0}`",
        ])
    localization = contract.get("localization") or {}
    if localization:
        lines.extend([
            "",
            "## Localization",
            f"- Status: `{localization.get('status') or ''}`",
            f"- Confidence: `{localization.get('confidence') or 0}`",
        ])
        for item in (localization.get("candidate_files") or [])[:6]:
            lines.append(f"- `{item.get('path')}` ({item.get('confidence')})")
    compiler = compiled.get("compiler") or {}
    if compiler:
        lines.extend([
            "",
            "## Compiler",
            f"- Schema: `{compiler.get('schema_version') or ''}`",
            f"- Target runtime: `{compiler.get('target_runtime') or ''}`",
            f"- Artifact hash: `{compiler.get('artifact_hash') or ''}`",
        ])
    lines.extend(["", "## Typed Actions"])
    for index, action in enumerate(compiled.get("actions") or [], start=1):
        subject = _action_target(action)
        requires = ", ".join(f"`{item}`" for item in action.get("requires") or []) or "(none)"
        lines.append(f"{index}. `{action.get('type')}` `{action.get('action_id') or ''}` {subject}")
        lines.append(f"   - Phase: {action.get('phase') or 'unknown'}")
        lines.append(f"   - Requires: {requires}")
        lines.append(f"   - Precondition: {action.get('precondition')}")
        lines.append(f"   - Observation: {action.get('observation')}")
        lines.append(f"   - Postcondition: {action.get('postcondition')}")
    return "\n".join(lines).strip() + "\n"
