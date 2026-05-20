import json
import subprocess
import sys
import textwrap
from pathlib import Path

from dhee import repo_intelligence as repo_mod
from dhee.repo_intelligence import (
    build_repo_brain,
    context_graph_query,
    load_repo_brain,
    localize_issue,
    repo_callers,
    repo_callees,
    repo_explore,
    repo_graph_from_brain,
    repo_impact,
    repo_symbol_search,
)
from dhee.task_contracts import compile_task_contract


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "dhee-test@example.com"], path)
    _run(["git", "config", "user.name", "Dhee Test"], path)
    (path / "dhee").mkdir()
    (path / "tests").mkdir()
    (path / "dhee" / "__init__.py").write_text("", encoding="utf-8")
    (path / "dhee" / "rules.py").write_text(
        "def normalize_path(path):\n"
        "    return str(path).strip()\n",
        encoding="utf-8",
    )
    (path / "dhee" / "context_firewall.py").write_text(
        "from .rules import normalize_path\n\n"
        "class ContextFirewall:\n"
        "    def allow_path(self, path):\n"
        "        value = normalize_path(path)\n"
        "        return not value.startswith('.env')\n\n"
        "def allow_path(path):\n"
        "    return ContextFirewall().allow_path(path)\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_context_firewall.py").write_text(
        "from dhee.context_firewall import allow_path\n\n"
        "def test_env_is_blocked():\n"
        "    assert allow_path('.env') is False\n",
        encoding="utf-8",
    )
    (path / "pyproject.toml").write_text("[project]\nname = \"repo-brain-test\"\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-m", "initial"], path)
    return path


def _write_fake_lsp_server(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            r'''
            import json
            import sys


            def read_message():
                headers = {}
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    if line in (b"\r\n", b"\n"):
                        break
                    name, _, value = line.decode("ascii", errors="replace").partition(":")
                    headers[name.lower()] = value.strip()
                length = int(headers.get("content-length") or 0)
                if length <= 0:
                    return None
                return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


            def send(message):
                payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(b"Content-Length: " + str(len(payload)).encode("ascii") + b"\r\n\r\n" + payload)
                sys.stdout.buffer.flush()


            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                request_id = message.get("id")
                if method == "initialize":
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "capabilities": {
                                    "textDocumentSync": 1,
                                    "documentSymbolProvider": True,
                                    "referencesProvider": True,
                                }
                            },
                        }
                    )
                elif method == "textDocument/didOpen":
                    uri = message["params"]["textDocument"]["uri"]
                    send(
                        {
                            "jsonrpc": "2.0",
                            "method": "textDocument/publishDiagnostics",
                            "params": {
                                "uri": uri,
                                "diagnostics": [
                                    {
                                        "range": {
                                            "start": {"line": 1, "character": 4},
                                            "end": {"line": 1, "character": 14},
                                        },
                                        "severity": 2,
                                        "source": "fake-lsp",
                                        "code": "demo",
                                        "message": "synthetic diagnostic",
                                    }
                                ],
                            },
                        }
                    )
                elif method == "textDocument/documentSymbol":
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": [
                                {
                                    "name": "ContextFirewall",
                                    "kind": 5,
                                    "range": {
                                        "start": {"line": 2, "character": 0},
                                        "end": {"line": 5, "character": 0},
                                    },
                                    "selectionRange": {
                                        "start": {"line": 2, "character": 6},
                                        "end": {"line": 2, "character": 21},
                                    },
                                    "children": [
                                        {
                                            "name": "allow_path",
                                            "kind": 6,
                                            "range": {
                                                "start": {"line": 3, "character": 4},
                                                "end": {"line": 5, "character": 0},
                                            },
                                            "selectionRange": {
                                                "start": {"line": 3, "character": 8},
                                                "end": {"line": 3, "character": 18},
                                            },
                                        }
                                    ],
                                }
                            ],
                        }
                    )
                elif method == "textDocument/references":
                    uri = message["params"]["textDocument"]["uri"]
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": [
                                {
                                    "uri": uri,
                                    "range": {
                                        "start": {"line": 3, "character": 8},
                                        "end": {"line": 3, "character": 18},
                                    },
                                }
                            ],
                        }
                    )
                elif method == "shutdown":
                    send({"jsonrpc": "2.0", "id": request_id, "result": None})
                elif method == "exit":
                    break
            '''
        ).lstrip(),
        encoding="utf-8",
    )


def test_repo_brain_persists_symbols_imports_calls_and_test_map(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    brain = build_repo_brain(
        repo,
        goal="Fix failing context firewall tests",
        relevant_files=["dhee/context_firewall.py"],
        must_run=["pytest tests/test_context_firewall.py"],
    )

    storage = brain["storage"]
    assert brain["schema_version"] == "dhee.repo_intelligence.v4"
    assert brain["engine"]["indexer"] == "swe_repo_brain.v4"
    assert storage["ref"].startswith("repo_brain:")
    assert (repo / storage["path"]).exists()
    assert (repo / ".dhee" / "context" / "repo_brain" / "latest.json").exists()
    assert brain["symbol_index"]["summary"]["symbol_count"] >= 3
    assert brain["edge_index"]["summary"]["edge_count"] >= 3
    assert brain["query_index"]["summary"]["entry_count"] >= brain["metrics"]["indexed_file_count"]
    assert brain["source_windows"]["summary"]["raw_file_bodies_excluded"] is True
    assert brain["extractor_versions"]["engine"] == "swe_repo_brain.v4"
    assert any(symbol["qualname"] == "ContextFirewall.allow_path" for symbol in brain["symbols"])
    assert any(symbol["name"] == "normalize_path" for symbol in brain["symbols"])
    assert any(
        item["resolved_path"] == "dhee/context_firewall.py"
        for item in brain["imports"]["tests/test_context_firewall.py"]
    )
    assert any(edge["callee_name"] == "normalize_path" for edge in brain["call_graph"])

    linked_tests = brain["test_map"]["source_to_tests"]["dhee/context_firewall.py"]
    assert linked_tests[0]["path"] == "tests/test_context_firewall.py"
    assert linked_tests[0]["confidence"] > 0.5
    assert any(
        edge["source"] == "tests/test_context_firewall.py" and edge["target"] == "dhee/context_firewall.py"
        for edge in brain["dependency_graph"]["local_import_edges"]
    )
    assert brain["test_ownership"]["source_to_tests"]["dhee/context_firewall.py"][0]["path"] == "tests/test_context_firewall.py"
    assert any(
        "test imports source module" in reason
        for reason in brain["test_ownership"]["source_to_tests"]["dhee/context_firewall.py"][0]["reasons"]
    )
    assert brain["repo_graph"]["schema_version"] == "dhee.repo_graph_artifact.v1"
    assert brain["metrics"]["repo_graph_node_count"] >= 4
    assert brain["metrics"]["repo_graph_edge_count"] >= 3
    assert (repo / storage["repo_graph_path"]).exists()
    repo_graph = repo_graph_from_brain(brain)
    assert any(node["id"] == "file:dhee/context_firewall.py" for node in repo_graph["nodes"])
    assert any(edge["type"] == "tested_by" for edge in repo_graph["edges"])
    context_graph = context_graph_query(brain, "Fix failing context firewall tests")
    assert context_graph["schema_version"] == "dhee.context_graph_slice.v1"
    assert context_graph["summary"]["node_count"] >= 2
    assert context_graph["summary"]["source_window_count"] >= 1
    assert context_graph["source_windows"][0]["line_count"] <= 80
    assert context_graph["source_windows"][0]["char_count"] <= 4000
    assert context_graph["source_windows"][0]["provenance"]["source"] == "bounded_source_window"
    assert context_graph["expansion_tiers"][0]["hop"] == 0
    assert context_graph["policy"]["comprehensive_context_first"] is True
    assert context_graph["policy"]["bounded_line_numbered_source_windows"] is True
    assert brain["git_ownership"]["by_file"]["dhee/context_firewall.py"]["authors"][0]["name"] == "Dhee Test"
    assert brain["metrics"]["ownership_file_count"] >= 3

    loaded = load_repo_brain(repo)
    assert loaded["ok"] is True
    assert loaded["brain"]["schema_version"] == "dhee.repo_intelligence.v4"


def test_repo_brain_executes_live_lsp_when_supported_server_is_available(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / "repo")
    fake_lsp = tmp_path / "fake_lsp.py"
    _write_fake_lsp_server(fake_lsp)

    monkeypatch.setitem(repo_mod.LSP_SERVER_CANDIDATES, "python", ("fake-lsp",))
    monkeypatch.setitem(repo_mod.LSP_EXECUTABLE_COMMANDS, "fake-lsp", (sys.executable, str(fake_lsp)))
    monkeypatch.setattr(repo_mod.shutil, "which", lambda name: str(fake_lsp) if name == "fake-lsp" else None)

    brain = build_repo_brain(repo, goal="Fix failing context firewall tests")

    live = brain["lsp_index"]["languages"]["python"]["live"]
    assert brain["lsp_index"]["mode"] == "live_enriched"
    assert live["attempted"] is True
    assert live["ok"] is True
    assert live["document_symbol_count"] >= 2
    assert live["reference_count"] >= 1
    assert live["diagnostic_count"] >= 1
    assert any(symbol["name"] == "allow_path" for symbol in live["document_symbols"])
    assert any(diagnostic["path"].startswith("dhee/") for diagnostic in live["diagnostics"])
    assert brain["metrics"]["lsp_live_success_count"] == 1
    assert brain["metrics"]["lsp_live_document_symbol_count"] >= 2
    assert brain["metrics"]["lsp_live_reference_count"] >= 1
    assert brain["metrics"]["lsp_live_diagnostic_count"] >= 1


def test_repo_brain_indexes_typescript_coverage_flaky_and_incremental_delta(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "src").mkdir()
    (repo / "src" / "auth.ts").write_text(
        "import { normalizeToken } from './token'\n\n"
        "export class AuthService {\n"
        "  login(token: string) {\n"
        "    return normalizeToken(token)\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "src" / "token.ts").write_text(
        "export function normalizeToken(token: string) {\n"
        "  return token.trim()\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "src" / "auth.test.ts").write_text(
        "import { AuthService } from './auth'\n\n"
        "test('login normalizes token', () => {\n"
        "  expect(new AuthService().login(' x ')).toBe('x')\n"
        "})\n",
        encoding="utf-8",
    )
    (repo / "coverage.xml").write_text(
        "<?xml version='1.0' ?>\n"
        "<coverage line-rate='0.5'>\n"
        "  <packages><package><classes>\n"
        "    <class filename='dhee/context_firewall.py' line-rate='0.75' branch-rate='0.25'>\n"
        "      <lines><line number='1' hits='1'/><line number='2' hits='0'/></lines>\n"
        "    </class>\n"
        "  </classes></package></packages>\n"
        "</coverage>\n",
        encoding="utf-8",
    )
    (repo / "coverage.json").write_text(
        json.dumps(
            {
                "files": {
                    "src/token.ts": {
                        "summary": {"percent_covered": 50.0, "covered_lines": 1, "num_statements": 2},
                        "missing_lines": [2],
                        "contexts": {
                            "1": ["src/auth.test.ts::test_login|run"],
                            "2": ["src/auth.test.ts::test_login|run"],
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runs = repo / ".dhee" / "context" / "task_runs" / "run-1"
    runs.mkdir(parents=True)
    (runs / "events.jsonl").write_text(
        json.dumps({"action": {"command": "pytest tests/test_context_firewall.py"}, "outcome": "passed"}) + "\n"
        + json.dumps(
            {
                "action": {"command": "pytest tests/test_context_firewall.py"},
                "outcome": "failed",
                "stderr": "FAILED context firewall",
            }
        ) + "\n"
        + json.dumps(
            {
                "action": {"command": "npm test -- src/auth.test.ts"},
                "outcome": "failed",
                "stderr": "FAILED token at src/token.ts:2:1",
            }
        ) + "\n",
        encoding="utf-8",
    )

    first = build_repo_brain(repo, goal="Fix auth login token normalization")
    (repo / "src" / "token.ts").write_text(
        "export function normalizeToken(token: string) {\n"
        "  return token.trim().toLowerCase()\n"
        "}\n",
        encoding="utf-8",
    )
    second = build_repo_brain(repo, goal="Fix auth login token normalization")

    assert any(symbol["path"] == "src/auth.ts" and symbol["name"] == "AuthService" for symbol in second["symbols"])
    assert any(symbol["path"] == "src/auth.ts" and symbol["qualname"] == "AuthService.login" for symbol in second["symbols"])
    assert any(span["path"] == "src/auth.ts" and span["qualname"] == "AuthService.login" for span in second["syntax_index"]["spans"])
    assert any(item["resolved_path"] == "src/token.ts" for item in second["imports"]["src/auth.ts"])
    assert any(call["callee"] == "normalizeToken" for call in second["call_sites"])
    assert any(
        call["callee"] == "normalizeToken"
        and call["caller_qualname"] == "AuthService.login"
        for call in second["call_sites"]
    )
    assert any(
        edge["callee_name"] == "normalizeToken"
        and edge["caller_qualname"] == "AuthService.login"
        for edge in second["call_graph"]
    )
    assert any(edge["source"] == "src/auth.ts" and edge["target"] == "src/token.ts" for edge in second["dependency_graph"]["local_import_edges"])
    assert second["test_map"]["source_to_tests"]["src/auth.ts"][0]["path"] == "src/auth.test.ts"
    assert second["test_map"]["source_to_tests"]["src/auth.ts"][0]["command"] == "npm test -- src/auth.test.ts"
    assert second["coverage_map"]["files"]["dhee/context_firewall.py"]["line_rate"] == 0.75
    assert second["coverage_map"]["files"]["dhee/context_firewall.py"]["uncovered_lines"] == [2]
    assert second["coverage_map"]["files"]["src/token.ts"]["test_contexts"] == ["src/auth.test.ts"]
    assert second["flaky_tests"][0]["status"] == "flaky"
    assert second["failure_index"]["by_file"]["src/token.ts"]["lines"] == [2]
    assert second["test_ownership"]["source_to_tests"]["src/token.ts"][0]["path"] == "src/auth.test.ts"
    assert any(
        "coverage context executed source lines" in reason
        for reason in second["test_ownership"]["source_to_tests"]["src/token.ts"][0]["reasons"]
    )
    assert second["syntax_index"]["active"] is True
    assert "typescript" in second["lsp_index"]["languages"]
    assert second["metrics"]["coverage_file_count"] == 2
    assert second["metrics"]["test_ownership_edge_count"] >= 1
    assert second["metrics"]["flaky_test_count"] == 1
    assert second["metrics"]["failure_file_count"] >= 1
    assert second["metrics"]["syntax_span_count"] >= 2
    assert second["metrics"]["tree_sitter_call_site_count"] >= 1
    assert second["metrics"]["lsp_request_count"] >= 1
    assert second["incremental_index"]["previous_ref"] == first["storage"]["ref"]
    assert "src/token.ts" in second["incremental_index"]["changed_files"]
    assert second["incremental_index"]["source_index_reuse"]["reused_file_count"] >= 1
    assert second["incremental_index"]["syntax_index_reuse"]["reused_file_count"] >= 1

    localization = localize_issue("Fix auth login token normalization", second)
    token_candidate = next(item for item in localization["candidate_files"] if item["path"] == "src/token.ts")
    assert token_candidate["failure_evidence"]["failure_count"] == 1
    assert "failure_index:src/token.ts" in token_candidate["evidence_pointers"]
    assert token_candidate["test_ownership"][0]["path"] == "src/auth.test.ts"
    assert localization["candidate_tests"][0]["path"] == "src/auth.test.ts"
    assert localization["candidate_tests"][0]["reason"] == "owned test from test-ownership index"


def test_repo_brain_persists_route_component_maps_and_impact(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    (repo / "app" / "dashboard").mkdir(parents=True)
    (repo / "app" / "components").mkdir(parents=True)
    (repo / "app" / "api" / "health").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "app" / "dashboard" / "page.tsx").write_text(
        "import { UserPanel } from '../components/UserPanel'\n\n"
        "export default function DashboardPage() {\n"
        "  return <UserPanel />\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "app" / "components" / "UserPanel.tsx").write_text(
        "export function UserPanel() {\n"
        "  return <section />\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "app" / "api" / "health" / "route.ts").write_text(
        "export async function GET() {\n"
        "  return Response.json({ ok: true })\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "src" / "server.ts").write_text(
        "import express from 'express'\n"
        "const router = express.Router()\n"
        "router.post('/login', loginHandler)\n"
        "function loginHandler() { return true }\n",
        encoding="utf-8",
    )
    (repo / "api.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    _run(["git", "add", "."], repo)
    _run(["git", "commit", "-m", "add routes"], repo)

    brain = build_repo_brain(repo, goal="Understand dashboard route impact")

    route_map = brain["route_map"]
    routes = route_map["routes"]
    route_values = {route["route"] for route in routes}
    assert route_map["schema_version"] == "dhee.route_map.v1"
    assert "/dashboard" in route_values
    assert "/api/health" in route_values
    assert "/login" in route_values
    assert "/health" in route_values
    assert brain["metrics"]["route_count"] >= 4

    component_map = brain["component_map"]
    component_names = {component["name"] for component in component_map["components"]}
    assert component_map["schema_version"] == "dhee.component_map.v1"
    assert {"DashboardPage", "UserPanel"}.issubset(component_names)
    assert any(edge["type"] == "uses_component" for edge in component_map["dependency_edges"])
    assert brain["metrics"]["component_count"] >= 2

    graph = repo_graph_from_brain(brain)
    assert graph["node_types"]["route"] >= 4
    assert graph["node_types"]["component"] >= 2
    assert graph["edge_types"]["exposes_route"] >= 4
    assert graph["edge_types"]["renders"] >= 1
    assert graph["edge_types"]["uses_component"] >= 1

    impact = repo_impact(brain, "app/components/UserPanel.tsx", depth=3)
    assert any(route["route"] == "/dashboard" for route in impact["impacted_routes"])
    assert any(component["name"] == "UserPanel" for component in impact["impacted_components"])
    assert any(edge["type"] in {"uses_component", "renders"} for edge in impact["edges"])


def test_repo_localizer_ranks_source_symbols_and_nearest_tests(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    brain = build_repo_brain(repo, goal="Fix failing context firewall tests")

    localization = localize_issue("Fix failing context firewall tests", brain)

    assert localization["schema_version"] == "dhee.repo_localization.v1"
    assert localization["status"] == "localized"
    assert localization["candidate_files"][0]["path"] == "dhee/context_firewall.py"
    assert any(item["path"] == "tests/test_context_firewall.py" for item in localization["candidate_tests"])
    assert any(item["qualname"] == "ContextFirewall" for item in localization["candidate_symbols"])


def test_repo_native_symbol_call_impact_and_explore_queries(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    brain = build_repo_brain(repo, goal="Fix failing context firewall tests")

    search = repo_symbol_search(brain, "ContextFirewall allow_path", limit=5)
    assert search["schema_version"] == "dhee.repo_symbol_search.v1"
    assert any(item["qualname"] == "ContextFirewall.allow_path" for item in search["results"])
    assert search["results"][0]["evidence_pointers"]

    callers = repo_callers(brain, "normalize_path", depth=1)
    assert callers["schema_version"] == "dhee.repo_call_graph_query.v1"
    assert any(edge["type"] == "calls" for edge in callers["edges"])
    assert any("allow_path" in (node.get("label") or "") for node in callers["nodes"])

    callees = repo_callees(brain, "ContextFirewall.allow_path", depth=1)
    assert any("normalize_path" in (node.get("label") or "") for node in callees["nodes"])

    impact = repo_impact(brain, "normalize_path", depth=2)
    assert impact["schema_version"] == "dhee.repo_impact.v1"
    assert any(item["path"] == "dhee/context_firewall.py" for item in impact["impacted_files"])
    assert any(item["path"] == "tests/test_context_firewall.py" for item in impact["candidate_tests"])

    explore = repo_explore(brain, "Fix failing context firewall tests", max_files=4, max_symbols=8)
    assert explore["schema_version"] == "dhee.repo_explore.v1"
    assert explore["source_windows"]
    assert explore["summary"]["source_window_chars"] <= 18000
    assert "numbered_source" in explore["source_windows"][0]


def test_task_contract_consumes_repo_brain_localizer_and_verifier(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    compiled = compile_task_contract("Fix failing context firewall tests", repo=repo)
    contract = compiled["contract"]

    assert contract["repo_intelligence"]["symbol_count"] >= 3
    assert contract["repo_intelligence"]["localization_status"] == "localized"
    assert contract["localization"]["candidate_files"][0]["path"] == "dhee/context_firewall.py"
    assert contract["impact_analysis"]["schema_version"] == "dhee.repo_impact.v1"
    assert any(item["path"] == "tests/test_context_firewall.py" for item in contract["impact_analysis"]["candidate_tests"])
    assert "pytest tests/test_context_firewall.py" in contract["must_run"]
    assert any(
        command.startswith("python3 -m py_compile") and "dhee/context_firewall.py" in command
        for command in contract["verification_card"]["import_smoke_tests"]
    )
    assert "no separate pass-to-pass regression command identified" in contract["verification_card"]["coverage_gaps"]
    assert any(item["kind"] == "localization" for item in contract["compiled_context"]["items"])


def test_mcp_slim_repo_brain_tools(tmp_path):
    from dhee import mcp_slim

    repo = _init_repo(tmp_path / "repo")

    indexed = mcp_slim.HANDLERS["dhee_repo_brain_index"](
        {"repo": str(repo), "goal": "Fix failing context firewall tests"}
    )
    fetched = mcp_slim.HANDLERS["dhee_repo_brain_get"]({"repo": str(repo)})
    localized = mcp_slim.HANDLERS["dhee_repo_brain_localize"](
        {"repo": str(repo), "goal": "Fix failing context firewall tests"}
    )
    graph = mcp_slim.HANDLERS["dhee_repo_graph_export"]({"repo": str(repo), "include_graph": False})
    context_graph = mcp_slim.HANDLERS["dhee_context_graph_query"](
        {"repo": str(repo), "query": "Fix failing context firewall tests", "limit": 100}
    )
    symbol_search = mcp_slim.HANDLERS["dhee_repo_symbol_search"](
        {"repo": str(repo), "query": "ContextFirewall allow_path", "limit": 5}
    )
    callers = mcp_slim.HANDLERS["dhee_repo_callers"](
        {"repo": str(repo), "symbol": "normalize_path"}
    )
    impact = mcp_slim.HANDLERS["dhee_repo_impact"](
        {"repo": str(repo), "symbol_or_path": "normalize_path"}
    )
    explore = mcp_slim.HANDLERS["dhee_repo_explore"](
        {"repo": str(repo), "query": "Fix failing context firewall tests", "max_files": 4}
    )

    assert indexed["repo_intelligence"]["symbol_count"] >= 3
    assert fetched["ok"] is True
    assert fetched["brain"] is None
    assert fetched["repo_intelligence"]["schema_version"] == "dhee.repo_intelligence.v4"
    assert localized["ok"] is True
    assert localized["localization"]["candidate_files"][0]["path"] == "dhee/context_firewall.py"
    assert graph["repo_graph"]["schema_version"] == "dhee.repo_graph_artifact.v1"
    assert graph["repo_graph"]["node_count"] >= 4
    assert context_graph["context_graph"]["policy"]["comprehensive_context_first"] is True
    assert context_graph["context_graph"]["source_windows"]
    assert context_graph["context_graph"]["summary"]["edge_count"] >= 1
    assert symbol_search["symbol_search"]["results"]
    assert callers["callers"]["edges"]
    assert any(item["path"] == "dhee/context_firewall.py" for item in impact["impact"]["impacted_files"])
    assert explore["explore"]["source_windows"]

    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "dhee.cli",
            "context",
            "repo-brain",
            "search",
            "ContextFirewall allow_path",
            "--repo",
            str(repo),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        capture_output=True,
    )
    cli_result = json.loads(cli.stdout)
    assert cli_result["symbol_search"]["results"]
