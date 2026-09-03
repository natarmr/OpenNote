"""Agent shell: multi-round tool-calling chat over a notebook."""
from opennote.agents.loop import AgentResult, agent_turn
from opennote.agents.tools import TOOL_SCHEMAS, ToolContext, execute_tool, get_tool_schemas, render_tool_results

__all__ = [
    "AgentResult",
    "agent_turn",
    "TOOL_SCHEMAS",
    "ToolContext",
    "execute_tool",
    "get_tool_schemas",
    "render_tool_results",
]