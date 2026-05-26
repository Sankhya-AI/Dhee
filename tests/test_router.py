"""Router test suite — Phase 4 (intent), Phase 6 (enforcement),
Phase 7 (edit ledger), Phase 8 (policy/tune), handlers round-trip.

Each test isolates state via a temp DHEE_ROUTER_PTR_DIR + policy path
so the user's real ~/.dhee is never touched. Handlers are exercised
directly — no MCP framework dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def router_tmp(tmp_path, monkeypatch):
    """Isolate every router-side file to a fresh tmp dir."""
    ptr_dir = tmp_path / "ptr"
    policy_path = tmp_path / "policy.json"
    flag_file = tmp_path / "enforce"
    dhee_dir = tmp_path / "dhee"

    monkeypatch.setenv("DHEE_ROUTER_PTR_DIR", str(ptr_dir))
    monkeypatch.setenv("DHEE_ROUTER_POLICY", str(policy_path))
    monkeypatch.setenv("DHEE_ROUTER_ENFORCE_FILE", str(flag_file))
    monkeypatch.setenv("DHEE_ROUTER_SESSION_ID", "pytest")
    monkeypatch.setenv("DHEE_DATA_DIR", str(dhee_dir))
    # Clear the enforce env toggle so tests use the flag file only.
    monkeypatch.delenv("DHEE_ROUTER_ENFORCE", raising=False)
    yield tmp_path


# ---------------------------------------------------------------------------
# Phase 4 — intent classifier
# ---------------------------------------------------------------------------


class TestIntentClassifier:
    @pytest.mark.parametrize("path,expected", [
        ("/a/src/foo.py", "source_code"),
        ("/a/app/main.ts", "source_code"),
        ("/a/tests/test_foo.py", "test"),
        ("/a/__tests__/thing.spec.js", "test"),
        ("/a/path/test_whatever.py", "test"),
        ("/a/README.md", "doc"),
        ("/a/docs/guide.rst", "doc"),
        ("/a/config.yaml", "config"),
        ("/a/.env", "config"),
        ("/a/data.csv", "data"),
        ("/a/records.jsonl", "data"),
        ("Makefile", "build"),
        ("pyproject.toml", "build"),
        ("/a/x.unknown", "other"),
        ("", "other"),
    ])
    def test_classify_read(self, path, expected):
        from dhee.router.intent import classify_read
        assert classify_read(path) == expected


# ---------------------------------------------------------------------------
# Phase 8 — policy
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_default_is_normal(self, router_tmp):
        from dhee.router import policy
        assert policy.get_depth("Read", "source_code") == "normal"

    def test_set_and_get(self, router_tmp):
        from dhee.router import policy
        policy.set_depth("Read", "source_code", "deep")
        assert policy.get_depth("Read", "source_code") == "deep"
        assert policy.get_depth("Read", "test") == "normal"  # unset → default

    def test_invalid_depth_rejected(self, router_tmp):
        from dhee.router import policy
        with pytest.raises(ValueError):
            policy.set_depth("Read", "source_code", "extra-deep")

    def test_clear_all(self, router_tmp):
        from dhee.router import policy
        policy.set_depth("Read", "source_code", "deep")
        policy.set_depth("Bash", "git_log", "shallow")
        removed = policy.clear()
        assert removed == 2
        assert policy.get_depth("Read", "source_code") == "normal"

    def test_atomic_write_produces_valid_json(self, router_tmp):
        from dhee.router import policy
        policy.set_depth("Read", "source_code", "deep")
        data = json.loads(policy._policy_path().read_text())
        assert data["depths"]["Read"]["source_code"] == "deep"
        assert data["version"] == 1


# ---------------------------------------------------------------------------
# Phase 7 — edit ledger
# ---------------------------------------------------------------------------


class TestEditLedger:
    def test_dedup_by_hash(self, router_tmp):
        from dhee.router import edit_ledger as L
        L.record("Edit", "/a/x.py", "print(1)")
        L.record("Edit", "/a/x.py", "print(1)")  # identical → dup collapse
        L.record("Edit", "/a/x.py", "print(2)")  # different hash
        entries = L.summarise()
        assert len(entries) == 1  # one file
        assert entries[0].path == "/a/x.py"
        assert entries[0].occurrences == 3  # sum across hashes

    def test_multi_file(self, router_tmp):
        from dhee.router import edit_ledger as L
        L.record("Edit", "/a/x.py", "x")
        L.record("Write", "/a/y.py", "y")
        entries = L.summarise()
        assert {e.path for e in entries} == {"/a/x.py", "/a/y.py"}

    def test_render_block_skips_when_empty(self, router_tmp):
        from dhee.router import edit_ledger as L
        assert L.render_block() == ""

    def test_render_block_marks_dupes(self, router_tmp):
        from dhee.router import edit_ledger as L
        L.record("Edit", "/a.py", "v1")
        L.record("Edit", "/a.py", "v2")
        block = L.render_block()
        assert "<edits" in block
        assert "/a.py x2" in block
        assert "</edits>" in block

    def test_ignores_non_write_tool(self, router_tmp):
        from dhee.router import edit_ledger as L
        L.record("Read", "/a.py", "content")
        assert L.summarise() == []


# ---------------------------------------------------------------------------
# Phase 8 — tune
# ---------------------------------------------------------------------------


def _fake_call(ptr_dir: Path, tool: str, intent: str, ptr: str, *, depth: str = "normal") -> None:
    """Simulate handlers.store() by writing a ptr meta file."""
    session = ptr_dir / "pytest"
    session.mkdir(parents=True, exist_ok=True)
    meta = {"tool": tool, "intent": intent, "depth": depth, "ptr": ptr}
    (session / f"{ptr}.json").write_text(json.dumps(meta))
    (session / f"{ptr}.txt").write_text("x")


def _fake_expand(ptr_dir: Path, tool: str, intent: str) -> None:
    session = ptr_dir / "pytest"
    session.mkdir(parents=True, exist_ok=True)
    log = session / "expansions.jsonl"
    with log.open("a") as f:
        f.write(json.dumps({"ptr": "x", "tool": tool, "intent": intent}) + "\n")


class TestTune:
    def test_empty_report(self, router_tmp):
        from dhee.router import tune
        r = tune.build_report()
        assert r.buckets == []
        assert r.suggestions == []

    def test_high_expansion_suggests_deepen(self, router_tmp):
        from dhee.router import tune
        ptr_dir = router_tmp / "ptr"
        for i in range(5):
            _fake_call(ptr_dir, "Read", "source_code", f"R-{i:04d}")
        for _ in range(3):  # 3/5 = 60% → deepen
            _fake_expand(ptr_dir, "Read", "source_code")
        r = tune.build_report()
        assert len(r.suggestions) == 1
        s = r.suggestions[0]
        assert s.bucket.tool == "Read"
        assert s.bucket.intent == "source_code"
        assert s.new_depth == "deep"
        assert s.bucket.current_depth == "normal"

    def test_low_expansion_suggests_shallow_only_above_n(self, router_tmp):
        from dhee.router import tune
        ptr_dir = router_tmp / "ptr"
        # 15 calls, zero expansions — enough samples → shallower
        for i in range(15):
            _fake_call(ptr_dir, "Bash", "listing", f"B-{i:04d}")
        r = tune.build_report()
        assert len(r.suggestions) == 1
        assert r.suggestions[0].new_depth == "shallow"

    def test_low_expansion_but_too_few_samples_no_suggestion(self, router_tmp):
        from dhee.router import tune
        ptr_dir = router_tmp / "ptr"
        for i in range(3):  # below MIN_SAMPLES_FOR_SHALLOWER
            _fake_call(ptr_dir, "Bash", "listing", f"B-{i:04d}")
        r = tune.build_report()
        assert r.suggestions == []

    def test_apply_persists_to_policy(self, router_tmp):
        from dhee.router import policy, tune
        ptr_dir = router_tmp / "ptr"
        for i in range(5):
            _fake_call(ptr_dir, "Read", "source_code", f"R-{i:04d}")
        for _ in range(3):
            _fake_expand(ptr_dir, "Read", "source_code")
        r = tune.build_report()
        n = tune.apply(r)
        assert n == 1
        assert policy.get_depth("Read", "source_code") == "deep"


# ---------------------------------------------------------------------------
# Phase 6 — PreToolUse enforcement gate
# ---------------------------------------------------------------------------


class TestEnforcementGate:
    def test_offnoop_without_flag(self, router_tmp):
        from dhee.router.pre_tool_gate import evaluate
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}
        assert evaluate(payload) == {}

    def _turn_on(self, router_tmp):
        (router_tmp / "enforce").write_text("1\n")
        cfg = router_tmp / ".dhee" / "config.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"repo_id": "test", "schema_version": 1}))

    def test_on_allows_small_read(self, router_tmp, tmp_path):
        self._turn_on(router_tmp)
        small = tmp_path / "small.py"
        small.write_text("x" * 100)  # under 20 KB
        from dhee.router.pre_tool_gate import evaluate
        r = evaluate({"tool_name": "Read", "tool_input": {"file_path": str(small)}})
        assert r == {}

    def test_on_denies_large_read(self, router_tmp, tmp_path):
        self._turn_on(router_tmp)
        big = tmp_path / "big.py"
        big.write_text("x" * (30 * 1024))
        from dhee.router.pre_tool_gate import evaluate
        r = evaluate({"tool_name": "Read", "tool_input": {"file_path": str(big)}})
        assert r.get("permissionDecision") == "deny"
        assert "mcp__dhee__dhee_read" in r.get("additionalContext", "")

    def test_on_noops_outside_initialized_workspace(self, router_tmp, tmp_path):
        self._turn_on(router_tmp)
        outside = tmp_path.parent / f"vanilla-{tmp_path.name}"
        outside.mkdir()
        big = outside / "big.py"
        big.write_text("x" * (30 * 1024))

        from dhee.router.pre_tool_gate import evaluate

        r = evaluate({"tool_name": "Read", "tool_input": {"file_path": str(big)}})
        assert r == {}
        r = evaluate({
            "tool_name": "Bash",
            "tool_input": {"cwd": str(outside), "command": "git log --oneline"},
        })
        assert r == {}

    def test_on_allows_ranged_read(self, router_tmp, tmp_path):
        self._turn_on(router_tmp)
        big = tmp_path / "big.py"
        big.write_text("x" * (30 * 1024))
        from dhee.router.pre_tool_gate import evaluate
        r = evaluate({
            "tool_name": "Read",
            "tool_input": {"file_path": str(big), "offset": 1, "limit": 50},
        })
        assert r == {}

    def test_on_denies_heavy_bash(self, router_tmp):
        self._turn_on(router_tmp)
        from dhee.router.pre_tool_gate import evaluate
        for cmd in ("git log --oneline", "grep -r foo .", "pytest tests/"):
            r = evaluate({"tool_name": "Bash", "tool_input": {"command": cmd}})
            assert r.get("permissionDecision") == "deny", cmd

    def test_on_allows_tiny_bash(self, router_tmp):
        self._turn_on(router_tmp)
        from dhee.router.pre_tool_gate import evaluate
        r = evaluate({"tool_name": "Bash", "tool_input": {"command": "echo hi"}})
        assert r == {}

    def test_on_allows_heavy_bash_with_reducer_pipe(self, router_tmp):
        """A reducer pipe (head/tail/wc/grep -c) bounds the producer's
        output, so heavy-output heuristics should let the command through."""
        self._turn_on(router_tmp)
        from dhee.router.pre_tool_gate import evaluate
        cases = [
            "pytest tests/test_x.py -q 2>&1 | tail -20",
            "git log --oneline | head -n 10",
            "grep -r foo . | wc -l",
            "find . -name '*.py' | head 50",
            "rg foo | grep -c bar",
        ]
        for cmd in cases:
            r = evaluate({"tool_name": "Bash", "tool_input": {"command": cmd}})
            assert r == {}, f"reducer pipe should allow: {cmd}"

    def test_on_allows_heavy_bash_with_explicit_bypass(self, router_tmp):
        self._turn_on(router_tmp)
        from dhee.router.pre_tool_gate import evaluate
        r = evaluate({
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/test_x.py -q  # dhee:bypass"},
        })
        assert r == {}


# ---------------------------------------------------------------------------
# Handlers round-trip — intent + policy attribution
# ---------------------------------------------------------------------------


class TestHandlersRoundTrip:
    def test_read_records_intent_and_depth(self, router_tmp, tmp_path):
        from dhee.router import handlers, ptr_store

        src = tmp_path / "mod.py"
        src.write_text("def f(): return 1\n")
        res = handlers.handle_dhee_read({"file_path": str(src)})
        assert "ptr" in res
        meta = ptr_store.load_meta(res["ptr"])
        assert meta["intent"] == "source_code"
        assert meta["depth"] == "normal"
        assert meta["tool"] == "Read"

    def test_read_honors_policy_depth(self, router_tmp, tmp_path):
        from dhee.router import handlers, policy, ptr_store

        policy.set_depth("Read", "source_code", "deep")
        src = tmp_path / "mod.py"
        src.write_text("def f(): return 1\n")
        res = handlers.handle_dhee_read({"file_path": str(src)})
        meta = ptr_store.load_meta(res["ptr"])
        assert meta["depth"] == "deep"

    def test_explicit_depth_overrides_policy(self, router_tmp, tmp_path):
        from dhee.router import handlers, policy, ptr_store

        policy.set_depth("Read", "source_code", "deep")
        src = tmp_path / "mod.py"
        src.write_text("def f(): return 1\n")
        res = handlers.handle_dhee_read({"file_path": str(src), "digest_depth": "shallow"})
        meta = ptr_store.load_meta(res["ptr"])
        assert meta["depth"] == "shallow"

    def test_read_uses_task_aware_schema_when_query_supplied(self, router_tmp, tmp_path):
        from dhee.router import handlers, ptr_store

        src = tmp_path / "mod.py"
        src.write_text(("def f():\n    raise ValueError('bad')\n\n" * 160))
        res = handlers.handle_dhee_read({"file_path": str(src), "query": "debug failing pytest traceback"})
        meta = ptr_store.load_meta(res["ptr"])

        assert meta["task_intent"] == "debug_failure"
        assert meta["depth"] == "deep"
        assert "task_schema=debug_failure" in res["digest"]
        assert "focus:" in res["digest"]
        assert res["focus_count"] >= 1

    def test_bounded_read_returns_inline_source_window_without_expansion(self, router_tmp, tmp_path):
        from dhee.router import handlers, ptr_store

        src = tmp_path / "mod.py"
        src.write_text("".join(f"line_{i} = {i}\n" for i in range(1, 80)))

        res = handlers.handle_dhee_read({"file_path": str(src), "offset": 20, "limit": 6})

        assert res["ptr"]
        assert res["bounded_source_included"] is True
        assert res["source_window"]["start_line"] == 20
        assert res["source_window"]["end_line"] == 25
        assert "  20 | line_20 = 20" in res["source_window"]["numbered_source"]
        assert res["pointer_recovery"]["if_expand_unavailable"].startswith("call dhee_read again")
        meta = ptr_store.load_meta(res["ptr"])
        assert meta["source_window_included"] is True

    def test_unbounded_read_keeps_raw_behind_pointer_but_explains_recovery(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "mod.py"
        src.write_text("".join(f"line_{i} = {i}\n" for i in range(1, 260)))

        res = handlers.handle_dhee_read({"file_path": str(src)})

        assert "source_window" not in res
        assert "pointer_recovery" in res
        assert "offset+limit" in res["pointer_recovery"]["if_expand_unavailable"]
        assert "explicit bounded reads include source_window inline" in res["digest"]

    def test_include_source_returns_capped_window_not_full_file(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "mod.py"
        src.write_text("".join(f"line_{i} = {i}\n" for i in range(1, 220)))

        res = handlers.handle_dhee_read({"file_path": str(src), "include_source": True})

        assert res["source_window"]["start_line"] == 1
        assert res["source_window"]["line_count"] == 120
        assert res["source_window"]["truncated"] is True
        assert "line_219" not in res["source_window"]["numbered_source"]

    def test_read_infers_task_schema_from_compiled_state(self, router_tmp, tmp_path):
        from dhee.context_state import ContextStateStore
        from dhee.router import handlers, ptr_store

        store = ContextStateStore(repo=os.getcwd(), workspace_id=os.getcwd(), user_id="default", agent_id="test")
        store.observe_prompt("Debug failing pytest traceback in parser")
        src = tmp_path / "parser.py"
        src.write_text("def parse_token(value):\n    raise ValueError(value)\n")

        res = handlers.handle_dhee_read({"file_path": str(src)})
        meta = ptr_store.load_meta(res["ptr"])

        assert meta["task_intent"] == "debug_failure"
        assert meta["task_source"] == "compiled_state"
        assert res["task_source"] == "compiled_state"

    def test_repeated_unchanged_read_is_suppressed_by_stable_hash(self, router_tmp, tmp_path):
        from dhee.context_state import ContextStateStore
        from dhee.router import handlers

        src = tmp_path / "stable.py"
        src.write_text("def stable():\n    return 1\n")

        handlers.handle_dhee_read({"file_path": str(src)})
        handlers.handle_dhee_read({"file_path": str(src)})

        store = ContextStateStore(repo=os.getcwd(), workspace_id=os.getcwd(), user_id="default", agent_id="test")
        audit = store.read_audit_text()
        assert '"decision": "suppress"' in audit
        assert "already admitted" in audit

    def test_read_records_critical_surface_decision(self, router_tmp, tmp_path):
        from dhee.db.sqlite import SQLiteManager
        from dhee.router import handlers

        src = tmp_path / "mod.py"
        src.write_text("def f(x):\n    return x + 1\n" * 256)
        res = handlers.handle_dhee_read({"file_path": str(src)})
        assert "ptr" in res

        db = SQLiteManager(str((router_tmp / "dhee") / "history.db"))
        decisions = db.list_route_decisions(user_id="default", limit=10)
        assert decisions
        decision = decisions[0]
        assert decision["packet_kind"] == "routed_read"
        assert decision["route"] == "reflect"
        assert decision["source_event_id"] == res["ptr"]
        assert decision["token_delta"] > 0
        assert decision["locality_scope"] in {"folder", "workspace", "global"}

    def test_quality_report_includes_critical_surface_summary(self, router_tmp, tmp_path):
        from dhee.router import handlers, quality_report

        src = tmp_path / "guide.md"
        src.write_text(("router memory " * 200) + "\n")
        handlers.handle_dhee_read({"file_path": str(src)})

        report = quality_report.build_report(limit=0)
        assert report.critical_surface["total_decisions"] >= 1
        assert report.critical_surface["by_packet_kind"]["routed_read"] >= 1
        assert report.context_governance["receipt_count"] >= 1
        assert "cache_tier_breakdown" in report.context_governance
        assert report.tool_schema["original_tokens"] > 0
        assert report.tool_schema["tiers"]["strong"]["tokens"] < report.tool_schema["original_tokens"]
        assert "quality_gates" in report.to_dict()
        assert "router_token_savings" in report.quality_gates["gates"]

    def test_quality_gate_verdict_passes_when_targets_are_met(self):
        from dhee.router.quality_report import _quality_gates_section

        gates = _quality_gates_section(
            router={"total_calls": 20, "expansion_rate": 0.04},
            replay={
                "total_calls": 20,
                "annotated_sessions": 2,
                "saved_pct": 62.5,
                "assistant_turns": 4,
                "projected_cache_read_per_turn": 12_000,
                "stale_context_incidents": 0,
                "task_parity": {"pass": 2, "fail": 0, "unknown": 0, "avg_score": 0.98, "score_count": 2},
            },
            context_governance={"receipt_count": 5, "assertion_mismatch_count": 0},
        )

        assert gates["verdict"] == "pass"
        assert gates["gates"]["router_token_savings"]["passed"] is True
        assert gates["gates"]["expansion_rate"]["passed"] is True

    def test_quality_gate_verdict_flags_attention(self):
        from dhee.router.quality_report import _quality_gates_section

        gates = _quality_gates_section(
            router={"total_calls": 20, "expansion_rate": 0.28},
            replay={
                "total_calls": 20,
                "annotated_sessions": 2,
                "saved_pct": 22.0,
                "assistant_turns": 4,
                "projected_cache_read_per_turn": 42_000,
                "stale_context_incidents": 1,
                "task_parity": {"pass": 1, "fail": 1, "unknown": 0, "avg_score": 0.80, "score_count": 2},
            },
            context_governance={"receipt_count": 5, "assertion_mismatch_count": 1},
        )

        assert gates["verdict"] == "attention"
        assert gates["gates"]["router_token_savings"]["passed"] is False
        assert gates["gates"]["cache_read_per_turn"]["passed"] is False
        assert gates["gates"]["stale_context_incidents"]["passed"] is False
        assert gates["gates"]["task_parity_failures"]["passed"] is False
        assert gates["gates"]["task_parity_score"]["passed"] is False

    def test_expand_records_attribution(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "mod.py"
        src.write_text("x = 1\n")
        stored = handlers.handle_dhee_read({"file_path": str(src)})
        ptr = stored["ptr"]
        expanded = handlers.handle_dhee_expand_result({"ptr": ptr, "reason": "needed exact value", "expected": "x assignment"})
        assert expanded["ptr"] == ptr
        assert "x = 1" in expanded["content"]
        assert expanded["expansion"]["reason"] == "needed exact value"

        # Expansion log must have the ptr with tool + intent attribution.
        log = router_tmp / "ptr" / "pytest" / "expansions.jsonl"
        assert log.exists()
        rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        assert any(
            r.get("ptr") == ptr
            and r.get("tool") == "Read"
            and r.get("intent") == "source_code"
            and r.get("reason") == "needed exact value"
            for r in rows
        )

    def test_bash_preview_only_does_not_execute(self, router_tmp, tmp_path):
        from dhee.router import handlers

        marker = tmp_path / "should_not_exist"
        res = handlers.handle_dhee_bash({
            "command": f"echo nope > {marker}",
            "preview_only": True,
        })

        assert res["will_execute"] is False
        assert res["preflight"]["output_risk"] in {"low", "medium", "high"}
        assert not marker.exists()

    def test_ptr_store_writes_private_files(self, router_tmp):
        from dhee.router import ptr_store

        stored = ptr_store.store("secret raw output", tool="Read", meta={"intent": "source_code"})

        assert oct(stored.path.stat().st_mode & 0o777) == "0o600"
        assert oct(stored.path.parent.stat().st_mode & 0o777) == "0o700"

    def test_git_diff_digest_extracts_change_shape(self):
        from dhee.router import bash_digest

        diff = """diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,3 @@
 old = 1
+new = 2
-gone = 3
"""
        digest = bash_digest.digest_bash(cmd="git diff", exit_code=0, duration_ms=1, stdout=diff, stderr="")

        assert digest.cls == "git_diff"
        assert "files_changed=1" in digest.summary
        assert "additions=1" in digest.summary
        assert "deletions=1" in digest.summary


class TestLanguageAwareReadDigest:
    def test_tsx_digest_extracts_types_components_and_exports(self):
        from dhee.router import digest

        src = """
import React from 'react';

export interface Props {
  name: string;
}

export type Mode = 'compact' | 'full';

export const UserPanel: React.FC<Props> = ({ name }) => {
  return <section>{name}</section>;
};

export { UserPanel as Panel };
"""
        d = digest.digest_read("UserPanel.tsx", src)

        assert d.kind == "typescript"
        assert "Props (interface)" in d.symbols["types"]
        assert "Mode (type)" in d.symbols["types"]
        assert "UserPanel" in d.symbols["components"]
        assert "react" in d.symbols["imports"]
        assert "UserPanel" in d.symbols["exports"]

    def test_java_digest_extracts_contract_symbols(self):
        from dhee.router import digest

        src = """
package app;

import java.util.List;

public final class RouterService {
  public List<String> route(String query) {
    return List.of(query);
  }

  private int score(String value) {
    return value.length();
  }
}
"""
        d = digest.digest_read("RouterService.java", src)

        assert d.kind == "java"
        assert "RouterService (class)" in d.symbols["types"]
        assert "route(String query)" in d.symbols["methods"]
        assert "score(String value)" in d.symbols["methods"]
        assert "java.util.List" in d.symbols["imports"]

    def test_logs_digest_extracts_severity_counts_and_signals(self):
        from dhee.router import digest

        src = "\n".join(
            [
                "2026-05-13 INFO boot complete",
                "2026-05-13 WARN retrying request",
                "2026-05-13 ERROR failed to open pack",
                "2026-05-13 ERROR failed to verify signature",
            ]
        )
        d = digest.digest_read("runtime.log", src)

        assert d.kind == "log"
        assert "ERROR=2" in d.symbols["levels"]
        assert "INFO=1" in d.symbols["levels"]
        assert "WARN=1" in d.symbols["levels"]
        assert any("failed to open pack" in item for item in d.symbols["signals"])

    def test_shell_and_sql_digest_extracts_operational_contracts(self):
        from dhee.router import digest

        shell = """
#!/usr/bin/env bash
export DHEE_ENV=dev
start_daemon() {
  python -m dhee.runtime_daemon
}
"""
        sh = digest.digest_read("bootstrap.sh", shell)
        assert "start_daemon()" in sh.symbols["functions"]
        assert "DHEE_ENV" in sh.symbols["variables"]

        sql = """
CREATE TABLE IF NOT EXISTS memories (id text primary key);
CREATE VIEW recent_memories AS SELECT * FROM memories;
CREATE INDEX idx_memories_id ON memories(id);
"""
        sq = digest.digest_read("schema.sql", sql)
        assert "memories (table)" in sq.symbols["objects"]
        assert "recent_memories (view)" in sq.symbols["objects"]
        assert "idx_memories_id (index)" in sq.symbols["objects"]


# ---------------------------------------------------------------------------
# Regression: defensive argument handling (2026-04-17 stress)
# ---------------------------------------------------------------------------


class TestHandlerArgValidation:
    """Handlers must never raise — errors surface as {"error": "..."}."""

    def test_non_numeric_offset_returns_error(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "x.py"
        src.write_text("a\nb\nc\n")
        res = handlers.handle_dhee_read(
            {"file_path": str(src), "offset": "abc", "limit": 2}
        )
        assert "error" in res
        assert "offset" in res["error"]

    def test_non_numeric_limit_returns_error(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "x.py"
        src.write_text("a\nb\nc\n")
        res = handlers.handle_dhee_read(
            {"file_path": str(src), "offset": 1, "limit": "not-a-number"}
        )
        assert "error" in res
        assert "limit" in res["error"]

    def test_negative_limit_returns_error(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "x.py"
        src.write_text("a\n")
        res = handlers.handle_dhee_read(
            {"file_path": str(src), "offset": 1, "limit": -5}
        )
        assert "error" in res

    def test_string_numeric_offset_coerces(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "x.py"
        src.write_text("a\nb\nc\n")
        res = handlers.handle_dhee_read(
            {"file_path": str(src), "offset": "1", "limit": "2"}
        )
        assert "error" not in res
        assert res["line_count"] == 2


class TestDigestXmlSafety:
    """Digest tags must be valid XML (no undeclared namespace prefixes)."""

    def test_read_digest_uses_underscore_tag(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "x.py"
        src.write_text("print('hi')\n")
        res = handlers.handle_dhee_read({"file_path": str(src)})
        digest = res["digest"]
        assert "<dhee_read " in digest
        assert "</dhee_read>" in digest
        assert "<dhee:" not in digest

    def test_bash_digest_uses_underscore_tag(self, router_tmp):
        from dhee.router import handlers

        res = handlers.handle_dhee_bash({"command": "echo hello"})
        digest = res["digest"]
        assert "<dhee_bash " in digest
        assert "</dhee_bash>" in digest
        assert "<dhee:" not in digest

    def test_agent_digest_uses_underscore_tag(self, router_tmp):
        from dhee.router import handlers

        res = handlers.handle_dhee_agent({"text": "some agent output\n"})
        digest = res["digest"]
        assert "<dhee_agent " in digest
        assert "</dhee_agent>" in digest
        assert "<dhee:" not in digest

    def test_agent_digest_supports_typed_schema_hint(self, router_tmp):
        from dhee.router import handlers

        res = handlers.handle_dhee_agent({
            "kind": "LocalizationDigest",
            "text": "confidence: high\n- dhee/context_state.py:120-130 stores receipts\n",
        })

        assert res["schema"] == "LocalizationDigest"
        assert "schema=LocalizationDigest" in res["digest"]
        assert "dhee/context_state.py:120-130" in res["digest"]


class TestPreToolGateQuoteSafety:
    """Heavy-pattern matches must ignore quoted-string arguments."""

    def test_quoted_git_log_allowed(self, router_tmp, monkeypatch):
        from dhee.router.pre_tool_gate import evaluate
        monkeypatch.setenv("DHEE_ROUTER_ENFORCE", "1")
        res = evaluate({
            "tool_name": "Bash",
            "tool_input": {"command": "echo 'not a git log, just text'"},
        })
        assert res == {}

    def test_quoted_pytest_allowed(self, router_tmp, monkeypatch):
        from dhee.router.pre_tool_gate import evaluate
        monkeypatch.setenv("DHEE_ROUTER_ENFORCE", "1")
        res = evaluate({
            "tool_name": "Bash",
            "tool_input": {"command": 'echo "pytest is a tool"'},
        })
        assert res == {}

    def test_real_git_log_still_denied(self, router_tmp, monkeypatch):
        from dhee.router.pre_tool_gate import evaluate
        monkeypatch.setenv("DHEE_ROUTER_ENFORCE", "1")
        res = evaluate({
            "tool_name": "Bash",
            "tool_input": {"command": "git log --oneline -20"},
        })
        assert res.get("permissionDecision") == "deny"

    def test_piped_heavy_command_still_denied(self, router_tmp, monkeypatch):
        from dhee.router.pre_tool_gate import evaluate
        monkeypatch.setenv("DHEE_ROUTER_ENFORCE", "1")
        res = evaluate({
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi | git log --oneline"},
        })
        assert res.get("permissionDecision") == "deny"
