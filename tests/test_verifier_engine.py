import subprocess
from pathlib import Path

from dhee.verifier_engine import (
    VERIFIER_ENGINE_PLAN_SCHEMA,
    VERIFIER_ENGINE_RUN_SCHEMA,
    build_verifier_plan,
    run_verifier_engine,
)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "dhee-test@example.com"], path)
    _run(["git", "config", "user.name", "Dhee Test"], path)
    (path / "pkg").mkdir()
    (path / "tests").mkdir()
    (path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (path / "pkg" / "feature.py").write_text("def ok():\n    return True\n", encoding="utf-8")
    _run(["git", "add", "."], path)
    _run(["git", "commit", "-m", "initial"], path)
    return path


def _write_test(repo: Path, name: str, body: str) -> str:
    path = repo / "tests" / name
    path.write_text(body, encoding="utf-8")
    return "pytest " + path.relative_to(repo).as_posix()


def _compiled(command: str, **card_overrides):
    card = {
        "schema_version": "dhee.verification_card.v1",
        "fail_to_pass_tests": [command],
        "pass_to_pass_tests": [],
        "nearest_tests": [],
        "import_smoke_tests": [],
        "static_checks": [],
        "security_checks": [],
    }
    card.update(card_overrides)
    return {
        "format": "dhee_task_contract_compile.v1",
        "contract": {
            "schema_version": "dhee.task_contract.v1",
            "task_id": "verifier-engine-task",
            "goal": "Verify a tiny change",
            "must_run": list(card.get("fail_to_pass_tests") or [command]),
            "forbidden_paths": [".env", ".env.*"],
            "verification_card": card,
        },
        "actions": [],
    }


def _baseline(command: str, status: str = "failed"):
    return {
        "baseline_results": {
            command: {
                "status": status,
                "evidence_key": "baseline.pre_change",
            }
        }
    }


def test_required_fail_to_pass_with_failed_baseline_and_post_pass_verifies(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    command = _write_test(
        repo,
        "test_pass.py",
        "from pkg.feature import ok\n\n\ndef test_ok():\n    assert ok()\n",
    )
    compiled = _compiled(command)

    plan = build_verifier_plan(compiled, repo=repo, include_static=False, include_security=False)
    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(command, "failed"),
        include_static=False,
        include_security=False,
        persist=False,
    )

    assert plan["schema_version"] == VERIFIER_ENGINE_PLAN_SCHEMA
    assert result["schema_version"] == VERIFIER_ENGINE_RUN_SCHEMA
    assert result["status"] == "passed"
    assert result["commands"][0]["status"] == "passed"
    assert result["fail_to_pass_transitions"][0]["verified"] is True
    assert result["gate_summary"]["status"] == "passed"
    assert result["proof_bundle"]["verifier_result"]["source"] == "dhee.verifier_engine"
    assert result["proof_bundle"]["verifier_result"]["status"] == "passed"
    assert result["proof_bundle"]["contract_supervisor_verifier_result"]["status"] == "blocked"
    assert result["policy"]["no_shell"] is True
    assert result["policy"]["no_llm"] is True


def test_required_fail_to_pass_post_failure_fails_gate(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    command = _write_test(repo, "test_fail.py", "def test_fail():\n    assert False\n")
    compiled = _compiled(command)

    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(command, "failed"),
        include_static=False,
        include_security=False,
        persist=False,
    )

    assert result["status"] == "failed"
    assert result["commands"][0]["status"] == "failed"
    assert result["fail_to_pass_transitions"][0]["transition_status"] == "failed"
    assert command in result["gate_summary"]["failed_required_commands"]


def test_required_unsafe_command_blocks_gate_without_execution(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    command = 'python -c "print(1)"'
    compiled = _compiled(command)

    result = run_verifier_engine(
        compiled,
        repo=repo,
        include_static=False,
        include_security=False,
        persist=False,
    )

    assert result["status"] == "blocked"
    assert result["commands"][0]["status"] == "blocked"
    assert result["commands"][0]["attempts"][0]["executed"] is False
    assert "allowed -m module" in result["commands"][0]["blocked_reason"]


def test_required_pass_to_pass_failure_fails_gate(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    fail_to_pass = _write_test(repo, "test_fix.py", "def test_fix():\n    assert True\n")
    pass_to_pass = _write_test(repo, "test_regression.py", "def test_regression():\n    assert False\n")
    compiled = _compiled(fail_to_pass, pass_to_pass_tests=[pass_to_pass])

    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(fail_to_pass, "failed"),
        include_static=False,
        include_security=False,
        persist=False,
    )

    assert result["status"] == "failed"
    assert result["pass_to_pass_results"][0]["status"] == "failed"
    assert pass_to_pass in result["gate_summary"]["failed_required_commands"]


def test_optional_nearest_failure_makes_gate_partial_not_passed(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    fail_to_pass = _write_test(repo, "test_fix.py", "def test_fix():\n    assert True\n")
    nearest_rel = "tests/test_nearest.py"
    (repo / nearest_rel).write_text("def test_nearest():\n    assert False\n", encoding="utf-8")
    compiled = _compiled(fail_to_pass, nearest_tests=[nearest_rel])

    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(fail_to_pass, "failed"),
        include_static=False,
        include_security=False,
        persist=False,
    )

    assert result["status"] == "partial"
    assert result["nearest_test_results"][0]["status"] == "failed"
    assert result["gate_summary"]["optional_nearest_failed_commands"] == ["pytest tests/test_nearest.py"]


def test_known_flaky_command_requires_stable_pass_across_attempts(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    fail_to_pass = _write_test(repo, "test_fix.py", "def test_fix():\n    assert True\n")
    flaky = _write_test(
        repo,
        "test_flaky.py",
        "from pathlib import Path\n\n"
        "def test_flaky():\n"
        "    marker = Path('.flaky-count')\n"
        "    count = int(marker.read_text()) if marker.exists() else 0\n"
        "    marker.write_text(str(count + 1))\n"
        "    assert count == 0\n",
    )
    compiled = _compiled(
        fail_to_pass,
        pass_to_pass_tests=[flaky],
        flaky_tests=[flaky],
    )

    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(fail_to_pass, "failed"),
        include_static=False,
        include_security=False,
        flaky_attempts=2,
        persist=False,
    )

    flaky_result = result["pass_to_pass_results"][0]
    assert result["status"] == "failed"
    assert flaky_result["status"] == "failed"
    assert [attempt["status"] for attempt in flaky_result["attempts"]] == ["passed", "failed"]
    assert result["flaky_evidence"][0]["stable_pass"] is False


def test_security_builtin_failure_propagates_to_gate(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    command = _write_test(repo, "test_fix.py", "def test_fix():\n    assert True\n")
    (repo / ".env").write_text("TOKEN=do-not-commit\n", encoding="utf-8")
    compiled = _compiled(command, security_checks=["verify forbidden path changes"])

    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(command, "failed"),
        include_static=False,
        include_security=True,
        persist=False,
    )

    assert result["status"] == "failed"
    assert result["security_results"][0]["status"] == "failed"
    assert "builtin:verify forbidden path changes" in result["gate_summary"]["failed_required_commands"]


def test_static_py_compile_result_and_stable_command_ids_are_deterministic(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    command = _write_test(repo, "test_fix.py", "def test_fix():\n    assert True\n")
    compiled = _compiled(
        command,
        import_smoke_tests=["python -m py_compile pkg/feature.py"],
    )

    first_plan = build_verifier_plan(compiled, repo=repo, include_security=False)
    second_plan = build_verifier_plan(compiled, repo=repo, include_security=False)
    result = run_verifier_engine(
        compiled,
        repo=repo,
        baseline_plan=_baseline(command, "failed"),
        include_security=False,
        persist=False,
    )

    assert [item["command_id"] for item in first_plan["commands"]] == [
        item["command_id"] for item in second_plan["commands"]
    ]
    assert result["status"] == "passed"
    assert result["static_results"][0]["status"] == "passed"
    assert all(command_result["evidence_key"].endswith(".final") for command_result in result["commands"])
