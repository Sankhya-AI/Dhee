import subprocess
from pathlib import Path

from dhee import repo_link
from dhee.contract_supervisor import record_observation_transition, supervise_action
from dhee.contract_runtime import activate_contract_runtime, contract_runtime_status, deactivate_contract_runtime
from dhee.task_contracts import (
    ACTION_TYPES,
    compile_task_contract,
    create_task_contract,
    get_task_contract,
    import_task_contract,
    interpret_task_contract,
    list_task_contracts,
    validate_task_contract,
)


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
    (path / "dhee" / "context_firewall.py").write_text(
        "def allow_path(path):\n    return not path.startswith('.env')\n",
        encoding="utf-8",
    )
    (path / "tests" / "test_context_firewall.py").write_text(
        "from dhee.context_firewall import allow_path\n\n"
        "def test_env_is_blocked():\n"
        "    assert allow_path('.env') is False\n",
        encoding="utf-8",
    )
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-m", "initial"], path)
    return path


def _edit_proof(test_command: str = "pytest tests/test_context_firewall.py") -> dict:
    return {
        "edit_span": {"path": "dhee/context_firewall.py", "start_line": 1, "end_line": 2},
        "invariant": "context firewall must reject .env paths",
        "related_tests": [test_command],
        "rollback_point": "HEAD",
    }


def test_compile_task_contract_builds_deterministic_actionables(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    compiled = compile_task_contract("Fix failing context firewall tests in Dhee", repo=repo)
    contract = compiled["contract"]
    actions = compiled["actions"]

    assert compiled["format"] == "dhee_task_contract_compile.v1"
    assert contract["schema_version"] == "dhee.task_contract.v1"
    assert contract["goal"] == "Fix failing context firewall tests in Dhee"
    assert contract["mode"] == "patch"
    assert contract["repo"] == "repo"
    assert "dhee/context_firewall.py" in contract["relevant_files"]
    assert "tests/test_context_firewall.py" in contract["relevant_files"]
    assert "pytest tests/test_context_firewall.py" in contract["must_run"]
    assert "dhee/" in contract["allowed_write_paths"]
    assert "tests/" in contract["allowed_write_paths"]
    assert ".env" in contract["forbidden_paths"]
    assert contract["context_budget"]["repo_context_tokens"] == 6000
    assert contract["repo_intelligence"]["schema_version"] == "dhee.repo_intelligence.v4"
    assert contract["impact_analysis"]["schema_version"] == "dhee.repo_impact.v1"
    assert contract["compiled_context"]["schema_version"] == "dhee.context_ledger.v1"
    assert contract["compiled_context"]["items"][0]["why_included"]
    assert contract["verification_card"]["schema_version"] == "dhee.verification_card.v1"
    assert contract["contamination_status"]["schema_version"] == "dhee.contamination_status.v1"
    assert "issue_parse" in [item["name"] for item in compiled["compiler"]["passes"]]

    assert validate_task_contract(compiled)["ok"] is True
    assert compiled["actions_schema"] == "dhee.chotu_action_bytecode.v1"
    assert compiled["compiler"]["schema_version"] == "dhee.contract_compiler.v1"
    assert {action["type"] for action in actions} <= ACTION_TYPES
    assert len({action["action_id"] for action in actions}) == len(actions)
    assert actions[0]["type"] == "SEARCH_CODE"
    assert any(action["type"] == "RUN_TEST" for action in actions)
    assert actions[-1]["type"] == "SUBMIT_PATCH"
    run_ids = {action["action_id"] for action in actions if action["type"] == "RUN_TEST"}
    assert set(actions[-1]["requires"]) == run_ids
    for action in actions:
        for field in ("precondition", "execution", "observation", "postcondition", "memory_update"):
            assert field in action
        assert action["bytecode"]["schema_version"] == "dhee.chotu_action_bytecode.v1"


def test_compile_task_contract_accepts_explicit_must_run_and_budget(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    compiled = compile_task_contract(
        "Fix context firewall",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py -q"],
        context_budget={"state_card_tokens": 100, "retrieved_memory_tokens": 200, "repo_context_tokens": 300, "tool_output_tokens": 400},
    )

    assert compiled["contract"]["must_run"] == ["pytest tests/test_context_firewall.py -q"]
    assert compiled["contract"]["context_budget"]["tool_output_tokens"] == 400
    run_action = next(action for action in compiled["actions"] if action["type"] == "RUN_TEST")
    assert run_action["command"] == "pytest tests/test_context_firewall.py -q"
    assert run_action["precondition"] == "Dependency environment exists and command is safe for the sandbox."


def test_create_task_contract_writes_md_json_and_indexes_repo_context(tmp_path):
    repo = _init_repo(tmp_path / "repo")

    created = create_task_contract("Fix context firewall tests", repo=repo)
    task_id = created["contract"]["task_id"]

    assert created["format"] == "dhee_task_contract_create.v1"
    assert Path(created["paths"]["json"]).exists()
    assert Path(created["paths"]["markdown"]).exists()
    assert "Task Contract" in Path(created["paths"]["markdown"]).read_text(encoding="utf-8")
    listed = list_task_contracts(repo=repo)
    assert [item["task_id"] for item in listed] == [task_id]
    fetched = get_task_contract(task_id, repo=repo)
    assert fetched["contract"]["task_id"] == task_id
    entries = repo_link.list_entries(repo)
    assert any(entry.kind == "task_contract" and entry.meta["task_id"] == task_id for entry in entries)


def test_import_and_interpret_task_contract_in_target_repo(tmp_path):
    source_repo = _init_repo(tmp_path / "source")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=source_repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )

    target_repo = _init_repo(tmp_path / "target")
    imported = import_task_contract(created["paths"]["dir"], repo=target_repo)
    interpreted = interpret_task_contract(imported["contract"]["task_id"], repo=target_repo)

    assert imported["format"] == "dhee_task_contract_import.v1"
    assert interpreted["format"] == "dhee.task_contract_interpretation.v1"
    assert interpreted["readiness"] == "ready"
    assert interpreted["policy"]["auto_execute"] is False
    assert any(step["type"] == "RUN_TEST" for step in interpreted["execution_plan"])
    assert any(diag["code"] == "REPO_ID_MISMATCH" for diag in interpreted["diagnostics"])


def test_interpret_task_contract_blocks_missing_required_file(tmp_path):
    source_repo = _init_repo(tmp_path / "source")
    created = create_task_contract("Fix context firewall tests", repo=source_repo)

    target_repo = _init_repo(tmp_path / "target")
    (target_repo / "dhee" / "context_firewall.py").unlink()
    interpreted = interpret_task_contract(created["paths"]["dir"], repo=target_repo)

    assert interpreted["readiness"] == "blocked"
    assert any(diag["code"] == "READ_PATH_MISSING" for diag in interpreted["diagnostics"])


def test_mcp_slim_task_contract_compile_handler(tmp_path):
    from dhee import mcp_slim

    repo = _init_repo(tmp_path / "repo")

    result = mcp_slim.HANDLERS["dhee_task_contract_compile"](
        {"repo": str(repo), "goal": "Fix context firewall tests"}
    )

    assert result["contract"]["goal"] == "Fix context firewall tests"
    assert result["validation"]["ok"] is True
    assert result["actions"][0]["type"] == "SEARCH_CODE"


def test_mcp_slim_task_contract_create_and_interpret_handlers(tmp_path):
    from dhee import mcp_slim

    repo = _init_repo(tmp_path / "repo")

    created = mcp_slim.HANDLERS["dhee_task_contract_create"](
        {"repo": str(repo), "goal": "Fix context firewall tests"}
    )
    interpreted = mcp_slim.HANDLERS["dhee_task_contract_interpret"](
        {"repo": str(repo), "task_id": created["contract"]["task_id"]}
    )

    assert created["format"] == "dhee_task_contract_create.v1"
    assert interpreted["readiness"] == "ready"


def test_contract_supervisor_allows_and_denies_actions(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )
    task_id = created["contract"]["task_id"]

    allowed = supervise_action(
        task_id,
        {"type": "RUN_TEST", "command": "pytest tests/test_context_firewall.py", "timeout_sec": 120},
        repo=repo,
    )
    denied = supervise_action(
        task_id,
        {"type": "RUN_TEST", "command": "pytest tests/test_unrelated.py", "timeout_sec": 120},
        repo=repo,
    )
    forbidden_edit = supervise_action(
        task_id,
        {"type": "EDIT_FILE", "path": ".env", "patch": "--- a/.env\n+++ b/.env\n"},
        repo=repo,
    )

    assert allowed["decision"] == "allow"
    assert denied["decision"] == "deny"
    assert denied["violations"][0]["code"] == "TEST_COMMAND_OUT_OF_CONTRACT"
    assert forbidden_edit["decision"] == "deny"
    assert any(item["code"] == "EDIT_PATH_FORBIDDEN" for item in forbidden_edit["violations"])


def test_contract_supervisor_enforces_observation_graph(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )
    task_id = created["contract"]["task_id"]

    edit_before_read = supervise_action(
        task_id,
        {
            "type": "EDIT_FILE",
            "path": "dhee/context_firewall.py",
            "patch": "--- a/dhee/context_firewall.py\n+++ b/dhee/context_firewall.py\n",
            "proof": _edit_proof(),
        },
        repo=repo,
    )
    submit_before_tests = supervise_action(
        task_id,
        {"type": "SUBMIT_PATCH", "summary": "Fix context firewall", "tests": ["pytest tests/test_context_firewall.py"]},
        repo=repo,
    )

    assert edit_before_read["decision"] == "deny"
    assert any(item["code"] == "EDIT_REQUIRES_READ_OBSERVATION" for item in edit_before_read["violations"])
    assert submit_before_tests["decision"] == "deny"
    assert any(item["code"] == "SUBMIT_REQUIRES_PASSING_TESTS" for item in submit_before_tests["violations"])

    record_observation_transition(
        task_id,
        {"type": "READ_FILE", "path": "dhee/context_firewall.py"},
        "Read allow_path implementation",
        repo=repo,
        outcome="observed",
    )
    edit_after_read = supervise_action(
        task_id,
        {
            "type": "EDIT_FILE",
            "path": "dhee/context_firewall.py",
            "patch": "--- a/dhee/context_firewall.py\n+++ b/dhee/context_firewall.py\n",
            "proof": _edit_proof(),
        },
        repo=repo,
    )
    assert edit_after_read["decision"] == "allow"

    record_observation_transition(
        task_id,
        {"type": "RUN_TEST", "command": "pytest tests/test_context_firewall.py", "timeout_sec": 120},
        "1 passed",
        repo=repo,
        outcome="passed",
    )
    submit_after_tests = supervise_action(
        task_id,
        {"type": "SUBMIT_PATCH", "summary": "Fix context firewall", "tests": ["pytest tests/test_context_firewall.py"]},
        repo=repo,
    )

    assert submit_after_tests["decision"] == "allow"
    assert "pytest tests/test_context_firewall.py" in submit_after_tests["runtime_state"]["passed_tests"]
    assert submit_after_tests["proof_bundle_preview"]["proof_bundle"]["verifier_result"]["status"] == "passed"

    submitted = record_observation_transition(
        task_id,
        {"type": "SUBMIT_PATCH", "summary": "Fix context firewall", "tests": ["pytest tests/test_context_firewall.py"]},
        "Ready to submit",
        repo=repo,
        outcome="submitted",
    )
    proof_bundle = submitted["proof_bundle"]["proof_bundle"]
    assert proof_bundle["schema_version"] == "dhee.proof_bundle.v1"
    assert proof_bundle["contract_id"] == task_id
    assert proof_bundle["verifier_result"]["status"] == "passed"
    assert proof_bundle["tests_run"][0]["command"] == "pytest tests/test_context_firewall.py"
    assert Path(submitted["proof_bundle"]["paths"]["proof_bundle"]).exists()


def test_contract_supervisor_requires_edit_proof_obligations(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )
    task_id = created["contract"]["task_id"]
    record_observation_transition(
        task_id,
        {"type": "READ_FILE", "path": "dhee/context_firewall.py"},
        "Read failing implementation",
        repo=repo,
        outcome="observed",
    )

    denied = supervise_action(
        task_id,
        {
            "type": "EDIT_FILE",
            "path": "dhee/context_firewall.py",
            "patch": "--- a/dhee/context_firewall.py\n+++ b/dhee/context_firewall.py\n",
        },
        repo=repo,
    )
    allowed = supervise_action(
        task_id,
        {
            "type": "EDIT_FILE",
            "path": "dhee/context_firewall.py",
            "patch": "--- a/dhee/context_firewall.py\n+++ b/dhee/context_firewall.py\n",
            "proof": _edit_proof(),
        },
        repo=repo,
    )

    assert denied["decision"] == "deny"
    assert any(item["code"] == "EDIT_PROOF_OBLIGATION_MISSING" for item in denied["violations"])
    assert allowed["decision"] == "allow"


def test_contract_supervisor_rejects_invalid_edit_span(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )
    task_id = created["contract"]["task_id"]
    record_observation_transition(
        task_id,
        {"type": "READ_FILE", "path": "dhee/context_firewall.py"},
        "Read failing implementation",
        repo=repo,
        outcome="observed",
    )
    bad_proof = _edit_proof()
    bad_proof["edit_span"] = {"path": "tests/test_context_firewall.py", "start_line": 1, "end_line": 2}

    denied = supervise_action(
        task_id,
        {
            "type": "EDIT_FILE",
            "path": "dhee/context_firewall.py",
            "patch": "--- a/dhee/context_firewall.py\n+++ b/dhee/context_firewall.py\n",
            "proof": bad_proof,
        },
        repo=repo,
    )

    assert denied["decision"] == "deny"
    assert any(item["code"] == "EDIT_SPAN_INVALID" for item in denied["violations"])


def test_contract_supervisor_blocks_submit_with_unrelated_changed_file(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )
    task_id = created["contract"]["task_id"]
    record_observation_transition(
        task_id,
        {"type": "RUN_TEST", "command": "pytest tests/test_context_firewall.py", "timeout_sec": 120},
        "1 passed",
        repo=repo,
        outcome="passed",
    )
    (repo / "README.md").write_text("unrelated change\n", encoding="utf-8")

    denied = supervise_action(
        task_id,
        {"type": "SUBMIT_PATCH", "summary": "Fix context firewall", "tests": ["pytest tests/test_context_firewall.py"]},
        repo=repo,
    )

    assert denied["decision"] == "deny"
    assert any(item["code"] == "SUBMIT_CHANGED_PATH_OUT_OF_CONTRACT" for item in denied["violations"])
    assert denied["proof_bundle_preview"]["proof_bundle"]["verifier_result"]["status"] == "blocked"


def test_contract_supervisor_records_observation_transition(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )

    result = record_observation_transition(
        created["contract"]["task_id"],
        {"type": "RUN_TEST", "command": "pytest tests/test_context_firewall.py", "timeout_sec": 120},
        "1 failed: allow_path returned true for .env",
        repo=repo,
        outcome="failed",
        next_action={"type": "READ_FILE", "path": "dhee/context_firewall.py", "reason": "Inspect failing implementation"},
    )

    assert result["format"] == "dhee_contract_observation_record.v1"
    assert result["decision"]["decision"] == "allow"
    assert result["next_decision"]["decision"] == "allow"
    assert result["checkpoint"]["checkpoint"]["stage"] == "after_failing_test"
    events_path = Path(result["paths"]["events"])
    assert events_path.exists()
    assert "allow_path returned true" in events_path.read_text(encoding="utf-8")


def test_active_contract_runtime_refuses_router_calls_and_records_observations(tmp_path):
    from dhee.router.handlers import handle_dhee_bash, handle_dhee_grep, handle_dhee_read

    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["python3 -m pytest tests/test_context_firewall.py"],
    )
    task_id = created["contract"]["task_id"]

    activated = activate_contract_runtime(task_id, repo=repo, agent_id="test", harness="pytest")
    assert activated["active"] is True
    assert contract_runtime_status(repo=repo)["task_id"] == task_id

    allowed_read = handle_dhee_read({"file_path": str(repo / "dhee" / "context_firewall.py")})
    denied_read = handle_dhee_read({"file_path": str(repo / "README.md")})
    denied_search = handle_dhee_grep({"repo": str(repo), "path": str(repo), "pattern": "totally_unrelated_symbol"})
    denied_bash = handle_dhee_bash({"cwd": str(repo), "command": "python3 -m pytest tests/test_unrelated.py", "timeout": 30})

    assert allowed_read["contract_runtime"]["decision"] == "allow"
    assert allowed_read["contract_runtime"]["observation"]["outcome"] == "observed"
    assert denied_read["format"] == "dhee.contract_tool_refusal.v1"
    assert denied_read["will_execute"] is False
    assert "READ_PATH_OUT_OF_CONTRACT" in denied_read["violation_codes"]
    assert denied_search["format"] == "dhee.contract_tool_refusal.v1"
    assert "SEARCH_QUERY_OUT_OF_CONTRACT" in denied_search["violation_codes"]
    assert denied_bash["format"] == "dhee.contract_tool_refusal.v1"
    assert "TEST_COMMAND_OUT_OF_CONTRACT" in denied_bash["violation_codes"]

    edit_after_router_read = supervise_action(
        task_id,
        {
            "type": "EDIT_FILE",
            "path": "dhee/context_firewall.py",
            "patch": "--- a/dhee/context_firewall.py\n+++ b/dhee/context_firewall.py\n",
            "proof": _edit_proof("python3 -m pytest tests/test_context_firewall.py"),
        },
        repo=repo,
    )
    assert edit_after_router_read["decision"] == "allow"

    passed_test = handle_dhee_bash({"cwd": str(repo), "command": "python3 -m pytest tests/test_context_firewall.py", "timeout": 60})
    assert passed_test["exit_code"] == 0
    assert passed_test["contract_runtime"]["observation"]["outcome"] == "passed"

    submit = supervise_action(
        task_id,
        {"type": "SUBMIT_PATCH", "summary": "Fix context firewall", "tests": ["python3 -m pytest tests/test_context_firewall.py"]},
        repo=repo,
    )
    assert submit["decision"] == "allow"

    deactivated = deactivate_contract_runtime(repo=repo, agent_id="test", reason="test complete")
    assert deactivated["active"] is False


def test_pre_tool_gate_blocks_native_edit_under_active_contract(tmp_path):
    from dhee.router.pre_tool_gate import evaluate

    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )
    activate_contract_runtime(created["contract"]["task_id"], repo=repo, agent_id="test")

    denied = evaluate(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(repo / "dhee" / "context_firewall.py"),
                "old_string": "return True",
                "new_string": "return not path.startswith('.env')",
            },
        }
    )

    assert denied["permissionDecision"] == "deny"
    assert "EDIT_PROOF_OBLIGATION_MISSING" in denied["additionalContext"]


def test_router_can_require_active_contract_for_coding_actions(tmp_path, monkeypatch):
    from dhee.router.handlers import handle_dhee_read

    repo = _init_repo(tmp_path / "repo")
    monkeypatch.setenv("DHEE_REQUIRE_ACTIVE_CONTRACT", "1")

    denied = handle_dhee_read({"file_path": str(repo / "dhee" / "context_firewall.py")})

    assert denied["format"] == "dhee.contract_tool_refusal.v1"
    assert "ACTIVE_CONTRACT_REQUIRED" in denied["violation_codes"]


def test_mcp_slim_contract_supervisor_handlers(tmp_path):
    from dhee import mcp_slim

    repo = _init_repo(tmp_path / "repo")
    created = create_task_contract(
        "Fix context firewall tests",
        repo=repo,
        must_run=["pytest tests/test_context_firewall.py"],
    )

    decision = mcp_slim.HANDLERS["dhee_contract_supervise_action"](
        {
            "repo": str(repo),
            "task_id": created["contract"]["task_id"],
            "action": {"type": "RUN_TEST", "command": "pytest tests/test_context_firewall.py", "timeout_sec": 120},
        }
    )
    event = mcp_slim.HANDLERS["dhee_contract_record_observation"](
        {
            "repo": str(repo),
            "task_id": created["contract"]["task_id"],
            "action": {"type": "RUN_TEST", "command": "pytest tests/test_context_firewall.py", "timeout_sec": 120},
            "observation": "passed",
            "outcome": "passed",
        }
    )
    proof = mcp_slim.HANDLERS["dhee_contract_proof_bundle"](
        {"repo": str(repo), "task_id": created["contract"]["task_id"], "persist": False}
    )
    runtime = mcp_slim.HANDLERS["dhee_contract_runtime_activate"](
        {"repo": str(repo), "task_id": created["contract"]["task_id"], "agent_id": "pytest"}
    )
    runtime_status = mcp_slim.HANDLERS["dhee_contract_runtime_status"]({"repo": str(repo)})
    runtime_deactivated = mcp_slim.HANDLERS["dhee_contract_runtime_deactivate"](
        {"repo": str(repo), "reason": "test complete"}
    )

    assert decision["decision"] == "allow"
    assert event["event"]["outcome"] == "passed"
    assert proof["proof_bundle"]["verifier_result"]["status"] == "passed"
    assert proof["paths"] == {}
    assert runtime["active"] is True
    assert runtime_status["task_id"] == created["contract"]["task_id"]
    assert runtime_deactivated["active"] is False


def test_cli_context_task_parser_accepts_compile_goal():
    from dhee.cli import build_parser

    args = build_parser().parse_args(
        ["context", "task", "compile", "Fix context firewall", "--repo", ".", "--must-run", "pytest tests/test_context_firewall.py", "--json"]
    )

    assert args.context_action == "task"
    assert args.entry_id == "compile"
    assert args.context_args == ["Fix context firewall"]
    assert args.must_run == ["pytest tests/test_context_firewall.py"]


def test_cli_context_task_parser_accepts_interpret():
    from dhee.cli import build_parser

    args = build_parser().parse_args(
        ["context", "task", "interpret", "task_123", "--repo", ".", "--strict", "--json"]
    )

    assert args.context_action == "task"
    assert args.entry_id == "interpret"
    assert args.context_args == ["task_123"]
    assert args.strict is True


def test_cli_context_task_parser_accepts_supervise_action_json():
    from dhee.cli import build_parser

    args = build_parser().parse_args(
        [
            "context",
            "task",
            "supervise",
            "task_123",
            "--repo",
            ".",
            "--action-json",
            '{"type":"RUN_TEST","command":"pytest tests/test_context_firewall.py","timeout_sec":120}',
            "--json",
        ]
    )

    assert args.context_action == "task"
    assert args.entry_id == "supervise"
    assert args.context_args == ["task_123"]
    assert "RUN_TEST" in args.action_json


def test_cli_context_task_parser_accepts_runtime_activation():
    from dhee.cli import build_parser

    args = build_parser().parse_args(
        ["context", "task", "activate", "task_123", "--repo", ".", "--strict", "--force", "--json"]
    )

    assert args.context_action == "task"
    assert args.entry_id == "activate"
    assert args.context_args == ["task_123"]
    assert args.strict is True
    assert args.force is True


def test_cli_context_task_parser_accepts_proof_bundle():
    from dhee.cli import build_parser

    args = build_parser().parse_args(
        ["context", "task", "proof", "task_123", "--repo", ".", "--dry-run", "--json"]
    )

    assert args.context_action == "task"
    assert args.entry_id == "proof"
    assert args.context_args == ["task_123"]
    assert args.dry_run is True


def test_cli_context_task_proof_dispatches_to_proof_bundle(monkeypatch, capsys):
    from dhee.cli import build_parser, cmd_context
    import dhee.contract_supervisor as contract_supervisor

    called = {}

    def fake_build_proof_bundle(task_contract, *, repo=None, strict=False, persist=True):
        called["task_contract"] = task_contract
        called["repo"] = repo
        called["persist"] = persist
        return {
            "format": "dhee_contract_proof_bundle.v1",
            "proof_bundle": {
                "contract_id": task_contract,
                "verifier_result": {
                    "status": "passed",
                    "passed_tests": [],
                    "required_tests": [],
                    "out_of_contract_changed_paths": [],
                    "forbidden_changed_paths": [],
                },
            },
            "paths": {},
        }

    monkeypatch.setattr(contract_supervisor, "build_proof_bundle", fake_build_proof_bundle)
    args = build_parser().parse_args(
        ["context", "task", "proof", "task_123", "--repo", ".", "--dry-run"]
    )
    cmd_context(args)

    assert called == {"task_contract": "task_123", "repo": ".", "persist": False}
    assert "Proof bundle task_123" in capsys.readouterr().out
