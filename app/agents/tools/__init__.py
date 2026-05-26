"""Tool layer.

Tools are organised into domain subpackages (`agent/`, `content/`, `outreach/`,
`revenue/`). Each tool file exports a `ToolDefinition` constant (e.g.
`CREATE_POST` in `app/tools/content/create_post.py`); the domain `__init__.py`
re-exports those constants for ergonomic agent imports.

Adding a new tool:
  1. Create `app/tools/<domain>/<tool_name>.py` with a `ToolDefinition`
     named in UPPER_SNAKE_CASE matching the file name (e.g. `CREATE_POST`).
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
