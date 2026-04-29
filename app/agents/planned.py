"""Stub classes for agents that are registered but not yet implemented.

Each stub carries its code-owned metadata (slug, name, description,
requires_approval) so the registry can treat all agents uniformly. `run()`
raises NotImplementedError — these slots exist so DB seeding and dispatch
have a typed reference rather than a string literal.

`allowed_tools` is intentionally empty; real tool names will be wired up
when each agent is actually built out.
"""
from typing import Any, ClassVar
from uuid import UUID

from app.agents.base import BaseAgent


class _PlannedAgent(BaseAgent):
    """Shared body for agents that exist in the registry but have no run()."""

    allowed_tools: ClassVar[tuple[str, ...]] = ()

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError(f"Agent '{self.slug}' is not yet implemented")


class SDRResearcherAgent(_PlannedAgent):
    slug = "sdr-researcher"
    name = "SDR Researcher"
    description = (
        "Researches new accounts from HubSpot or manual triggers. "
        "Enriches contact and company data via Apollo."
    )


class OutreachAgent(_PlannedAgent):
    slug = "outreach-agent"
    name = "Outreach Agent"
    description = (
        "Drafts and queues personalised outreach emails after SDR Researcher completes."
    )


class ContentWriterAgent(_PlannedAgent):
    slug = "content-writer"
    name = "Content Writer"
    description = (
        "Produces marketing and thought-leadership content on a manual or scheduled trigger."
    )


class ProposalGeneratorAgent(_PlannedAgent):
    slug = "proposal-generator"
    name = "Proposal Generator"
    description = (
        "Generates client proposals when a HubSpot deal advances to the proposal stage."
    )


class SlideDeckAgent(_PlannedAgent):
    slug = "slide-deck-agent"
    name = "Slide Deck Agent"
    description = "Converts a completed proposal into a presentation deck."


class _CriticAgent(_PlannedAgent):
    """Internal evaluator invoked by orchestrator chains, not by humans.

    Critics never appear in the inbox: their step rows are `step_kind=critique`
    which the inbox filter already excludes. They have no `run()` because they
    are driven by chain step handlers, not the legacy agent_runner.
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
