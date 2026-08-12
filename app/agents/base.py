from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from app.agents.tools.base import ToolDefinition

if TYPE_CHECKING:
    from app.agents.tools import ProgressEmitter


class Agent:
    """Single base class for every agent in the system.

    Role (Orchestrator vs Domain — see CONTEXT.md) is not encoded in the type
    system. It lives in vocabulary and at the slug level. See ADR-0003.

    Class attributes are the single source of truth for slug / name /
    description / permissions / delegation. The DB `agents` table is derived
    from these via `app/seed.py`.
    """

    slug: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str] = ""
    requires_approval: ClassVar[bool] = True
    allowed_tools: ClassVar[tuple[ToolDefinition, ...]] = ()
    available_agents: ClassVar[tuple[type["Agent"], ...]] = ()
    model: ClassVar[str] = ""

    def __init__(self, agent_id: UUID | None = None) -> None:
        self.agent_id = agent_id
        self._tool_by_name: dict[str, ToolDefinition] = {
            t.name: t for t in type(self).allowed_tools
        }

    def get_system_prompt(self) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} must implement get_system_prompt()"
        )

    def get_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI-format schemas for tools this agent is allowed to use."""
        return [t.as_openai_schema() for t in type(self).allowed_tools]

    def _render_available_agents(self) -> str:
        """Render the delegation roster as ` - slug — description` lines.

        Subclasses with a populated `available_agents` can drop this into
        their system prompt to surface the targets the LLM may reach via
        `ask_agent`. Empty tuple → empty string.
        """
        return "\n".join(
            f"- `{cls.slug}` — {cls.description}" for cls in self.available_agents
        )

    async def execute_tool(
        self,
        name: str,
        tool_input: dict[str, Any],
        *,
        progress: "ProgressEmitter | None" = None,
    ) -> Any:
        """Dispatch a tool call against the agent's allowed_tools.

        Routes through `dispatch_tool` so Done/AwaitingApproval/Blocked
        return shapes are uniformly translated into LLM-visible dicts
        (ADR-0002).
        """
        from app.orchestrator.dispatch import dispatch_tool
        from app.agents.tools import ToolContext

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
        return await dispatch_tool(tool, ctx, tool_input)
