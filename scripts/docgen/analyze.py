"""Static analyzers used to produce deep deterministic file documentation."""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - depends on runtime Python version
    tomllib = None

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None


ROOT_FILES = {"pyproject.toml", "Dockerfile", "docker-compose.yml"}
BRANCH_NODES = [
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.BoolOp,
    ast.IfExp,
    ast.comprehension,
]
if hasattr(ast, "Match"):  # Python 3.10+
    BRANCH_NODES.append(ast.Match)
BRANCH_NODES = tuple(BRANCH_NODES)


def collect_target_files(
    repo_root: str | Path,
    exclude_tests: bool = True,
    include_non_python: bool = True,
) -> List[str]:
    """Collect tracked files in scope for deterministic PDF generation."""
    root = Path(repo_root).resolve()
    tracked = _git_ls_files(root)

    selected: List[str] = []
    for rel in tracked:
        if rel.endswith(".md"):
            continue
        if exclude_tests and rel.startswith("tests/"):
            continue

        in_scope = (
            rel.startswith("engram/")
            or rel.startswith("plugins/engram-memory/")
            or rel in ROOT_FILES
        )
        if not in_scope:
            continue

        if not include_non_python and not rel.endswith(".py"):
            continue

        selected.append(rel)

    return sorted(selected)


def analyze_python_file(path: str | Path) -> Dict[str, Any]:
    """Analyze a Python file with AST and return a deterministic metadata payload."""
    file_path = Path(path)
    source = _read_text(file_path)
    tree = ast.parse(source)
    lines = source.splitlines()

    imports = _extract_imports(tree)
    constants = _extract_constants(tree)
    top_level_functions = _extract_functions(tree.body)
    classes = _extract_classes(tree.body)
    top_symbols = {item["name"] for item in top_level_functions} | {item["name"] for item in classes}

    parent_map = _build_parent_map(tree)
    raises = _extract_raises(tree, parent_map)
    call_map = _build_call_map(tree.body, top_symbols)
    side_effect_hints = _side_effect_hints(imports, tree)

    branch_count = sum(1 for node in ast.walk(tree) if isinstance(node, BRANCH_NODES))
    comment_count = sum(1 for line in lines if line.strip().startswith("#"))
    non_empty = sum(1 for line in lines if line.strip())

    module_docstring = ast.get_docstring(tree)
    complexity = {
        "line_count": len(lines),
        "non_empty_lines": non_empty,
        "comment_lines": comment_count,
        "branch_nodes": branch_count,
        "cyclomatic_estimate": 1 + branch_count,
    }

    dependencies = sorted({item["module"] for item in imports if item["module"]})

    return {
        "file_type": "python",
        "path": str(file_path),
        "line_count": len(lines),
        "module_docstring": module_docstring or "",
        "imports": imports,
        "constants": constants,
        "functions": top_level_functions,
        "classes": classes,
        "raises": raises,
        "side_effect_hints": side_effect_hints,
        "call_map": call_map,
        "complexity": complexity,
        "dependencies": dependencies,
    }


def analyze_non_python_file(path: str | Path) -> Dict[str, Any]:
    """Analyze supported non-Python files used by runtime and integration layers."""
    file_path = Path(path)
    text = _read_text(file_path)
    line_count = len(text.splitlines())
    lowered = file_path.name.lower()

    parser_errors: List[str] = []
    structure: List[str] = []
    runtime_implications: List[str] = []
    integrations: List[str] = []
    instructions: List[Dict[str, Any]] = []

    if lowered == "dockerfile":
        format_name = "dockerfile"
        instructions = _parse_dockerfile(text)
        structure = [
            f"{item['instruction']} ({file_path.name}:{item['line']})"
            for item in instructions[:80]
        ]
        runtime_implications.extend(_docker_runtime_implications(instructions))
        integrations.extend(_docker_integrations(instructions))

    elif file_path.suffix == ".json":
        format_name = "json"
        try:
            parsed = json.loads(text)
            structure = _flatten_keys(parsed)
            runtime_implications.extend(_config_runtime_implications(structure))
            integrations.extend(_integration_hints_from_keys(structure))
        except Exception as exc:
            parser_errors.append(f"JSON parse error: {exc}")

    elif file_path.suffix == ".toml":
        format_name = "toml"
        try:
            parsed = _load_toml(text)
            structure = _flatten_keys(parsed)
            runtime_implications.extend(_config_runtime_implications(structure))
            integrations.extend(_integration_hints_from_keys(structure))
        except Exception as exc:
            parser_errors.append(f"TOML parse error: {exc}")

    elif file_path.suffix in {".yml", ".yaml"}:
        format_name = "yaml"
        try:
            parsed = _load_yaml(text)
            structure = _flatten_keys(parsed)
            runtime_implications.extend(_config_runtime_implications(structure))
            integrations.extend(_integration_hints_from_keys(structure))
        except Exception as exc:
            parser_errors.append(f"YAML parse error: {exc}")

    elif file_path.suffix == ".html":
        format_name = "html"
        html_info = _analyze_html(text)
        structure = html_info["structure"]
        integrations = html_info["integrations"]
        runtime_implications = html_info["runtime_implications"]

    else:
        format_name = "text"
        structure = _line_headings(text)

    return {
        "file_type": "non_python",
        "format": format_name,
        "path": str(file_path),
        "line_count": line_count,
        "structure": structure,
        "instructions": instructions,
        "runtime_implications": _stable_unique(runtime_implications),
        "integrations": _stable_unique(integrations),
        "parser_errors": parser_errors,
    }


def build_doc_payload(path: str | Path, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Build deep sectioned documentation payload in required section order."""
    rel_path = str(path)
    file_type = analysis["file_type"]
    line_count = analysis.get("line_count", 0)

    sections: List[Dict[str, Any]] = []

    sections.append(
        {
            "title": "Role in repository",
            "paragraphs": _role_in_repository(rel_path, analysis),
            "code_blocks": [],
        }
    )

    file_map_lines, metrics_block = _file_map_and_metrics(rel_path, analysis)
    sections.append(
        {
            "title": "File map and metrics",
            "paragraphs": file_map_lines,
            "code_blocks": [metrics_block] if metrics_block else [],
        }
    )

    interface_paragraphs, interface_blocks = _public_interfaces(rel_path, analysis)
    sections.append(
        {
            "title": "Public interfaces and key symbols",
            "paragraphs": interface_paragraphs,
            "code_blocks": interface_blocks,
        }
    )

    sections.append(
        {
            "title": "Execution/data flow walkthrough",
            "paragraphs": _execution_walkthrough(rel_path, analysis),
            "code_blocks": [],
        }
    )

    sections.append(
        {
            "title": "Error handling and edge cases",
            "paragraphs": _error_and_edge_cases(rel_path, analysis),
            "code_blocks": [],
        }
    )

    sections.append(
        {
            "title": "Integration and dependencies",
            "paragraphs": _integration_and_dependencies(rel_path, analysis),
            "code_blocks": [],
        }
    )

    sections.append(
        {
            "title": "Safe modification guide",
            "paragraphs": _safe_modification(rel_path, analysis),
            "code_blocks": [],
        }
    )

    sections.append(
        {
            "title": "Reading order for large files",
            "paragraphs": _reading_order(rel_path, analysis),
            "code_blocks": [],
        }
    )

    return {
        "file_path": rel_path,
        "line_count": line_count,
        "file_type": file_type,
        "doc_depth": "deep",
        "method": "deterministic_static",
        "sections": sections,
    }


def _git_ls_files(repo_root: Path) -> List[str]:
    output = subprocess.check_output(["git", "-C", str(repo_root), "ls-files"], text=True)
    return [line.strip() for line in output.splitlines() if line.strip()]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def _extract_imports(tree: ast.AST) -> List[Dict[str, Any]]:
    imports: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(
                    {
                        "module": alias.name,
                        "name": alias.asname or alias.name,
                        "line": node.lineno,
                        "kind": "import",
                    }
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(
                    {
                        "module": module,
                        "name": alias.asname or alias.name,
                        "line": node.lineno,
                        "kind": "from",
                    }
                )
    imports.sort(key=lambda item: (item["line"], item["module"], item["name"]))
    return imports


def _extract_constants(tree: ast.Module) -> List[Dict[str, Any]]:
    constants: List[Dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants.append({"name": target.id, "line": node.lineno})
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                constants.append({"name": node.target.id, "line": node.lineno})
    return sorted(constants, key=lambda item: (item["line"], item["name"]))


def _extract_functions(nodes: Iterable[ast.stmt]) -> List[Dict[str, Any]]:
    funcs: List[Dict[str, Any]] = []
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(_function_metadata(node))
    return funcs


def _extract_classes(nodes: Iterable[ast.stmt]) -> List[Dict[str, Any]]:
    classes: List[Dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, ast.ClassDef):
            continue

        methods: List[Dict[str, Any]] = []
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(_function_metadata(child, class_name=node.name))

        bases = [_safe_unparse(base) for base in node.bases]
        decorators = [_safe_unparse(dec) for dec in node.decorator_list]
        classes.append(
            {
                "name": node.name,
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "bases": bases,
                "decorators": decorators,
                "methods": methods,
            }
        )
    return classes


def _function_metadata(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_name: Optional[str] = None,
) -> Dict[str, Any]:
    decorators = [_safe_unparse(dec) for dec in node.decorator_list]
    signature = _format_signature(node)
    return {
        "name": node.name,
        "qualified_name": f"{class_name}.{node.name}" if class_name else node.name,
        "line": node.lineno,
        "end_line": getattr(node, "end_lineno", node.lineno),
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "decorators": decorators,
        "signature": signature,
    }


def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args_text = _safe_unparse(node.args)
    ret_text = f" -> {_safe_unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix} {node.name}({args_text}){ret_text}"


def _build_parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    parent_map: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def _extract_raises(tree: ast.AST, parent_map: Dict[ast.AST, ast.AST]) -> List[Dict[str, Any]]:
    raises: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue

        exc = _safe_unparse(node.exc) if node.exc else "re-raise"
        context = _enclosing_symbol(node, parent_map)
        raises.append(
            {
                "line": node.lineno,
                "exception": exc,
                "context": context,
            }
        )
    raises.sort(key=lambda item: (item["line"], item["exception"]))
    return raises


def _enclosing_symbol(node: ast.AST, parent_map: Dict[ast.AST, ast.AST]) -> str:
    current = node
    fn_name: Optional[str] = None
    class_name: Optional[str] = None

    while current in parent_map:
        current = parent_map[current]
        if fn_name is None and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_name = current.name
        elif class_name is None and isinstance(current, ast.ClassDef):
            class_name = current.name

    if class_name and fn_name:
        return f"{class_name}.{fn_name}"
    if fn_name:
        return fn_name
    if class_name:
        return class_name
    return "module"


def _build_call_map(nodes: Iterable[ast.stmt], top_symbols: set[str]) -> Dict[str, List[str]]:
    call_map: Dict[str, List[str]] = {}

    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            called = _called_top_symbols(node, top_symbols)
            call_map[node.name] = called
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    key = f"{node.name}.{child.name}"
                    call_map[key] = _called_top_symbols(child, top_symbols)

    return call_map


def _called_top_symbols(node: ast.AST, top_symbols: set[str]) -> List[str]:
    called: List[str] = []
    seen: set[str] = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        raw = _call_name(item)
        if not raw:
            continue
        first = raw.split(".")[0]
        if first in top_symbols and first not in seen:
            seen.add(first)
            called.append(first)
    return called


def _call_name(node: ast.Call) -> Optional[str]:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        chain: List[str] = []
        current: ast.AST = node.func
        while isinstance(current, ast.Attribute):
            chain.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            chain.append(current.id)
        chain.reverse()
        return ".".join(chain)
    return None


def _side_effect_hints(imports: List[Dict[str, Any]], tree: ast.AST) -> Dict[str, List[str]]:
    modules = [item["module"].lower() for item in imports if item["module"]]
    calls = [(_call_name(node) or "").lower() for node in ast.walk(tree) if isinstance(node, ast.Call)]
    tokens = modules + calls

    buckets = {
        "database": ["sqlite", "database", "db", "cursor", "execute", "commit", "rollback"],
        "network": ["http", "request", "socket", "openai", "gemini", "ollama", "client", "api"],
        "filesystem": ["open", "path", "mkdir", "write", "unlink", "rename", "shutil", "glob"],
        "logging": ["logging", "logger", "print"],
        "subprocess": ["subprocess", "popen", "check_output", "os.system", "run"],
        "environment": ["getenv", "environ", "os.environ"],
    }

    hints: Dict[str, List[str]] = {}
    for bucket, markers in buckets.items():
        matched = sorted(
            {
                token
                for token in tokens
                for marker in markers
                if marker in token
            }
        )
        if matched:
            hints[bucket] = matched

    return hints


def _load_toml(text: str) -> Any:
    if tomllib is not None:
        return tomllib.loads(text)

    try:
        import tomli  # type: ignore

        return tomli.loads(text)
    except Exception as exc:  # pragma: no cover - dependent on environment
        raise RuntimeError("tomllib/tomli unavailable for TOML parsing") from exc


def _load_yaml(text: str) -> Any:
    if yaml is not None:
        return yaml.safe_load(text)

    # Fallback: parse top-level keys only if PyYAML is unavailable.
    parsed: Dict[str, Any] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key = stripped.split(":", 1)[0].strip()
            parsed[key] = "<unknown>"
    return parsed


def _flatten_keys(data: Any, prefix: str = "") -> List[str]:
    keys: List[str] = []

    if isinstance(data, dict):
        for key in sorted(data.keys(), key=lambda item: str(item)):
            key_str = str(key)
            current = f"{prefix}.{key_str}" if prefix else key_str
            keys.append(current)
            keys.extend(_flatten_keys(data[key], current))
    elif isinstance(data, list):
        current = f"{prefix}[]" if prefix else "[]"
        keys.append(current)
        if data:
            keys.extend(_flatten_keys(data[0], current))

    return keys[:400]


def _config_runtime_implications(keys: List[str]) -> List[str]:
    implications: List[str] = []
    lowered = [key.lower() for key in keys]

    if any("dependencies" in key for key in lowered):
        implications.append("Dependency declarations drive installation/runtime compatibility.")
    if any("scripts" in key for key in lowered):
        implications.append("Script entries define developer and release workflows.")
    if any("environment" in key or "env" in key for key in lowered):
        implications.append("Environment keys influence runtime behavior across environments.")
    if any("port" in key for key in lowered):
        implications.append("Port settings determine network exposure and service wiring.")
    if any("api" in key or "key" in key or "token" in key for key in lowered):
        implications.append("Credential-related keys require secret management and redaction discipline.")
    if any("database" in key or "sqlite" in key for key in lowered):
        implications.append("Storage-related keys alter persistence topology and migration expectations.")

    return implications


def _integration_hints_from_keys(keys: List[str]) -> List[str]:
    integrations: List[str] = []
    lowered = [key.lower() for key in keys]

    for provider in ["openai", "gemini", "ollama", "fastapi", "docker", "mcp"]:
        if any(provider in key for key in lowered):
            integrations.append(f"Contains configuration surface for {provider} integration.")

    return integrations


def _parse_dockerfile(text: str) -> List[Dict[str, Any]]:
    instructions: List[Dict[str, Any]] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        match = re.match(r"^([A-Za-z]+)\s+(.*)$", stripped)
        if match:
            instruction = match.group(1).upper()
            value = match.group(2).strip()
        else:
            instruction = "RAW"
            value = stripped

        instructions.append(
            {
                "line": lineno,
                "instruction": instruction,
                "value": value,
            }
        )

    return instructions


def _docker_runtime_implications(instructions: List[Dict[str, Any]]) -> List[str]:
    implications: List[str] = []
    names = {item["instruction"] for item in instructions}

    if "FROM" in names:
        implications.append("Base image selection constrains OS packages and runtime security posture.")
    if "RUN" in names:
        implications.append("RUN layers define build-time dependencies and affect image reproducibility.")
    if "EXPOSE" in names:
        implications.append("EXPOSE signals service ports expected by runtime orchestration.")
    if "CMD" in names or "ENTRYPOINT" in names:
        implications.append("Process startup instructions control container lifecycle and health behavior.")
    if "ENV" in names:
        implications.append("ENV instructions create default environment values for downstream execution.")

    return implications


def _docker_integrations(instructions: List[Dict[str, Any]]) -> List[str]:
    integrations: List[str] = []
    values = "\n".join(item["value"] for item in instructions).lower()

    for token in ["python", "uvicorn", "fastapi", "sqlite", "engram"]:
        if token in values:
            integrations.append(f"Docker build/runtime references {token} components.")

    return integrations


class _HTMLShapeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tag_counter: Counter[str] = Counter()
        self.ids: set[str] = set()
        self.classes: set[str] = set()
        self.scripts: List[str] = []
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        self.tag_counter[tag] += 1
        attr_map = {key: value for key, value in attrs}

        if attr_map.get("id"):
            self.ids.add(attr_map["id"] or "")
        if attr_map.get("class"):
            class_value = attr_map["class"] or ""
            for item in class_value.split():
                self.classes.add(item)
        if tag == "script":
            src = attr_map.get("src") or "inline-script"
            self.scripts.append(src)
        if tag in {"a", "link"}:
            href = attr_map.get("href")
            if href:
                self.links.append(href)


def _analyze_html(text: str) -> Dict[str, Any]:
    parser = _HTMLShapeParser()
    parser.feed(text)

    structure = [f"<{tag}> count={count}" for tag, count in parser.tag_counter.most_common(20)]
    if parser.ids:
        structure.append(f"IDs: {', '.join(sorted(parser.ids)[:25])}")
    if parser.classes:
        structure.append(f"Classes: {', '.join(sorted(parser.classes)[:25])}")

    integrations: List[str] = []
    for src in parser.scripts[:50]:
        integrations.append(f"Script dependency: {src}")
    for href in parser.links[:50]:
        integrations.append(f"Hyperlink/resource reference: {href}")

    runtime_implications = [
        "HTML structure defines frontend entry points and developer observability surfaces.",
    ]
    if parser.scripts:
        runtime_implications.append("Script tags can introduce runtime dependencies and browser execution order coupling.")
    if parser.links:
        runtime_implications.append("External links/resources may fail if deployment paths or hosts change.")

    return {
        "structure": structure,
        "integrations": integrations,
        "runtime_implications": runtime_implications,
    }


def _line_headings(text: str) -> List[str]:
    headings: List[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            headings.append(f"Section {stripped} (line {lineno})")
        elif ":" in stripped and not stripped.startswith("#"):
            key = stripped.split(":", 1)[0].strip()
            headings.append(f"Key {key} (line {lineno})")
        if len(headings) >= 120:
            break
    return headings


def _role_in_repository(rel_path: str, analysis: Dict[str, Any]) -> List[str]:
    role = _role_from_path(rel_path)
    file_type = analysis["file_type"]
    paragraphs = [
        f"`{rel_path}` is part of the {role}. This file is analyzed as `{file_type}` content.",
    ]

    if file_type == "python":
        funcs = len(analysis.get("functions", []))
        classes = len(analysis.get("classes", []))
        paragraphs.append(
            f"Symbol density: {classes} class(es), {funcs} top-level function(s), "
            f"{analysis.get('line_count', 0)} total lines.")
    else:
        fmt = analysis.get("format", "text")
        paragraphs.append(
            f"Configuration/asset format `{fmt}` controls runtime behavior for the repository's integration and deployment surfaces.")

    return paragraphs


def _role_from_path(rel_path: str) -> str:
    mapping = {
        "engram/api/": "API service layer",
        "engram/core/": "core memory kernel",
        "engram/db/": "database persistence layer",
        "engram/embeddings/": "embedding provider integration layer",
        "engram/llms/": "LLM provider abstraction layer",
        "engram/memory/": "public memory orchestration layer",
        "engram/retrieval/": "retrieval and ranking layer",
        "engram/vector_stores/": "vector-store backend layer",
        "engram/utils/": "shared utility layer",
        "engram/integrations/": "external tool integration layer",
        "plugins/engram-memory/": "agent plugin integration package",
    }
    for prefix, role in mapping.items():
        if rel_path.startswith(prefix):
            return role
    if rel_path in ROOT_FILES:
        return "root runtime/build configuration"
    return "repository support layer"


def _file_map_and_metrics(rel_path: str, analysis: Dict[str, Any]) -> tuple[List[str], str]:
    paragraphs: List[str] = []
    blocks: List[str] = []
    line_count = analysis.get("line_count", 0)

    paragraphs.append(f"The file has {line_count} lines and belongs to `{analysis['file_type']}` analysis path.")

    if analysis["file_type"] == "python":
        complexity = analysis.get("complexity", {})
        blocks.extend(
            [
                f"line_count: {complexity.get('line_count', line_count)}",
                f"non_empty_lines: {complexity.get('non_empty_lines', 0)}",
                f"comment_lines: {complexity.get('comment_lines', 0)}",
                f"branch_nodes: {complexity.get('branch_nodes', 0)}",
                f"cyclomatic_estimate: {complexity.get('cyclomatic_estimate', 1)}",
            ]
        )

        if analysis.get("module_docstring"):
            paragraphs.append(
                f"Module docstring present near `{rel_path}:1`, indicating explicit file intent and usage guidance.")
        else:
            paragraphs.append("No module docstring detected; intent must be inferred from symbols and call graph.")
    else:
        fmt = analysis.get("format", "text")
        structure_len = len(analysis.get("structure", []))
        paragraphs.append(
            f"Detected `{fmt}` structure with {structure_len} key/instruction/tag entries captured for documentation.")
        if analysis.get("parser_errors"):
            paragraphs.append(
                f"Parser reported {len(analysis['parser_errors'])} issue(s); documentation falls back to best-effort structural extraction.")

    return paragraphs, "\n".join(blocks)


def _public_interfaces(rel_path: str, analysis: Dict[str, Any]) -> tuple[List[str], List[str]]:
    paragraphs: List[str] = []
    blocks: List[str] = []

    if analysis["file_type"] == "python":
        constants = analysis.get("constants", [])
        classes = analysis.get("classes", [])
        functions = analysis.get("functions", [])

        if constants:
            constants_text = ", ".join(
                f"`{item['name']}` ({rel_path}:{item['line']})" for item in constants[:40]
            )
            paragraphs.append(f"Top-level constants: {constants_text}.")

        if classes:
            paragraphs.append(
                f"Class interfaces appear at: "
                + ", ".join(f"`{item['name']}` ({rel_path}:{item['line']})" for item in classes[:30])
                + "."
            )
            class_sigs: List[str] = []
            for cls in classes[:20]:
                base_text = f"({', '.join(cls['bases'])})" if cls.get("bases") else ""
                class_sigs.append(f"class {cls['name']}{base_text}  # {rel_path}:{cls['line']}")
                for method in cls.get("methods", [])[:20]:
                    class_sigs.append(f"    {method['signature']}  # {rel_path}:{method['line']}")
            blocks.append("\n".join(class_sigs))

        if functions:
            paragraphs.append(
                f"Top-level callable interfaces: "
                + ", ".join(f"`{item['name']}` ({rel_path}:{item['line']})" for item in functions[:40])
                + "."
            )
            blocks.append(
                "\n".join(
                    f"{item['signature']}  # {rel_path}:{item['line']}" for item in functions[:80]
                )
            )

        if not (constants or classes or functions):
            paragraphs.append("No top-level public symbols detected (likely package marker or data-only module).")

    else:
        structure = analysis.get("structure", [])
        if structure:
            preview = ", ".join(f"`{item}`" for item in structure[:30])
            paragraphs.append(
                f"Primary structural interfaces include: {preview}."
            )
        else:
            paragraphs.append("No structured interface extracted; file may be minimal or free-form.")

        instructions = analysis.get("instructions", [])
        if instructions:
            blocks.append(
                "\n".join(
                    f"{item['instruction']} {item['value']}  # {rel_path}:{item['line']}"
                    for item in instructions[:80]
                )
            )

    return paragraphs, [block for block in blocks if block.strip()]


def _execution_walkthrough(rel_path: str, analysis: Dict[str, Any]) -> List[str]:
    paragraphs: List[str] = []

    if analysis["file_type"] == "python":
        call_map = analysis.get("call_map", {})
        if not call_map:
            paragraphs.append("No internal top-level call chaining detected; behavior is either declarative or externally invoked.")
        else:
            for symbol, callees in list(call_map.items())[:80]:
                if callees:
                    paragraphs.append(
                        f"Execution path: `{symbol}` in `{rel_path}` invokes {', '.join(f'`{c}`' for c in callees)}.")
                else:
                    paragraphs.append(
                        f"Execution path: `{symbol}` in `{rel_path}` has no detected calls to same-file top-level symbols.")

        async_symbols = [
            item["qualified_name"]
            for item in analysis.get("functions", [])
            if item.get("is_async")
        ]
        for cls in analysis.get("classes", []):
            async_symbols.extend(
                method["qualified_name"]
                for method in cls.get("methods", [])
                if method.get("is_async")
            )
        if async_symbols:
            paragraphs.append(
                "Async execution surfaces detected: " + ", ".join(f"`{name}`" for name in async_symbols[:40]) + "."
            )

    else:
        fmt = analysis.get("format", "text")
        if fmt == "dockerfile":
            paragraphs.append(
                "Dockerfile flow executes top-to-bottom as image layers; changing earlier instructions invalidates downstream cache layers.")
            paragraphs.append(
                f"Instruction order is captured with line references in `{rel_path}` and should be reviewed sequentially during modifications.")
        elif fmt in {"json", "toml", "yaml"}:
            paragraphs.append(
                "Configuration flow is key-driven: loaders parse hierarchy first, then runtime components consume specific sections.")
            paragraphs.append(
                f"Use the extracted hierarchy from `{rel_path}` to locate producer/consumer contracts before editing values.")
        elif fmt == "html":
            paragraphs.append(
                "HTML flow is browser-driven: DOM structure defines render order while script/link tags define runtime side effects.")
            paragraphs.append(
                f"Tag and resource extraction from `{rel_path}` highlights where integration contracts attach.")
        else:
            paragraphs.append("Execution flow is not directly inferable from this file type; treat it as passive input to other systems.")

    return paragraphs


def _error_and_edge_cases(rel_path: str, analysis: Dict[str, Any]) -> List[str]:
    paragraphs: List[str] = []

    if analysis["file_type"] == "python":
        raises = analysis.get("raises", [])
        if raises:
            paragraphs.append(
                f"Detected {len(raises)} explicit `raise` statement(s); key sites include: "
                + ", ".join(
                    f"`{item['exception']}` in `{item['context']}` ({rel_path}:{item['line']})"
                    for item in raises[:30]
                )
                + "."
            )
        else:
            paragraphs.append("No explicit `raise` statements detected; failures may surface through dependency calls or return-value signaling.")

        complexity = analysis.get("complexity", {})
        if complexity.get("branch_nodes", 0) > 80:
            paragraphs.append(
                "High branch density suggests multiple conditional paths; validate edge-case behavior when changing conditionals.")
        if complexity.get("cyclomatic_estimate", 1) > 120:
            paragraphs.append(
                "Very high cyclomatic estimate indicates elevated regression risk; target incremental edits with focused tests.")

    else:
        parser_errors = analysis.get("parser_errors", [])
        if parser_errors:
            paragraphs.append("Parsing issues detected: " + "; ".join(parser_errors) + ".")
        else:
            paragraphs.append("No parser-level structural errors detected.")

        fmt = analysis.get("format")
        if fmt in {"json", "toml", "yaml"}:
            paragraphs.append(
                "Edge cases: invalid syntax, missing required keys, and value-type drift can break boot/runtime configuration loading.")
        elif fmt == "dockerfile":
            paragraphs.append(
                "Edge cases: cache invalidation, missing build context files, and incompatible base images can break builds.")
        elif fmt == "html":
            paragraphs.append(
                "Edge cases: missing script resources and DOM id/class mismatches can break client-side behavior.")

    return paragraphs


def _integration_and_dependencies(rel_path: str, analysis: Dict[str, Any]) -> List[str]:
    paragraphs: List[str] = []

    if analysis["file_type"] == "python":
        deps = analysis.get("dependencies", [])
        if deps:
            paragraphs.append(
                "Direct imports indicate dependency surface: "
                + ", ".join(f"`{dep}`" for dep in deps[:60])
                + "."
            )

        hints = analysis.get("side_effect_hints", {})
        if hints:
            for bucket, tokens in hints.items():
                paragraphs.append(
                    f"{bucket.capitalize()} side-effect signals from `{rel_path}`: "
                    + ", ".join(f"`{token}`" for token in tokens[:20])
                    + "."
                )
        else:
            paragraphs.append("No strong side-effect markers detected from imports/call signatures.")

    else:
        integrations = analysis.get("integrations", [])
        implications = analysis.get("runtime_implications", [])
        if integrations:
            paragraphs.append(
                "Integration touchpoints: " + ", ".join(f"{item}" for item in integrations[:30])
            )
        if implications:
            paragraphs.append(
                "Runtime implications: " + " ".join(implications[:10])
            )
        if not integrations and not implications:
            paragraphs.append("No major integration signals extracted from this file.")

    return paragraphs


def _safe_modification(rel_path: str, analysis: Dict[str, Any]) -> List[str]:
    base = [
        f"Start by checking dependent call sites and imports for `{rel_path}` before renaming symbols or keys.",
        "Preserve existing function/class signatures unless all callers are updated in the same change set.",
        "Apply narrow edits first, then run targeted tests for affected modules before broad refactors.",
    ]

    if analysis["file_type"] == "python":
        if analysis.get("side_effect_hints", {}).get("database"):
            base.append("Database interaction signals are present; validate migrations and transaction boundaries after edits.")
        if analysis.get("side_effect_hints", {}).get("network"):
            base.append("Network/API interaction signals are present; test failure and timeout handling paths explicitly.")
    else:
        fmt = analysis.get("format")
        if fmt in {"json", "toml", "yaml"}:
            base.append("Keep key names stable where they are consumed by code paths or deployment tooling.")
        if fmt == "dockerfile":
            base.append("Modify Dockerfile instructions with cache and layer ordering in mind to avoid accidental build regressions.")
        if fmt == "html":
            base.append("Coordinate HTML id/class changes with JavaScript and CSS selectors to avoid runtime UI regressions.")

    return base


def _reading_order(rel_path: str, analysis: Dict[str, Any]) -> List[str]:
    line_count = analysis.get("line_count", 0)

    if line_count <= 200:
        return [
            f"`{rel_path}` is compact ({line_count} lines). Read top-to-bottom in one pass, then revisit integration points.",
        ]

    if analysis["file_type"] == "python":
        steps: List[str] = [
            f"Pass 1: scan imports and constants near the top of `{rel_path}` to understand dependencies and global controls.",
        ]

        classes = analysis.get("classes", [])
        functions = analysis.get("functions", [])
        if classes:
            steps.append(
                "Pass 2: read class definitions in line order: "
                + ", ".join(f"`{item['name']}` ({rel_path}:{item['line']})" for item in classes[:20])
                + "."
            )
        if functions:
            steps.append(
                "Pass 3: review top-level functions in line order: "
                + ", ".join(f"`{item['name']}` ({rel_path}:{item['line']})" for item in functions[:25])
                + "."
            )

        steps.append("Final pass: inspect raise sites and side-effect hints to map operational risk points.")
        return steps

    return [
        f"Read `{rel_path}` in declaration order, then validate extracted hierarchy/instructions against runtime consumers.",
        "Prioritize sections that define dependencies, service commands, credentials, ports, and integration URLs.",
    ]


def _safe_unparse(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _stable_unique(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
