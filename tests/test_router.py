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

    def test_expand_records_attribution(self, router_tmp, tmp_path):
        from dhee.router import handlers

        src = tmp_path / "mod.py"
        src.write_text("x = 1\n")
        stored = handlers.handle_dhee_read({"file_path": str(src)})
        ptr = stored["ptr"]
        expanded = handlers.handle_dhee_expand_result({"ptr": ptr})
        assert expanded["ptr"] == ptr
        assert "x = 1" in expanded["content"]

        # Expansion log must have the ptr with tool + intent attribution.
        log = router_tmp / "ptr" / "pytest" / "expansions.jsonl"
        assert log.exists()
        rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        assert any(
            r.get("ptr") == ptr
            and r.get("tool") == "Read"
            and r.get("intent") == "source_code"
            for r in rows
        )


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
