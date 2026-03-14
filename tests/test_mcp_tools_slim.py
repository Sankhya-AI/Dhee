"""Verify MCP server tool contract."""

from engram import mcp_server

EXPECTED_TOOL_NAMES = {
    "remember",
    "search_memory",
    "get_memory",
    "get_all_memories",
    "engram_context",
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

    def test_tools_have_input_schemas(self):
        for tool in mcp_server.TOOLS:
            assert tool.inputSchema is not None, f"Tool '{tool.name}' missing inputSchema"
            assert "type" in tool.inputSchema, f"Tool '{tool.name}' schema missing 'type'"
