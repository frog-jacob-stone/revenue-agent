from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

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
    allowed_tools: ClassVar[tuple[str, ...]] = ()
    default_config: ClassVar[dict[str, Any]] = {}
    model: ClassVar[str] = ""


class ConversationalAgent(BaseAgent, ABC):
    """Agents that support conversational chat.

    Subclasses implement `get_system_prompt()`. Tool discovery and dispatch are
    handled by the base class using the shared tool registry (`app.tools`) and
    the agent's `allowed_tools` list.
    """

    def __init__(
        self,
        agent_id: UUID,
        config: dict[str, Any],
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.config = config
        self.allowed_tools: list[str] = (
            list(allowed_tools) if allowed_tools is not None else list(type(self).allowed_tools)
        )

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the full system prompt string for this agent."""
        ...

    def get_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI-format schemas for tools this agent is allowed to use."""
        from app.tools import get_tool_schemas

        return get_tool_schemas(self.allowed_tools)

    async def execute_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        *,
        progress: "ProgressEmitter | None" = None,
    ) -> Any:
        """Dispatch a tool call through the shared registry, enforcing allowed_tools.

        `progress`, when provided, is forwarded on ToolContext so the tool can
        emit intermediate events (e.g., workflow node updates) back to a
        streaming caller.
        """
        from app.tools import ToolContext, execute_tool as tools_execute

        if name not in self.allowed_tools:
            raise PermissionError(
                f"Tool '{name}' is not allowed for agent '{self.slug}'"
            )
        ctx = ToolContext(
            agent_id=self.agent_id,
            agent_slug=self.slug,
            config=self.config,
            progress=progress,
        )
        return await tools_execute(name, tool_input, ctx)


class _CriticAgent(BaseAgent):
    """Internal evaluator invoked by orchestrator graphs, not by humans.

    Critics never appear in the inbox: they run as internal nodes in critique
    loops (e.g. `voice_critique`, `accuracy_critique`) and never write to
    `approvals`.
    """

    requires_approval: ClassVar[bool] = False
