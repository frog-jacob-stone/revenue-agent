"""Tool registry.

Tools are organised into domain subpackages (`agent/`, `content/`, `revenue/`).
Each subpackage's `__init__.py` exports a list (e.g. `CONTENT_TOOLS`); this
module concatenates them into the single `TOOLS` lookup used by agents.

Adding a new tool:
  1. Create `app/tools/<domain>/<tool_name>.py` with a `ToolDefinition`
     named in UPPER_SNAKE_CASE matching the file name (e.g. `CREATE_POST`).
  2. Add it to that domain's `__init__.py` list (e.g. `CONTENT_TOOLS`).
  3. Add the tool name to the relevant agent's `allowed_tools` tuple.
  4. No change here — the domain lists below are the only registration point.

Cross-cutting helpers (`ToolContext`, `ToolDefinition`, `ProgressEmitter`)
live in `app/tools/base.py` and are re-exported here for convenience.
"""
from typing import Any

from app.tools.agent import AGENT_TOOLS
from app.tools.base import ProgressEmitter, ToolContext, ToolDefinition
from app.tools.content import CONTENT_TOOLS
from app.tools.revenue import REVENUE_TOOLS

_ALL_TOOLS: list[ToolDefinition] = [
    *AGENT_TOOLS,
    *REVENUE_TOOLS,
    *CONTENT_TOOLS,
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
