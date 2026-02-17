"""Verify MCP server has exactly 8 tools."""

from engram import mcp_server


class TestMCPToolsSlim:
    def test_exactly_8_tools(self):
        tools = mcp_server.TOOLS
        assert len(tools) == 8, f"Expected 8 tools, got {len(tools)}: {[t.name for t in tools]}"

    def test_core_tools_present(self):
        tool_names = [t.name for t in mcp_server.TOOLS]
        assert "remember" in tool_names
        assert "search_memory" in tool_names
        assert "get_memory" in tool_names
        assert "get_all_memories" in tool_names
        assert "get_memory_stats" in tool_names
        assert "engram_context" in tool_names
        assert "get_last_session" in tool_names
        assert "save_session_digest" in tool_names

    def test_no_duplicate_tool_names(self):
        tool_names = [t.name for t in mcp_server.TOOLS]
        assert len(tool_names) == len(set(tool_names)), "Duplicate tool names found"

    def test_tools_have_input_schemas(self):
        for tool in mcp_server.TOOLS:
            assert tool.inputSchema is not None, f"Tool '{tool.name}' missing inputSchema"
            assert "type" in tool.inputSchema, f"Tool '{tool.name}' schema missing 'type'"
