from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID


class BaseAgent(ABC):
    """Base class for all revenue agents. Implementations come in the next sprint."""

    slug: str
    name: str

    def __init__(self, agent_id: UUID, config: dict[str, Any]) -> None:
        self.agent_id = agent_id
        self.config = config

    @abstractmethod
    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the agent and return a list of proposed action payloads."""
        ...


class ConversationalAgent(BaseAgent, ABC):
    """Agents that support both workflow execution and conversational chat.

    Implement all three abstract methods. The generic agent_chat() loop calls
    these without knowing which agent it's talking to.
    """

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the full system prompt string for this agent."""
        ...

    @abstractmethod
    def get_tools(self) -> list[dict[str, Any]]:
        """Return OpenAI-format tool schemas."""
        ...

    @abstractmethod
    async def execute_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        """Execute a tool call by name and return a JSON-serialisable result."""
        ...
