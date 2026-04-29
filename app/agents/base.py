from abc import ABC, abstractmethod
from typing import Any, ClassVar
from uuid import UUID


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

    def __init__(
        self,
        agent_id: UUID,
        config: dict[str, Any],
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.config = config
        # Instance-level override shadows the class tuple. Default to the class
        # list so test agents constructed without a DB row still get their
        # declared permissions.
        self.allowed_tools: list[str] = (
            list(allowed_tools) if allowed_tools is not None else list(type(self).allowed_tools)
        )

    @abstractmethod
    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the agent and return a list of proposed action payloads."""
        ...

    @classmethod
    async def trigger(
        cls,
        context: dict[str, Any],
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Kick off this agent's workflow via the shared runner.

        Typed entrypoint for cross-agent triggers — callers use
        `RevenueRecognitionAgent.trigger(ctx)` instead of a slug string.
        """
        from app.services.agent_runner import run_agent

        return await run_agent(cls.slug, initiated_by=initiated_by, context=context)


class ConversationalAgent(BaseAgent, ABC):
    """Agents that support both workflow execution and conversational chat.

    Subclasses implement `run()` and `get_system_prompt()`. Tool discovery and
    dispatch are handled by the base class using the shared tool registry
    (`app.tools`) and the agent's `allowed_tools` list from the registry.
    """

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the full system prompt string for this agent."""
        ...

    def get_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI-format schemas for tools this agent is allowed to use."""
        from app.tools import get_tool_schemas

        return get_tool_schemas(self.allowed_tools)

    async def execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        """Dispatch a tool call through the shared registry, enforcing allowed_tools."""
        from app.tools import ToolContext, execute_tool as tools_execute

        if name not in self.allowed_tools:
            raise PermissionError(
                f"Tool '{name}' is not allowed for agent '{self.slug}'"
            )
        ctx = ToolContext(
            agent_id=self.agent_id,
            agent_slug=self.slug,
            config=self.config,
        )
        return await tools_execute(name, tool_input, ctx)
