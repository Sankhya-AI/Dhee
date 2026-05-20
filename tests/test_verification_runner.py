import subprocess
from pathlib import Path

from dhee import mcp_slim
from dhee.task_contracts import compile_task_contract
from dhee.verification_runner import build_verification_plan, run_verification


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
        "def allow_path(path):\n"
        "    return not str(path).startswith('.env')\n",
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


def test_verification_runner_executes_card_records_events_and_persists(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    compiled = compile_task_contract("Fix failing context firewall tests", repo=repo)

    plan = build_verification_plan(compiled, repo=repo, include_pass_to_pass=False, include_security=False)
    assert plan["schema_version"] == "dhee.verification_plan.v1"
    assert any(item["kind"] == "fail_to_pass" for item in plan["commands"])

    result = run_verification(
        compiled,
        repo=repo,
        timeout_sec=30,
        include_pass_to_pass=False,
        include_security=False,
    )

    run = result["verification_run"]
    assert run["schema_version"] == "dhee.verification_run.v1"
    assert run["status"] == "passed"
    assert Path(result["paths"]["verification_run"]).exists()
    fail_to_pass = [item for item in run["results"] if item["kind"] == "fail_to_pass"]
    assert fail_to_pass
    assert fail_to_pass[0]["status"] == "passed"
    assert fail_to_pass[0]["recorded_observation"]["accepted"] is True
    assert run["proof_bundle"]["verifier_result"]["status"] == "passed"


def test_verification_runner_fails_when_required_test_fails(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    compiled = compile_task_contract("Fix failing context firewall tests", repo=repo)
    (repo / "dhee" / "context_firewall.py").write_text(
        "def allow_path(path):\n"
        "    return True\n",
        encoding="utf-8",
    )

    result = run_verification(
        compiled,
        repo=repo,
        timeout_sec=30,
        include_pass_to_pass=False,
        include_static=False,
        include_security=False,
    )

    run = result["verification_run"]
    assert run["status"] == "failed"
    assert "pytest tests/test_context_firewall.py" in run["summary"]["failed_required_commands"]
    fail_to_pass = next(item for item in run["results"] if item["kind"] == "fail_to_pass")
    assert fail_to_pass["status"] == "failed"
    assert fail_to_pass["recorded_observation"]["accepted"] is True
    assert run["proof_bundle"]["verifier_result"]["status"] == "failed"


def test_verification_runner_blocks_unsafe_commands_without_shell(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    compiled = compile_task_contract(
        "Run dangerous verifier",
        repo=repo,
        must_run=["rm -rf /"],
    )

    result = run_verification(
        compiled,
        repo=repo,
        include_pass_to_pass=False,
        include_static=False,
        include_security=False,
    )

    run = result["verification_run"]
    assert run["status"] == "blocked"
    assert run["results"][0]["status"] == "blocked"
    assert "not allowed" in run["results"][0]["stderr_tail"]


def test_mcp_slim_exposes_verification_runner(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    compiled = compile_task_contract("Fix failing context firewall tests", repo=repo)

    result = mcp_slim.HANDLERS["dhee_contract_run_verification"](
        {
            "contract": compiled,
            "repo": str(repo),
            "timeout_sec": 30,
            "include_pass_to_pass": False,
            "include_security": False,
        }
    )

    assert result["verification_run"]["status"] == "passed"
