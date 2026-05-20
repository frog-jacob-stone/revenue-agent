from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Sequence
from uuid import UUID

from app.tools.base import ToolDefinition

if TYPE_CHECKING:
    from app.tools import ProgressEmitter


class BaseAgent(ABC):
    """Base class for all revenue agents.

    Subclasses declare code-owned metadata as class attributes — these are the
    single source of truth for slug/name/description/permissions. The DB
    `agents` table is derived from these via `app/seed.py`.
    """

    slug: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str] = ""
    requires_approval: ClassVar[bool] = True
    allowed_tools: ClassVar[tuple[ToolDefinition, ...]] = ()
    model: ClassVar[str] = ""


class ConversationalAgent(BaseAgent, ABC):
    """Agents that support conversational chat.

    Subclasses implement `get_system_prompt()`. Tool discovery and dispatch are
    handled by the base class against the agent's `allowed_tools` — a tuple of
    `ToolDefinition` references that the LLM sees as available functions.
    """

    def __init__(
        self,
        agent_id: UUID,
        allowed_tools: Sequence[ToolDefinition] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.allowed_tools: list[ToolDefinition] = (
            list(allowed_tools) if allowed_tools is not None else list(type(self).allowed_tools)
        )
        self._tool_by_name: dict[str, ToolDefinition] = {
            t.name: t for t in self.allowed_tools
        }

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the full system prompt string for this agent."""
        ...

    def get_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI-format schemas for tools this agent is allowed to use."""
        return [t.as_openai_schema() for t in self.allowed_tools]

    async def execute_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        *,
        progress: "ProgressEmitter | None" = None,
    ) -> Any:
        """Dispatch a tool call against the agent's allowed_tools.

        The LLM's tool_call carries a `name` string; we resolve it against
        the agent's own allowed list (least-privilege enforced by lookup
        rather than a separate permission check). `progress`, when provided,
        is forwarded on ToolContext so the tool can emit intermediate
        events back to a streaming caller.
        """
        from app.tools import ToolContext

        tool = self._tool_by_name.get(name)
        if tool is None:
            raise PermissionError(
                f"Tool '{name}' is not allowed for agent '{self.slug}'"
            )
        ctx = ToolContext(
            agent_id=self.agent_id,
            agent_slug=self.slug,
            progress=progress,
        )
        return await tool.execute(ctx, **tool_input)


