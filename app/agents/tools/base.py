import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import UUID

# ── Tool return shapes (ADR-0002) ──────────────────────────────────────────


@dataclass(frozen=True)
class Done:
    """Terminal-success return. `payload` is what the LLM sees as the tool result."""
    payload: dict[str, Any]


@dataclass(frozen=True)
class AwaitingApproval:
    """Tool computed a proposed change; write is gated on human approval.

    The runtime writes an `approvals` row with `executor` and `payload`,
    and surfaces a status dict to the LLM. The LLM cannot act on the
    approval — only the human, via the inbox UI, can grant it.
    """
    executor: str
    payload: dict[str, Any]
    summary: str
    action_type: str = "other"
    reasoning: str | None = None
    risk_level: str | None = None


@dataclass(frozen=True)
class Blocked:
    """Precondition not met. The LLM tells the user what to fix and re-trigger."""
    reason: str
    hint: dict[str, Any] | None = None


ToolReturn = Done | AwaitingApproval | Blocked


ToolExecutor = Callable[..., Awaitable[ToolReturn]]


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


class ProgressEmitter:
    """Async fan-in queue for streaming progress events out of a tool call.

    The chat stream creates one of these per tool invocation, passes it on
    ToolContext, and drains it concurrently with the tool's execution.
    Tools that spawn workflows (or otherwise have intermediate progress
    worth surfacing) call `emit({...})`. Tools that don't simply ignore it.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def emit(self, event: dict[str, Any]) -> None:
        self._queue.put_nowait(event)

    def close(self) -> None:
        self._queue.put_nowait(None)

    async def drain(self):
        """Yield queued events until close() is called."""
        while True:
            evt = await self._queue.get()
            if evt is None:
                return
            yield evt


@dataclass
class ToolContext:
    agent_id: UUID
    agent_slug: str
    workflow_id: UUID | None = None  # set when invoked from a graph node; None for chat
    progress: ProgressEmitter | None = None  # set when invoked from streaming chat
