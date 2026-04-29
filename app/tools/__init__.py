from typing import Any

from app.tools.agent_tools import (
    TRIGGER_INVOICE_GENERATION,
    TRIGGER_REVENUE_RECOGNITION,
)
from app.tools.base import ToolContext, ToolDefinition
from app.tools.harvest_tools import (
    GET_INVOICE_DETAILS,
    GET_UNBILLED_TIME_ENTRIES,
    LIST_CLIENT_PROJECTS,
    LIST_HARVEST_CLIENTS,
    LIST_INVOICES,
)
from app.tools.internal_tools import GET_PENDING_INVOICE_APPROVALS
from app.tools.revenue_tools import GET_REVENUE_DATA

_ALL_TOOLS: list[ToolDefinition] = [
    # Harvest reads
    LIST_HARVEST_CLIENTS,
    LIST_CLIENT_PROJECTS,
    LIST_INVOICES,
    GET_INVOICE_DETAILS,
    GET_UNBILLED_TIME_ENTRIES,
    # Internal reads
    GET_PENDING_INVOICE_APPROVALS,
    # Revenue
    GET_REVENUE_DATA,
    # Agent triggers
    TRIGGER_INVOICE_GENERATION,
    TRIGGER_REVENUE_RECOGNITION,
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
    "ToolContext",
    "ToolDefinition",
    "execute_tool",
    "get_tool_schemas",
]
