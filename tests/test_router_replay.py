from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_replay_claude_session_applies_embedded_golden_annotation(tmp_path):
    from dhee.benchmarks.router_replay import replay_session

    transcript = tmp_path / "claude-session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "message": {
                    "usage": {"cache_read_input_tokens": 9000, "output_tokens": 50},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {"command": "pytest -q"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "FAILED tests/test_parser.py::test_parse\n" * 80,
                        }
                    ]
                },
            },
            {
                "type": "dhee_golden_replay",
                "payload": {
                    "task_parity": "pass",
                    "task_parity_score": 0.97,
                    "stale_context_incidents": [{"source": "old plan"}],
                    "note": "Router preserved the failure target.",
                },
            },
        ],
    )

    report = replay_session(transcript, harness="claude_code")

    assert report.harness == "claude_code"
    assert report.total_calls == 1
    assert report.calls_by_tool["Bash"] == 1
    assert report.annotations_count == 1
    assert report.task_parity is True
    assert report.task_parity_score == 0.97
    assert report.stale_context_incidents == 1


def test_replay_codex_exec_command_and_external_golden_annotation(tmp_path):
    from dhee.benchmarks.router_replay import load_golden_annotations, replay_session

    transcript = tmp_path / "codex-session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"cmd": "python -m pytest -q", "cwd": str(tmp_path)}),
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "python -m pytest -q"],
                    "exit_code": 0,
                    "aggregated_output": "120 passed\n" * 60,
                },
            },
        ],
    )
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "codex-session",
                "task_parity": "fail",
                "stale_context_incidents": 2,
                "notes": ["Missed stale fixture detail."],
            }
        ],
    )

    report = replay_session(
        transcript,
        harness="codex",
        annotations=load_golden_annotations(golden),
    )

    assert report.harness == "codex"
    assert report.total_calls == 1
    assert report.calls_by_tool["Bash"] == 1
    assert report.task_parity is False
    assert report.stale_context_incidents == 2
    assert report.annotations_count == 1


def test_replay_aggregate_includes_golden_metrics(tmp_path):
    from dhee.benchmarks.router_replay import aggregate_reports, replay_session

    transcript = tmp_path / "codex.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "echo hello"],
                    "exit_code": 0,
                    "aggregated_output": "hello\n" * 100,
                },
            },
            {
                "type": "golden_annotation",
                "payload": {
                    "task_parity": True,
                    "task_parity_score": 1.0,
                    "stale_context_incidents": 0,
                },
            },
        ],
    )

    aggregate = aggregate_reports([replay_session(transcript, harness="codex")])

    assert aggregate["sessions_by_harness"] == {"codex": 1}
    assert aggregate["annotated_sessions"] == 1
    assert aggregate["task_parity"]["pass"] == 1
    assert aggregate["task_parity"]["avg_score"] == 1.0
    assert aggregate["task_parity"]["score_count"] == 1
    assert aggregate["stale_context_incidents"] == 0


def test_quality_report_replays_codex_golden_annotations(tmp_path, monkeypatch):
    from dhee.router import quality_report

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    transcript = sessions / "codex-quality.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "python -m pytest -q"],
                    "exit_code": 0,
                    "aggregated_output": "5 passed\n" * 80,
                },
            }
        ],
    )
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "codex-quality",
                "task_parity": "pass",
                "task_parity_score": 1.0,
                "stale_context_incidents": 0,
            }
        ],
    )

    report = quality_report.build_report(
        sessions_dir=sessions,
        harness="codex",
        golden_path=golden,
    )

    assert report.replay["harness"] == "codex"
    assert report.replay["sessions_by_harness"] == {"codex": 1}
    assert report.replay["annotated_sessions"] == 1
    assert report.replay["task_parity"]["pass"] == 1
    assert "stale_context_incidents" in report.quality_gates["gates"]
    assert "task_parity_failures" in report.quality_gates["gates"]
    assert "task_parity_score" in report.quality_gates["gates"]


def test_quality_gate_fails_pending_review_annotations(tmp_path, monkeypatch):
    from dhee.router import quality_report

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    transcript = sessions / "codex-pending.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "python -m pytest -q"],
                    "exit_code": 0,
                    "aggregated_output": "5 passed\n" * 80,
                },
            }
        ],
    )
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "codex-pending",
                "task_parity": "needs_review",
                "stale_context_incidents": 0,
            }
        ],
    )

    report = quality_report.build_report(
        sessions_dir=sessions,
        harness="codex",
        golden_path=golden,
    )
    gate = quality_report.gate_summary(report, allow_insufficient=True)

    assert report.replay["pending_review_sessions"] == 1
    assert report.quality_gates["gates"]["task_parity_pending_review"]["passed"] is False
    assert gate["ok"] is False
    assert "task_parity_pending_review" in gate["failed_gates"]


def test_fixture_golden_replay_corpus_covers_claude_and_codex(monkeypatch, tmp_path):
    from dhee.router import quality_report

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    root = Path(__file__).parent / "fixtures" / "golden_replay"
    report = quality_report.build_report(
        sessions_dir=root / "sessions",
        harness="all",
        golden_path=root / "golden.jsonl",
    )

    assert report.replay["sessions_by_harness"] == {"codex": 1, "claude_code": 1}
    assert report.replay["annotated_sessions"] == 2
    assert report.replay["task_parity"]["pass"] == 2
    assert report.replay["stale_context_incidents"] == 0
    assert report.quality_gates["gates"]["router_token_savings"]["passed"] is True
    assert quality_report.gate_summary(report, allow_insufficient=True)["ok"] is True


def test_fixture_redacted_real_replay_corpus_is_representative():
    from dhee.benchmarks.replay_corpus import inspect_corpus

    root = Path(__file__).parent / "fixtures" / "golden_replay" / "redacted_real"
    result = inspect_corpus(
        sessions_dir=root / "sessions",
        harness="all",
        golden_path=root / "golden_needs_review.jsonl",
    )
    aggregate = result["aggregate"]

    assert aggregate["sessions"] >= 4
    assert aggregate["sessions_by_harness"].get("claude_code", 0) >= 1
    assert aggregate["sessions_by_harness"].get("codex", 0) >= 1
    assert aggregate["total_calls"] >= 100
    assert aggregate["saved_pct"] >= 50
    assert aggregate["annotated_sessions"] == aggregate["sessions"]
    assert aggregate["pending_review_sessions"] == aggregate["sessions"]
    assert aggregate["task_parity"]["unknown"] == aggregate["sessions"]


def test_replay_corpus_harvest_redacts_real_session_shapes(tmp_path):
    from dhee.benchmarks.replay_corpus import harvest_corpus, inspect_corpus
    from dhee.benchmarks.router_replay import replay_session

    source = tmp_path / "source"
    source.mkdir()
    secret = "api_key=abcdefghijklmnopqrstuvwxyz123456"
    private_path = tmp_path / "private_repo" / "tests" / "test_private.py"
    raw_failure = (
        f"FAILED {private_path}::test_secret AssertionError: super secret {secret}\n"
        * 160
    )
    raw_pass = (
        f"{private_path}::test_secret PASSED with token={secret}\n"
        * 120
    )
    _write_jsonl(
        source / "claude-real.jsonl",
        [
            {
                "type": "assistant",
                "message": {
                    "usage": {"cache_read_input_tokens": 15000, "output_tokens": 200},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-secret",
                            "name": "Bash",
                            "input": {
                                "command": f"python -m pytest {private_path} -q --token {secret}",
                                "cwd": str(tmp_path / "private_repo"),
                            },
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-secret",
                            "content": raw_failure,
                        }
                    ]
                },
            },
        ],
    )
    _write_jsonl(
        source / "codex-real.jsonl",
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-secret",
                    "arguments": json.dumps(
                        {
                            "cmd": f"python -m pytest {private_path} -q --token {secret}",
                            "cwd": str(tmp_path / "private_repo"),
                        }
                    ),
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-secret",
                    "command": [
                        "/bin/zsh",
                        "-lc",
                        f"python -m pytest {private_path} -q --token {secret}",
                    ],
                    "exit_code": 0,
                    "aggregated_output": raw_pass,
                },
            },
        ],
    )

    out = tmp_path / "redacted"
    result = harvest_corpus(
        sessions_dir=source,
        output_dir=out,
        harness="all",
        min_calls=1,
        max_output_chars=20_000,
    )

    assert result["harvested_sessions"] == 2
    assert result["aggregate"]["sessions_by_harness"] == {"codex": 1, "claude_code": 1}
    corpus_text = "\n".join(path.read_text(encoding="utf-8") for path in (out / "sessions").glob("*.jsonl"))
    assert "abcdefghijklmnopqrstuvwxyz123456" not in corpus_text
    assert "super secret" not in corpus_text
    assert str(tmp_path) not in corpus_text
    assert "test_private.py" not in corpus_text
    assert "<redacted" in corpus_text or "tests/test_redacted.py" in corpus_text

    for session_path in (out / "sessions").glob("*.jsonl"):
        replay = replay_session(session_path, harness="auto")
        assert replay.total_calls >= 1

    inspected = inspect_corpus(
        sessions_dir=out / "sessions",
        harness="all",
        golden_path=out / "golden_needs_review.jsonl",
    )
    assert inspected["aggregate"]["annotated_sessions"] == 2
    assert inspected["aggregate"]["pending_review_sessions"] == 2
    assert inspected["aggregate"]["task_parity"]["unknown"] == 2
    assert inspected["aggregate"]["stale_context_incidents"] == 0


def test_replay_corpus_annotation_update_graduates_pending_review(tmp_path):
    from dhee.benchmarks.replay_corpus import upsert_golden_annotation
    from dhee.benchmarks.router_replay import aggregate_reports, load_golden_annotations, replay_session

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    transcript = sessions / "codex-reviewed.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "python -m pytest -q"],
                    "exit_code": 0,
                    "aggregated_output": "10 passed\n" * 100,
                },
            }
        ],
    )
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "codex-reviewed",
                "task_parity": "needs_review",
                "stale_context_incidents": 0,
            }
        ],
    )

    pending = aggregate_reports(
        [
            replay_session(
                transcript,
                harness="codex",
                annotations=load_golden_annotations(golden),
            )
        ]
    )
    assert pending["pending_review_sessions"] == 1

    result = upsert_golden_annotation(
        golden_path=golden,
        session_id="codex-reviewed",
        task_parity="pass",
        task_parity_score=0.99,
        stale_context_incidents=0,
        note="Reviewed against original task outcome.",
    )
    assert result["action"] == "updated"

    reviewed = aggregate_reports(
        [
            replay_session(
                transcript,
                harness="codex",
                annotations=load_golden_annotations(golden),
            )
        ]
    )
    assert reviewed["pending_review_sessions"] == 0
    assert reviewed["task_parity"]["pass"] == 1
    assert reviewed["task_parity"]["avg_score"] == 0.99


def test_cli_router_harvest_writes_redacted_corpus(tmp_path, monkeypatch, capsys):
    from dhee import cli

    source = tmp_path / "source"
    source.mkdir()
    secret = "token=abcdefghijklmnopqrstuvwxyz1234567890"
    _write_jsonl(
        source / "codex-real.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", f"python -m pytest {tmp_path}/private.py -q {secret}"],
                    "exit_code": 1,
                    "aggregated_output": f"FAILED {tmp_path}/private.py::test_case {secret}\n" * 120,
                },
            }
        ],
    )

    out = tmp_path / "corpus"
    monkeypatch.setattr(
        "sys.argv",
        [
            "dhee",
            "router",
            "harvest",
            "--sessions-dir",
            str(source),
            "--harness",
            "all",
            "--output-dir",
            str(out),
            "--json",
        ],
    )
    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert data["harvested_sessions"] == 1
    assert data["aggregate"]["total_calls"] == 1
    assert Path(data["manifest_path"]).exists()
    assert Path(data["golden_path"]).exists()
    corpus_text = "\n".join(path.read_text(encoding="utf-8") for path in (out / "sessions").glob("*.jsonl"))
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in corpus_text
    assert str(tmp_path) not in corpus_text


def test_cli_router_annotate_updates_golden_jsonl(tmp_path, monkeypatch, capsys):
    from dhee import cli

    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "session-a",
                "task_parity": "needs_review",
                "stale_context_incidents": 0,
            }
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "dhee",
            "router",
            "annotate",
            "--golden",
            str(golden),
            "--session-id",
            "session-a",
            "--task-parity",
            "pass",
            "--task-parity-score",
            "0.98",
            "--stale-context-incidents",
            "0",
            "--note",
            "Reviewed manually.",
            "--json",
        ],
    )
    cli.main()
    data = json.loads(capsys.readouterr().out)
    rows = [json.loads(line) for line in golden.read_text(encoding="utf-8").splitlines()]

    assert data["action"] == "updated"
    assert rows == [
        {
            "session_id": "session-a",
            "task_parity": "pass",
            "review_status": "reviewed",
            "task_parity_score": 0.98,
            "stale_context_incidents": 0,
            "note": "Reviewed manually.",
        }
    ]


def test_quality_report_gate_summary_allows_pending_but_not_failed(tmp_path, monkeypatch):
    from dhee.router import quality_report

    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))
    report = quality_report.build_report()

    report.quality_gates = {
        "verdict": "insufficient_data",
        "gates": {
            "pending": {"passed": None},
            "passed": {"passed": True},
        },
    }
    assert quality_report.gate_summary(report)["ok"] is False
    assert quality_report.gate_summary(report, allow_insufficient=True)["ok"] is True

    report.quality_gates["gates"]["failed"] = {"passed": False}
    assert quality_report.gate_summary(report, allow_insufficient=True)["ok"] is False


def test_cli_router_gate_passes_with_golden_and_allow_insufficient(tmp_path, monkeypatch, capsys):
    from dhee import cli

    home = tmp_path / "home"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))

    transcript = sessions / "codex-gate.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "python -m pytest -q"],
                    "exit_code": 0,
                    "aggregated_output": "FAILED tests/test_parser.py::test_parse\n" * 500,
                },
            }
        ],
    )
    golden = tmp_path / "golden.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "codex-gate",
                "task_parity": "pass",
                "task_parity_score": 0.99,
                "stale_context_incidents": 0,
            }
        ],
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "dhee",
            "router",
            "gate",
            "--harness",
            "codex",
            "--sessions-dir",
            str(sessions),
            "--golden",
            str(golden),
            "--allow-insufficient",
            "--json",
        ],
    )
    cli.main()
    data = json.loads(capsys.readouterr().out)

    assert data["gate"]["ok"] is True
    assert data["quality_gates"]["gates"]["task_parity_failures"]["passed"] is True
    assert data["quality_gates"]["gates"]["stale_context_incidents"]["passed"] is True


def test_cli_router_gate_exits_nonzero_on_failed_parity(tmp_path, monkeypatch, capsys):
    from dhee import cli

    home = tmp_path / "home"
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("DHEE_DATA_DIR", str(tmp_path / "dhee-data"))

    transcript = sessions / "codex-gate-fail.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "exec_command_end",
                    "call_id": "call-1",
                    "command": ["/bin/zsh", "-lc", "python -m pytest -q"],
                    "exit_code": 0,
                    "aggregated_output": "FAILED tests/test_parser.py::test_parse\n" * 500,
                },
            }
        ],
    )
    golden = tmp_path / "golden-fail.jsonl"
    _write_jsonl(
        golden,
        [
            {
                "session_id": "codex-gate-fail",
                "task_parity": "fail",
                "task_parity_score": 0.2,
                "stale_context_incidents": 1,
            }
        ],
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "dhee",
            "router",
            "gate",
            "--harness",
            "codex",
            "--sessions-dir",
            str(sessions),
            "--golden",
            str(golden),
            "--allow-insufficient",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    data = json.loads(capsys.readouterr().out)

    assert exc.value.code == 1
    assert data["gate"]["ok"] is False
    assert "task_parity_failures" in data["gate"]["failed_gates"]
