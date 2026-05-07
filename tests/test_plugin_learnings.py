from dhee import DheePlugin


def test_system_prompt_renders_promoted_learnings_only(tmp_path):
    plugin = DheePlugin(data_dir=tmp_path / "dhee", in_memory=True, offline=True)
    candidate = plugin.submit_learning(
        title="Use narrow tests",
        body="Run the smallest relevant test target before a broad regression suite.",
        kind="heuristic",
        source_agent_id="agent-a",
        source_harness="codex",
    )

    prompt = plugin._render_system_prompt({"learnings": []})
    assert "Use narrow tests" not in prompt

    plugin.promote_learning(candidate["id"], approved_by="test")
    ctx = plugin.context("test target")
    prompt = plugin._render_system_prompt(ctx)
    assert "### Learned Playbooks" in prompt
    assert "Use narrow tests" in prompt
