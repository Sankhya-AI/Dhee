"""Verify MCP server tool contract."""

import pytest

mcp_server = pytest.importorskip("dhee.mcp_server", reason="mcp package not installed")

EXPECTED_TOOL_NAMES = {
    "remember",
    "search_memory",
    "get_memory",
    "get_all_memories",
    "dhee_context",
    "get_last_session",
    "save_session_digest",
    "get_memory_stats",
    "search_skills",
    "apply_skill",
    "log_skill_outcome",
    "record_trajectory_step",
    "mine_skills",
    "get_skill_stats",
    "search_skills_structural",
    "analyze_skill_gaps",
    "decompose_skill",
    "apply_skill_with_bindings",
    "enrich_pending",
    "think",
    "anticipate",
    "record_outcome",
    "reflect",
    "store_intention",
    "dhee_submit_learning",
    "dhee_search_learnings",
    "dhee_promote_learning",
    "dhee_context_status",
    "dhee_context_state",
    "dhee_context_checkpoint",
    "dhee_context_rollover",
    "dhee_context_provision",
    "dhee_scene_world_route",
    "dhee_scene_compile",
    "dhee_scene_search",
    "dhee_context_pack",
    "dhee_task_contract_compile",
    "dhee_task_contract_create",
    "dhee_task_contract_list",
    "dhee_task_contract_get",
    "dhee_task_contract_import",
    "dhee_task_contract_interpret",
    "dhee_contract_supervise_action",
    "dhee_contract_record_observation",
    "dhee_contract_proof_bundle",
    "dhee_contract_runtime_activate",
    "dhee_contract_runtime_status",
    "dhee_contract_runtime_deactivate",
    "dhee_contract_enforcement_set",
    "dhee_contract_enforcement_status",
    "dhee_contract_runtime_doctor",
    "dhee_update_capsule_create",
    "dhee_update_capsule_list",
    "dhee_update_capsule_get",
    "dhee_update_capsule_import",
    "dhee_update_capsule_interpret",
    "dhee_tools_list",
    "dhee_shell",
    "dhee_list_assets",
    "dhee_get_asset",
    "dhee_sync_codex_artifacts",
    "dhee_why",
    "dhee_thread_state",
    "dhee_shared_task",
    "dhee_shared_task_results",
    "dhee_inbox",
    "dhee_broadcast",
    "dhee_context_bootstrap",
    "dhee_handoff",
    # Router tools (digest-at-source wrappers)
    "dhee_read",
    "dhee_bash",
    "dhee_grep",
    "dhee_agent",
    "dhee_expand_result",
}


class TestMCPToolsSlim:
    def test_expected_tool_contract(self):
        tools = mcp_server.TOOLS
        tool_names = [t.name for t in mcp_server.TOOLS]
        assert len(tools) == len(EXPECTED_TOOL_NAMES), (
            f"Expected {len(EXPECTED_TOOL_NAMES)} tools, got {len(tools)}: {tool_names}"
        )
        assert set(tool_names) == EXPECTED_TOOL_NAMES

    def test_no_duplicate_tool_names(self):
        tool_names = [t.name for t in mcp_server.TOOLS]
        assert len(tool_names) == len(set(tool_names)), "Duplicate tool names found"

    def test_server_advertises_context_first_instructions(self):
        instructions = getattr(mcp_server.server, "instructions", "") or ""
        assert "consult Dhee before reconstructing" in instructions
        assert "dhee_context_bootstrap" in instructions
        assert "dhee_handoff" in instructions
        assert "dhee_shared_task_results" in instructions
        assert "dhee_inbox" in instructions
        assert "dhee_search_learnings" in instructions
        assert "Codex session logs" in instructions

    def test_tools_have_input_schemas(self):
        for tool in mcp_server.TOOLS:
            assert tool.inputSchema is not None, f"Tool '{tool.name}' missing inputSchema"
            assert "type" in tool.inputSchema, f"Tool '{tool.name}' schema missing 'type'"

    def test_default_slim_server_has_compiled_state_tools(self):
        slim = pytest.importorskip("dhee.mcp_slim", reason="mcp package not installed")
        names = {tool.name for tool in slim.TOOLS}

        assert "dhee_context_state" in names
        assert "dhee_context_status" in names
        assert "dhee_context_rollover" in names
        assert "dhee_grep" in names
        assert "dhee_context_bootstrap" in names
        assert "dhee_handoff" in names
