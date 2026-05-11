from typing import Any

from app.tools.agent_tools import ASK_AGENT
from app.tools.base import ProgressEmitter, ToolContext, ToolDefinition
from app.tools.content_tools import ALL_CONTENT_TOOLS
from app.tools.revenue_tools import GET_REVENUE_DATA, TRIGGER_REVENUE_RECOGNITION

_ALL_TOOLS: list[ToolDefinition] = [
    GET_REVENUE_DATA,
    TRIGGER_REVENUE_RECOGNITION,
    ASK_AGENT,
    *ALL_CONTENT_TOOLS,
]

TOOLS: dict[str, ToolDefinition] = {t.name: t for t in _ALL_TOOLS}


def get_tool_schemas(names: list[str]) -> list[dict[str, Any]]:
    """Return OpenAI-format schemas for the named tools, skipping unknowns."""
    schemas: list[dict[str, Any]] = []
    for name in names:
        tool = TOOLS.get(name)
        if tool is None:
            continue
        schemas.append(tool.as_openai_schema())
    return schemas


async def execute_tool(name: str, tool_input: dict[str, Any], ctx: ToolContext) -> Any:
    """Dispatch a tool by name. Raises ValueError if the tool is not registered."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    return await tool.execute(ctx, **tool_input)


__all__ = [
    "TOOLS",
    "ProgressEmitter",
    "ToolContext",
    "ToolDefinition",
    "execute_tool",
    "get_tool_schemas",
]
