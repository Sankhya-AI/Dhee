from dhee.router import ptr_store, stats


def test_compute_stats_can_filter_by_agent(monkeypatch, tmp_path):
    monkeypatch.setenv("DHEE_ROUTER_PTR_DIR", str(tmp_path / "ptrs"))

    codex = ptr_store.store(
        "x" * 70,
        tool="Read",
        meta={"agent_id": "codex", "char_count": 70},
    )
    ptr_store.store(
        "y" * 35,
        tool="Bash",
        meta={"agent_id": "claude-code", "stdout_bytes": 35, "class": "pytest"},
    )
    ptr_store.record_expansion(codex.ptr, tool="Read", intent="source_code", agent_id="codex")

    all_stats = stats.compute_stats()
    codex_stats = stats.compute_stats(agent_id="codex")
    claude_stats = stats.compute_stats(agent_id="claude-code")
    agents = stats.list_agent_stats()

    assert all_stats.total_calls == 2
    assert all_stats.est_tokens_diverted == 30
    assert codex_stats.total_calls == 1
    assert codex_stats.est_tokens_diverted == 20
    assert codex_stats.expansion_calls == 1
    assert claude_stats.total_calls == 1
    assert claude_stats.est_tokens_diverted == 10
    assert {agent["id"] for agent in agents} == {"codex", "claude-code"}


def test_compute_stats_recovers_codexexec_bytes_from_raw_ptr(monkeypatch, tmp_path):
    monkeypatch.setenv("DHEE_ROUTER_PTR_DIR", str(tmp_path / "ptrs"))

    stored = ptr_store.store(
        "stdout line 1\nstdout line 2\n",
        tool="CodexExec",
        meta={"harness": "codex", "class": "generic"},
    )
    assert stored.path.exists()

    codex_stats = stats.compute_stats(agent_id="codex")
    agents = {agent["id"]: agent for agent in stats.list_agent_stats()}

    assert codex_stats.total_calls == 1
    assert codex_stats.bytes_stored == len("stdout line 1\nstdout line 2\n")
    assert codex_stats.est_tokens_diverted > 0
    assert agents["codex"]["tokensSaved"] == codex_stats.est_tokens_diverted
