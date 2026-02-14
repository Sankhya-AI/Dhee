"""Tests for engram.core.log_parser — JSONL conversation log parser."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from engram.core.log_parser import (
    _escape_path,
    _extract_text,
    _extract_tool_artifacts,
    find_latest_log,
    parse_conversation_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: str, entries: list) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")


SAMPLE_CONVERSATION = [
    {
        "type": "user",
        "message": {"role": "user", "content": "Implement the login feature for the auth module."},
        "timestamp": "2026-02-09T11:36:23.690Z",
        "cwd": "/Users/dev/project",
        "sessionId": "abc-123",
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll start by reading the auth module."},
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/Users/dev/project/auth.py"},
                },
            ],
        },
        "timestamp": "2026-02-09T11:36:30.000Z",
        "sessionId": "abc-123",
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Now let me edit the file."},
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "/Users/dev/project/auth.py", "old_string": "pass", "new_string": "return True"},
                },
            ],
        },
        "timestamp": "2026-02-09T11:37:00.000Z",
        "sessionId": "abc-123",
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Running the tests now."},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "pytest tests/test_auth.py -v"},
                },
            ],
        },
        "timestamp": "2026-02-09T11:37:30.000Z",
        "sessionId": "abc-123",
    },
    {
        "type": "user",
        "message": {"role": "user", "content": "Looks good, now add error handling."},
        "timestamp": "2026-02-09T11:38:00.000Z",
        "sessionId": "abc-123",
    },
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Adding try/except blocks for robust error handling."},
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "/Users/dev/project/errors.py"},
                },
            ],
        },
        "timestamp": "2026-02-09T11:38:30.000Z",
        "sessionId": "abc-123",
    },
]


# ---------------------------------------------------------------------------
# _escape_path
# ---------------------------------------------------------------------------

class TestEscapePath:

    def test_unix_path(self):
        assert _escape_path("/Users/foo/bar") == "-Users-foo-bar"

    def test_root(self):
        assert _escape_path("/") == "-"

    def test_no_leading_slash(self):
        assert _escape_path("relative/path") == "relative-path"


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------

class TestExtractText:

    def test_plain_string(self):
        assert _extract_text("hello world") == "hello world"

    def test_empty_string(self):
        assert _extract_text("") is None

    def test_whitespace_only(self):
        assert _extract_text("   ") is None

    def test_list_with_text_block(self):
        content = [{"type": "text", "text": "some text"}]
        assert _extract_text(content) == "some text"

    def test_list_with_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "first"},
            {"type": "tool_use", "name": "Read", "input": {}},
            {"type": "text", "text": "second"},
        ]
        assert _extract_text(content) == "first\nsecond"

    def test_list_with_only_tool_use(self):
        content = [{"type": "tool_use", "name": "Read", "input": {}}]
        assert _extract_text(content) is None

    def test_non_string_non_list(self):
        assert _extract_text(42) is None
        assert _extract_text(None) is None


# ---------------------------------------------------------------------------
# _extract_tool_artifacts
# ---------------------------------------------------------------------------

class TestExtractToolArtifacts:

    def test_read_tool(self):
        content = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b.py"}},
        ]
        files, cmds = [], []
        _extract_tool_artifacts(content, files, cmds)
        assert files == ["/a/b.py"]
        assert cmds == []

    def test_bash_tool(self):
        content = [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        ]
        files, cmds = [], []
        _extract_tool_artifacts(content, files, cmds)
        assert files == []
        assert cmds == ["ls -la"]

    def test_edit_and_write(self):
        content = [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x.py"}},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/y.py"}},
        ]
        files, cmds = [], []
        _extract_tool_artifacts(content, files, cmds)
        assert files == ["/x.py", "/y.py"]

    def test_glob_with_path(self):
        content = [
            {"type": "tool_use", "name": "Glob", "input": {"path": "/src"}},
        ]
        files, cmds = [], []
        _extract_tool_artifacts(content, files, cmds)
        assert files == ["/src"]

    def test_non_list_content(self):
        files, cmds = [], []
        _extract_tool_artifacts("plain string", files, cmds)
        assert files == []
        assert cmds == []

    def test_missing_input(self):
        content = [{"type": "tool_use", "name": "Read"}]
        files, cmds = [], []
        _extract_tool_artifacts(content, files, cmds)
        assert files == []


# ---------------------------------------------------------------------------
# parse_conversation_log
# ---------------------------------------------------------------------------

class TestParseConversationLog:

    def test_full_parse(self, tmp_path):
        logfile = str(tmp_path / "session.jsonl")
        _write_jsonl(logfile, SAMPLE_CONVERSATION)

        result = parse_conversation_log(logfile)

        assert result["source"] == "conversation_log_fallback"
        assert result["task_summary"].startswith("Implement the login feature")
        assert result["last_user_message"] == "Looks good, now add error handling."
        assert "error handling" in result["last_assistant_summary"]
        assert result["message_count"] == 6
        assert result["started_at"] == "2026-02-09T11:36:23.690Z"
        assert result["ended_at"] == "2026-02-09T11:38:30.000Z"

        # files: auth.py (from Read + Edit, deduped) and errors.py (from Write)
        assert "/Users/dev/project/auth.py" in result["files_touched"]
        assert "/Users/dev/project/errors.py" in result["files_touched"]
        # auth.py appears twice (Read + Edit) but should be deduped
        assert result["files_touched"].count("/Users/dev/project/auth.py") == 1

        assert "pytest tests/test_auth.py -v" in result["key_commands"]

    def test_empty_file(self, tmp_path):
        logfile = str(tmp_path / "empty.jsonl")
        Path(logfile).write_text("", encoding="utf-8")

        result = parse_conversation_log(logfile)

        assert result["message_count"] == 0
        assert result["task_summary"] == ""
        assert result["files_touched"] == []
        assert result["source"] == "conversation_log_fallback"

    def test_malformed_json_lines(self, tmp_path):
        logfile = str(tmp_path / "bad.jsonl")
        with open(logfile, "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps(SAMPLE_CONVERSATION[0]) + "\n")
            fh.write("{broken\n")

        result = parse_conversation_log(logfile)

        # should still parse the one valid entry
        assert result["message_count"] == 1
        assert result["task_summary"].startswith("Implement the login")

    def test_nonexistent_file(self):
        result = parse_conversation_log("/nonexistent/path.jsonl")
        assert result["message_count"] == 0
        assert result["source"] == "conversation_log_fallback"

    def test_task_summary_truncated_at_300(self, tmp_path):
        long_msg = "x" * 500
        entries = [
            {
                "type": "user",
                "message": {"role": "user", "content": long_msg},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ]
        logfile = str(tmp_path / "long.jsonl")
        _write_jsonl(logfile, entries)

        result = parse_conversation_log(logfile)
        assert len(result["task_summary"]) == 300

    def test_assistant_summary_truncated_at_500(self, tmp_path):
        long_text = "y" * 800
        entries = [
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": long_text},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ]
        logfile = str(tmp_path / "longassist.jsonl")
        _write_jsonl(logfile, entries)

        result = parse_conversation_log(logfile)
        assert len(result["last_assistant_summary"]) == 500


# ---------------------------------------------------------------------------
# find_latest_log
# ---------------------------------------------------------------------------

class TestFindLatestLog:

    def test_finds_newest_jsonl(self, tmp_path, monkeypatch):
        # Simulate ~/.claude/projects/<escaped>/
        escaped = "-Users-dev-project"
        proj_dir = tmp_path / ".claude" / "projects" / escaped
        proj_dir.mkdir(parents=True)

        old = proj_dir / "old-session.jsonl"
        old.write_text("{}\n", encoding="utf-8")
        # Force older mtime
        os.utime(str(old), (1000000, 1000000))

        new = proj_dir / "new-session.jsonl"
        new.write_text("{}\n", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        result = find_latest_log("/Users/dev/project")
        assert result is not None
        assert "new-session.jsonl" in result

    def test_no_matching_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_latest_log("/nonexistent/path") is None

    def test_empty_dir(self, tmp_path, monkeypatch):
        escaped = "-Users-dev-empty"
        proj_dir = tmp_path / ".claude" / "projects" / escaped
        proj_dir.mkdir(parents=True)

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_latest_log("/Users/dev/empty") is None

    def test_ignores_non_jsonl(self, tmp_path, monkeypatch):
        escaped = "-Users-dev-proj"
        proj_dir = tmp_path / ".claude" / "projects" / escaped
        proj_dir.mkdir(parents=True)
        (proj_dir / "notes.txt").write_text("hi", encoding="utf-8")
        (proj_dir / "data.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert find_latest_log("/Users/dev/proj") is None
