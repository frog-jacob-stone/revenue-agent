"""Tool layer.

Tools are organised into domain subpackages (`agent/`, `revenue/`). Each tool
file exports a `ToolDefinition` constant (e.g. `GET_REVENUE_DATA` in
`app/tools/revenue/get_revenue_data.py`); the domain `__init__.py` re-exports
those constants for ergonomic agent imports.

Adding a new tool:
  1. Create `app/tools/<domain>/<tool_name>.py` with a `ToolDefinition`
     named in UPPER_SNAKE_CASE matching the file name (e.g. `GET_REVENUE_DATA`).
  2. Re-export it from that domain's `__init__.py`.
  3. Add the `ToolDefinition` to the relevant agent's `allowed_tools` tuple.

Cross-cutting helpers (`ToolContext`, `ToolDefinition`, `ProgressEmitter`)
live in `app/tools/base.py` and are re-exported here for convenience.
"""
from app.agents.tools.base import (
    AwaitingApproval,
    Blocked,
    Done,
    ProgressEmitter,
    ToolContext,
    ToolDefinition,
    ToolReturn,
)

__all__ = [
    "AwaitingApproval",
    "Blocked",
    "Done",
    "ProgressEmitter",
    "ToolContext",
    "ToolDefinition",
    "ToolReturn",
]
