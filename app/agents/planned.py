"""Stub agent classes for orchestrator-driven agents.

Agents driven by chains (e.g. Outreach + the critic agents) don't need a
working `run()` — chain step handlers do the work. They still need a class so
the registry can seed an `agents` row with a slug, name, and description that
the chain step writes reference. `_PlannedAgent.run()` raises
NotImplementedError to make accidental legacy-runner invocations loud.
"""
from typing import Any, ClassVar
from uuid import UUID

from app.agents.base import BaseAgent


class _PlannedAgent(BaseAgent):
    """Shared body for agents whose work happens inside an orchestrator chain."""

    allowed_tools: ClassVar[tuple[str, ...]] = ()

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError(
            f"Agent '{self.slug}' has no legacy run() — it is driven by an orchestrator chain."
        )


class OutreachAgent(_PlannedAgent):
    slug = "outreach-agent"
    name = "Outreach Agent"
    description = (
        "Drafts and queues personalised outreach emails after SDR Researcher completes."
    )


class _CriticAgent(_PlannedAgent):
    """Internal evaluator invoked by orchestrator chains, not by humans.

    Critics never appear in the inbox: their step rows are `step_kind=critique`
    which the inbox filter already excludes.
    """

    requires_approval: ClassVar[bool] = False


class VoiceCriticAgent(_CriticAgent):
    slug = "voice-critic"
    name = "Voice Critic"
    description = (
        "Evaluates outbound drafts against the Frogslayer voice profile. Used as "
        "an internal critique step inside the Outreach chain."
    )


class AccuracyCriticAgent(_CriticAgent):
    slug = "accuracy-critic"
    name = "Accuracy Critic"
    description = (
        "Cross-checks outbound drafts for factual claims that aren't supported by "
        "the upstream context. Used as an internal critique step inside the Outreach chain."
    )
