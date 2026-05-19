import json
import subprocess
from pathlib import Path

import pytest

from dhee import repo_link
from dhee.update_capsules import (
    create_update_capsule,
    get_update_capsule,
    import_update_capsule,
    interpret_update_capsule,
    list_update_capsules,
)


def _run(args, cwd):
    subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _run(["git", "init"], path)
    _run(["git", "config", "user.email", "dhee-test@example.com"], path)
    _run(["git", "config", "user.name", "Dhee Test"], path)
    (path / "app.py").write_text("def feature():\n    return 'before'\n", encoding="utf-8")
    _run(["git", "add", "app.py"], path)
    _run(["git", "commit", "-m", "initial"], path)
    return path


def test_create_update_capsule_writes_md_json_indexes_repo_context_and_redacts(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    secret = "sk-" + ("a" * 32)
    (repo / "app.py").write_text(
        "def feature():\n"
        "    token = '" + secret + "'\n"
        "    return 'after'\n",
        encoding="utf-8",
    )
    (repo / "adapter.md").write_text("new adapter behavior\n", encoding="utf-8")

    result = create_update_capsule(
        repo=repo,
        since="HEAD",
        task_id="task-123",
        evidence=[
            {
                "kind": "temporal_scene",
                "ref": "scene_abc",
                "label": "Private browsing context from /Users/alice/notes.txt",
                "agent_id": "chotu",
                "source_app": "chotu",
                "confidentiality_scope": "personal",
            }
        ],
    )

    capsule = result["capsule"]
    paths = result["paths"]
    assert capsule["kind"] == "update_capsule"
    assert capsule["personal_context_used"] is True
    assert capsule["privacy"]["raw_personal_memory_included"] is False
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()

    data = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert data["context_ir"]["schema_version"] == "dhee.context_ir.v1"
    assert data["context_ir"]["symbol_table"]["files"]
    assert data["context_ir"]["operations"]
    assert data["base_file_hashes"]["app.py"]
    changed_paths = {item["path"] for item in data["changed_paths"]}
    assert {"app.py", "adapter.md"}.issubset(changed_paths)
    assert data["file_hashes"]["app.py"]
    serialized = json.dumps(data)
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert secret not in serialized
    assert secret not in markdown
    assert "/Users/alice" not in serialized
    assert "diff --git" in serialized

    entries = repo_link.list_entries(repo)
    capsule_entries = [entry for entry in entries if entry.kind == "update_capsule"]
    assert capsule_entries
    assert capsule_entries[-1].meta["capsule_id"] == capsule["id"]


def test_capsule_import_into_clean_repo_lists_and_gets_capsule(tmp_path):
    source_repo = _init_repo(tmp_path / "source")
    (source_repo / "app.py").write_text("def feature():\n    return 'after'\n", encoding="utf-8")
    created = create_update_capsule(repo=source_repo, since="HEAD", task_id="task-import")
    source_dir = Path(created["paths"]["dir"])

    target_repo = _init_repo(tmp_path / "target")
    imported = import_update_capsule(source_dir, repo=target_repo)

    assert imported["capsule"]["id"] == created["capsule"]["id"]
    listed = list_update_capsules(repo=target_repo)
    assert [item["id"] for item in listed] == [created["capsule"]["id"]]
    fetched = get_update_capsule(created["capsule"]["id"], repo=target_repo)
    assert fetched["capsule"]["id"] == created["capsule"]["id"]
    assert "Reproduction Guide" in fetched["markdown"]
    assert "Context IR" in fetched["markdown"]


def test_capsule_import_rejects_raw_private_memory_marker(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    capsule_dir = tmp_path / "bad_capsule"
    capsule_dir.mkdir()
    (capsule_dir / "capsule.json").write_text(
        json.dumps(
            {
                "id": "ucap_private",
                "title": "bad",
                "privacy": {"raw_personal_memory_included": True},
            }
        ),
        encoding="utf-8",
    )
    (capsule_dir / "capsule.md").write_text("private body", encoding="utf-8")

    with pytest.raises(ValueError):
        import_update_capsule(capsule_dir, repo=repo)


def test_update_capsule_interpreter_reports_ready_applied_and_conflict(tmp_path):
    source_repo = _init_repo(tmp_path / "source")
    after = "def feature():\n    return 'after'\n"
    (source_repo / "app.py").write_text(after, encoding="utf-8")
    created = create_update_capsule(repo=source_repo, since="HEAD", task_id="interp")
    source_dir = Path(created["paths"]["dir"])

    target_repo = _init_repo(tmp_path / "target")
    ready = interpret_update_capsule(source_dir, repo=target_repo)
    assert ready["format"] == "dhee.context_interpretation.v1"
    assert ready["readiness"] == "ready"
    assert ready["execution_plan"][0]["action"] == "modify_file"
    assert ready["policy"]["auto_apply"] is False

    (target_repo / "app.py").write_text(after, encoding="utf-8")
    applied = interpret_update_capsule(source_dir, repo=target_repo)
    assert applied["readiness"] == "already_applied"

    (target_repo / "app.py").write_text("def feature():\n    return 'other'\n", encoding="utf-8")
    conflict = interpret_update_capsule(source_dir, repo=target_repo)
    assert conflict["readiness"] == "conflict"
    assert any(diag["code"] == "PRECONDITION_MISMATCH" for diag in conflict["diagnostics"])


def test_update_capsule_interpreter_resolves_moved_target_by_hash(tmp_path):
    source_repo = _init_repo(tmp_path / "source")
    (source_repo / "app.py").write_text("def feature():\n    return 'after'\n", encoding="utf-8")
    created = create_update_capsule(repo=source_repo, since="HEAD", task_id="moved-target")
    source_dir = Path(created["paths"]["dir"])

    target_repo = _init_repo(tmp_path / "target")
    (target_repo / "src").mkdir()
    (target_repo / "src" / "app.py").write_text(
        "def feature():\n    return 'before'\n",
        encoding="utf-8",
    )
    (target_repo / "app.py").unlink()

    interpreted = interpret_update_capsule(source_dir, repo=target_repo)

    assert interpreted["readiness"] == "ready"
    state = interpreted["operation_states"][0]
    assert state["path"] == "app.py"
    assert state["resolved_path"] == "src/app.py"
    assert state["resolution"] == "moved_before_hash_match"
    assert interpreted["execution_plan"][0]["resolved_path"] == "src/app.py"


def test_capsule_import_rejects_invalid_context_ir(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    capsule_dir = tmp_path / "bad_ir_capsule"
    capsule_dir.mkdir()
    (capsule_dir / "capsule.json").write_text(
        json.dumps(
            {
                "id": "ucap_bad_ir",
                "title": "bad ir",
                "privacy": {"raw_personal_memory_included": False},
                "context_ir": {
                    "schema_version": "dhee.context_ir.v1",
                    "symbol_table": {"files": []},
                    "operations": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (capsule_dir / "capsule.md").write_text("bad ir body", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid context_ir"):
        import_update_capsule(capsule_dir, repo=repo)


def test_mcp_slim_capsule_handlers_create_list_get(tmp_path):
    from dhee import mcp_slim

    repo = _init_repo(tmp_path / "repo")
    (repo / "app.py").write_text("def feature():\n    return 'after via mcp'\n", encoding="utf-8")

    created = mcp_slim.HANDLERS["dhee_update_capsule_create"](
        {"repo": str(repo), "since": "HEAD", "task_id": "mcp-task"}
    )
    capsule_id = created["capsule"]["id"]

    listed = mcp_slim.HANDLERS["dhee_update_capsule_list"]({"repo": str(repo)})
    assert [item["id"] for item in listed["results"]] == [capsule_id]

    fetched = mcp_slim.HANDLERS["dhee_update_capsule_get"](
        {"repo": str(repo), "capsule_id": capsule_id}
    )
    assert fetched["capsule"]["id"] == capsule_id

    interpreted = mcp_slim.HANDLERS["dhee_update_capsule_interpret"](
        {"repo": str(repo), "capsule_id": capsule_id}
    )
    assert interpreted["format"] == "dhee.context_interpretation.v1"
    assert interpreted["readiness"] in {"ready", "already_applied", "conflict"}


def test_cli_context_capsule_parser_accepts_nested_subcommands():
    from dhee.cli import build_parser

    args = build_parser().parse_args(
        ["context", "capsule", "interpret", "ucap_123", "--repo", ".", "--strict", "--json"]
    )
    assert args.context_action == "capsule"
    assert args.entry_id == "interpret"
    assert args.context_args == ["ucap_123"]
    assert args.repo == "."
    assert args.strict is True
