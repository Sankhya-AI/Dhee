import subprocess

from dhee.replay_branching import (
    BRANCH_FAILED_TEST,
    BRANCH_LOCALIZATION,
    BRANCH_PATCH,
    BRANCH_RECOVERY,
    BRANCH_SEQUENCE,
    BRANCH_SUBMIT,
    READ_ONLY_GIT_COMMANDS,
    REPLAY_BRANCHING_SCHEMA,
    ReplayBranchingEngine,
    build_replay_branch_plan,
    build_swe_replay_plan,
)


FIXED_TIME = "2026-05-20T00:00:00Z"


def _sample_plan(tmp_path, **overrides):
    kwargs = {
        "repo": tmp_path,
        "task_id": "task-replay-branching",
        "base_ref": "abc1234",
        "current_branch": "main",
        "relevant_files": ["dhee/replay_branching.py", "tests/test_replay_branching.py"],
        "patch_files": ["dhee/replay_branching.py"],
        "test_commands": ["pytest tests/test_replay_branching.py"],
        "failing_tests": [
            {
                "command": "pytest tests/test_replay_branching.py::test_failed_path",
                "status": "failed",
                "evidence_ref": "artifact://failed-test-log",
                "failure_signature": "assert branch graph",
            }
        ],
        "created_at": FIXED_TIME,
    }
    kwargs.update(overrides)
    return build_replay_branch_plan("Build replay branching engine", **kwargs)


def test_replay_plan_builds_required_swe_branch_graph(tmp_path):
    plan = _sample_plan(tmp_path)

    assert plan["schema_version"] == REPLAY_BRANCHING_SCHEMA
    assert plan["branch_sequence"] == list(BRANCH_SEQUENCE)
    assert [branch["kind"] for branch in plan["branches"]] == list(BRANCH_SEQUENCE)
    assert len(plan["branches"]) == 5
    assert len(plan["checkpoints"]) == 5
    assert len(plan["rollback_points"]) == 5
    assert plan["execution_policy"]["planner_only"] is True
    assert plan["execution_policy"]["git_mutations_executed"] is False

    by_kind = {branch["kind"]: branch for branch in plan["branches"]}
    assert by_kind[BRANCH_LOCALIZATION]["parent_id"] is None
    assert by_kind[BRANCH_PATCH]["parent_id"] == by_kind[BRANCH_LOCALIZATION]["branch_id"]
    assert by_kind[BRANCH_FAILED_TEST]["parent_id"] == by_kind[BRANCH_PATCH]["branch_id"]
    assert by_kind[BRANCH_RECOVERY]["parent_id"] == by_kind[BRANCH_FAILED_TEST]["branch_id"]
    assert by_kind[BRANCH_SUBMIT]["parent_id"] == by_kind[BRANCH_RECOVERY]["branch_id"]

    assert by_kind[BRANCH_LOCALIZATION]["child_ids"] == [by_kind[BRANCH_PATCH]["branch_id"]]
    assert by_kind[BRANCH_SUBMIT]["child_ids"] == []
    assert by_kind[BRANCH_PATCH]["rollback_to"].startswith("checkpoint:")
    assert by_kind[BRANCH_LOCALIZATION]["rollback_to"] == "abc1234"
    assert all(branch["name"].startswith("swe-replay/task-replay-branching/") for branch in plan["branches"])


def test_replay_plan_ids_are_deterministic_for_canonical_inputs(tmp_path):
    first = _sample_plan(
        tmp_path,
        relevant_files=["tests/test_replay_branching.py", "dhee/replay_branching.py"],
        patch_files=["dhee/replay_branching.py"],
        test_commands=["pytest tests/test_replay_branching.py"],
    )
    second = _sample_plan(
        tmp_path,
        relevant_files=["dhee/replay_branching.py", "tests/test_replay_branching.py"],
        patch_files=["dhee/replay_branching.py"],
        test_commands=["pytest tests/test_replay_branching.py"],
    )
    different = _sample_plan(tmp_path, task_id="task-other")

    assert first["plan_id"] == second["plan_id"]
    assert [branch["branch_id"] for branch in first["branches"]] == [
        branch["branch_id"] for branch in second["branches"]
    ]
    assert [event["event_id"] for event in first["audit_events"]] == [
        event["event_id"] for event in second["audit_events"]
    ]
    assert first["plan_id"] != different["plan_id"]


def test_failed_test_recovery_and_submit_metadata_are_linked(tmp_path):
    plan = _sample_plan(tmp_path)
    by_kind = {branch["kind"]: branch for branch in plan["branches"]}
    checkpoints_by_id = {item["checkpoint_id"]: item for item in plan["checkpoints"]}
    rollback_by_id = {item["rollback_point_id"]: item for item in plan["rollback_points"]}

    failed = by_kind[BRANCH_FAILED_TEST]
    patch = by_kind[BRANCH_PATCH]
    recovery = by_kind[BRANCH_RECOVERY]
    submit = by_kind[BRANCH_SUBMIT]

    assert failed["metadata"]["failing_tests"][0]["evidence_ref"] == "artifact://failed-test-log"
    assert failed["metadata"]["failure_rollback_target_branch_id"] == patch["branch_id"]
    assert failed["metadata"]["failure_rollback_target_checkpoint_id"] == patch["checkpoint_id"]
    assert recovery["metadata"]["recovers_from_branch_id"] == failed["branch_id"]
    assert recovery["metadata"]["recovery_starts_at_checkpoint_id"] == patch["checkpoint_id"]
    assert submit["metadata"]["requires_successful_branch_id"] == recovery["branch_id"]
    assert any(gate["kind"] == "test" for gate in submit["metadata"]["submit_gates"])

    for branch in plan["branches"]:
        assert branch["checkpoint_id"] in checkpoints_by_id
        assert branch["rollback_point_id"] in rollback_by_id
        assert checkpoints_by_id[branch["checkpoint_id"]]["rollback_point_id"] == branch["rollback_point_id"]

    event_types = [event["event_type"] for event in plan["audit_events"]]
    assert event_types[0] == "plan.created"
    assert "branch.linked" in event_types
    assert event_types[-1] == "plan.ready"
    assert [event["sequence"] for event in plan["audit_events"]] == list(range(1, len(plan["audit_events"]) + 1))


def test_engine_and_swe_alias_use_same_planner(tmp_path):
    engine = ReplayBranchingEngine(branch_namespace="custom/replay", actor="worker-6")
    engine_plan = engine.build_plan(
        "Build replay branching engine",
        repo=tmp_path,
        task_id="task-replay-branching",
        base_ref="abc1234",
        current_branch="main",
        created_at=FIXED_TIME,
    )
    alias_plan = build_swe_replay_plan(
        "Build replay branching engine",
        repo=tmp_path,
        task_id="task-replay-branching",
        base_ref="abc1234",
        current_branch="main",
        branch_namespace="custom/replay",
        actor="worker-6",
        created_at=FIXED_TIME,
    )

    assert engine_plan["plan_id"] == alias_plan["plan_id"]
    assert engine_plan["branch_namespace"] == "custom/replay"
    assert all(event["actor"] == "worker-6" for event in engine_plan["audit_events"])


def test_git_observation_is_read_only(monkeypatch, tmp_path):
    calls = []

    class Result:
        def __init__(self, stdout="", returncode=0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(args, **kwargs):
        calls.append(args)
        git_args = tuple(args[3:])
        assert git_args in READ_ONLY_GIT_COMMANDS
        if git_args == ("rev-parse", "--show-toplevel"):
            return Result(str(tmp_path))
        if git_args == ("rev-parse", "--short", "HEAD"):
            return Result("abc1234")
        if git_args == ("branch", "--show-current"):
            return Result("main")
        if git_args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return Result(" M dhee/replay_branching.py\n?? tests/test_replay_branching.py\n")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    plan = build_replay_branch_plan(
        "Build replay branching engine",
        repo=tmp_path,
        task_id="task-replay-branching",
        created_at=FIXED_TIME,
    )

    assert calls
    assert plan["repo"]["base_ref"] == "abc1234"
    assert plan["repo"]["current_branch"] == "main"
    assert plan["repo"]["dirty"] is True
    assert plan["repo"]["changed_paths"] == ["dhee/replay_branching.py", "tests/test_replay_branching.py"]
