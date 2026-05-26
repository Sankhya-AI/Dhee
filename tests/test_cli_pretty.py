from __future__ import annotations

import io


def test_render_init_is_calm_ascii_and_aligned():
    from dhee.cli_pretty import render_init

    out = io.StringIO()
    render_init(
        {
            "repo_root": "/tmp/project",
            "kind": "folder",
            "repo_id": "repo-1",
            "hooks": [],
            "claude_md": {"path": "/tmp/project/CLAUDE.md", "created": True},
            "agents_md": {"path": "/tmp/project/AGENTS.md", "updated": True},
            "ingest": {"status": "skipped", "reason": "skip_ingest"},
            "first_light": {"status": "skipped", "reason": "skip_first_light", "hits": []},
            "linked_repos": 2,
        },
        file=out,
        color=False,
    )

    text = out.getvalue()
    assert text.startswith("Dhee init\n")
    assert "workspace    /tmp/project" in text
    assert "git hooks    skip" in text
    assert "CLAUDE.md    created" in text
    assert "AGENTS.md    updated" in text
    assert "Next\n" in text
    assert "\x1b[" not in text


def test_completion_scripts_include_init():
    from dhee.cli import _bash_completion

    text = _bash_completion(["init", "status"])

    assert "complete -F _dhee_complete dhee" in text
    assert "init status" in text


def test_unknown_command_suggestion(capsys):
    from dhee.cli import _maybe_print_command_suggestion, build_parser

    assert _maybe_print_command_suggestion(build_parser(), ["statsu"]) is True

    err = capsys.readouterr().err
    assert "Unknown command: statsu" in err
    assert "dhee status" in err
