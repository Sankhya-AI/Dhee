import hashlib
import json
import subprocess
from pathlib import Path

from dhee.patch_families import (
    build_patch_family_plan,
    execute_cleanup_plan,
    execute_worktree_creation,
    rank_patch_family_results,
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


def _candidate_diff() -> str:
    return (
        "--- a/dhee/context_firewall.py\n"
        "+++ b/dhee/context_firewall.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def allow_path(path):\n"
        "-    return True\n"
        "+    return not str(path).startswith('.env')\n"
    )


def test_patch_family_plan_is_deterministic_and_metadata_only(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    raw_diff = _candidate_diff()
    candidate = {
        "family": "minimal_fix",
        "title": "Tighten context firewall",
        "summary": "Smallest correction for .env rejection.",
        "diff": raw_diff,
        "risk": "low",
        "confidence": 0.8,
        "metadata": {"source_agent": "worker-5", "patch": "must be stripped"},
    }
    verifiers = [
        "pytest tests/test_context_firewall.py -q",
        {"command": "python -m pytest tests/test_context_firewall.py", "kind": "pass_to_pass", "required": False},
    ]

    plan1 = build_patch_family_plan(repo, [candidate], verifiers, worktree_root=tmp_path / "worktrees")
    plan2 = build_patch_family_plan(repo, [candidate], verifiers, worktree_root=tmp_path / "worktrees")

    assert plan1 == plan2
    assert plan1["schema_version"] == "dhee.patch_family_plan.v1"
    assert plan1["policy"]["patch_application"] == "metadata_only_never_applied"

    serialized = json.dumps(plan1, sort_keys=True)
    assert raw_diff not in serialized
    assert "must be stripped" not in serialized

    family = plan1["families"][0]
    candidate_meta = family["candidate"]
    assert candidate_meta["patch_sha256"] == hashlib.sha256(raw_diff.encode("utf-8")).hexdigest()
    assert candidate_meta["patch_bytes"] == len(raw_diff.encode("utf-8"))
    assert candidate_meta["touched_paths"] == ["dhee/context_firewall.py"]
    assert candidate_meta["metadata"] == {"source_agent": "worker-5"}

    create_command = family["worktree"]["create_command"]
    assert create_command[:5] == ["git", "-C", str(repo), "worktree", "add"]
    assert "-b" in create_command
    assert family["worktree"]["isolation"]["patch_application"] == "not_performed_by_scaffold"
    assert [item["name"] for item in family["cleanup"]["commands"]] == [
        "remove_worktree",
        "delete_branch",
        "prune_worktrees",
    ]


def test_worktree_executor_dry_run_does_not_create_or_apply_patch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    plan = build_patch_family_plan(
        repo,
        [{"family": "minimal_fix", "title": "Plan only", "diff": _candidate_diff(), "risk": "low"}],
        ["pytest tests/test_context_firewall.py -q"],
        worktree_root=tmp_path / "worktrees",
    )
    family = plan["families"][0]
    worktree_path = Path(family["worktree"]["worktree_path"])

    create_result = execute_worktree_creation(family["worktree"], dry_run=True)
    cleanup_result = execute_cleanup_plan(family["cleanup"], dry_run=True)

    assert create_result["status"] == "planned"
    assert create_result["patch_application"] == "not_performed_by_scaffold"
    assert cleanup_result["status"] == "planned"
    assert not worktree_path.exists()
    assert "apply" not in json.dumps(create_result)


def test_verifiers_are_deduped_and_associated_with_each_candidate(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    candidates = [
        {"family": "minimal_fix", "title": "Small fix", "touched_paths": ["dhee/context_firewall.py"], "risk": "low"},
        {"family": "regression_safe_fix", "title": "Broader fix", "touched_paths": ["dhee/context_firewall.py", "tests/test_context_firewall.py"], "risk": "medium"},
    ]
    verifiers = [
        "pytest tests/test_context_firewall.py -q",
        "pytest tests/test_context_firewall.py -q",
        {"command": "python -m pytest tests/test_context_firewall.py", "kind": "pass_to_pass", "required": False},
    ]

    plan = build_patch_family_plan(repo, candidates, verifiers, worktree_root=tmp_path / "worktrees")

    assert len(plan["families"]) == 2
    candidate_ids = [family["candidate"]["candidate_id"] for family in plan["families"]]
    assert len(set(candidate_ids)) == 2

    for family in plan["families"]:
        assert len(family["verifiers"]) == 2
        assert family["ranking_inputs"]["required_verifier_count"] == 1
        assert family["ranking_inputs"]["optional_verifier_count"] == 1
        for verifier in family["verifiers"]:
            assert verifier["candidate_id"] == family["candidate"]["candidate_id"]
            assert verifier["cwd"] == family["worktree"]["worktree_path"]
            assert verifier["execution"] == "associated_not_executed"


def test_rank_patch_family_results_uses_verifier_outcomes_and_plan_inputs(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    candidates = [
        {"family": "minimal_fix", "title": "Small fix", "touched_paths": ["dhee/context_firewall.py"], "risk": "low"},
        {"family": "semantic_fix", "title": "Passing fix", "touched_paths": ["dhee/context_firewall.py"], "risk": "medium"},
        {"family": "regression_safe_fix", "title": "Large passing fix", "touched_paths": ["dhee/context_firewall.py", "tests/test_context_firewall.py"], "risk": "medium"},
    ]
    plan = build_patch_family_plan(
        repo,
        candidates,
        ["pytest tests/test_context_firewall.py -q"],
        worktree_root=tmp_path / "worktrees",
    )
    ids = [family["candidate"]["candidate_id"] for family in plan["families"]]

    ranking = rank_patch_family_results(
        [
            {
                "candidate_id": ids[0],
                "verifier_results": [{"command": "pytest tests/test_context_firewall.py -q", "required": True, "status": "failed"}],
            },
            {
                "candidate_id": ids[1],
                "verifier_results": [{"command": "pytest tests/test_context_firewall.py -q", "required": True, "status": "passed"}],
                "duration_ms": 500,
            },
            {
                "candidate_id": ids[2],
                "verifier_results": [{"command": "pytest tests/test_context_firewall.py -q", "required": True, "status": "passed"}],
                "duration_ms": 500,
            },
        ],
        plan=plan,
    )

    assert ranking["schema_version"] == "dhee.patch_family_ranking.v1"
    assert ranking["winner"]["candidate_id"] == ids[1]
    assert ranking["ranked"][-1]["candidate_id"] == ids[0]
    assert ranking["ranked"][0]["score"] > ranking["ranked"][1]["score"]
