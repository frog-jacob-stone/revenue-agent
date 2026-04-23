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
