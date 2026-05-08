from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID

ToolExecutor = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    execute: ToolExecutor

    def as_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


@dataclass
class ToolContext:
    agent_id: UUID
    agent_slug: str
    config: dict[str, Any] = field(default_factory=dict)
    workflow_id: UUID | None = None  # set when invoked from a graph node; None for chat
