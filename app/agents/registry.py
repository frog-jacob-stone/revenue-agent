"""Registry of all agents in the system.

The agent class is the single source of truth for slug, name, description,
requires_approval, and allowed_tools. This module only declares the list of
classes and exposes a slug → class lookup. Never add metadata here — put it
on the class.

After the inline-LLM-tools cleanup (plan 12), this registry contains only
identity-bearing agents:
  - `chief-of-staff` — the single conversational front door
  - `bdr` — Business Development Representative (worker; outreach attribution)
  - `revenue-ops` — domain worker; revenue recognition + analysis
  - `linkedin` — domain worker; LinkedIn content creation + approval attribution

Single-turn LLM calls made by graph nodes are NOT agents. Their prompts live
inline in the graph files as `MODEL` + `SYSTEM_PROMPT` constants, attributed
to free-form slug strings via `Attribution(agent_slug=..., purpose=...)` on the
dispatcher.
"""
from app.agents.base import Agent
from app.agents.bdr_agent import BDRAgent
from app.agents.chief_of_staff_agent import ChiefOfStaffAgent
from app.agents.linkedin_agent import LinkedInAgent
from app.agents.revenue_ops_agent import RevenueOpsAgent

AGENTS: tuple[type[Agent], ...] = (
    ChiefOfStaffAgent,
    BDRAgent,
    RevenueOpsAgent,
    LinkedInAgent,
)


def _assert_unique_slugs() -> None:
    slugs = [cls.slug for cls in AGENTS]
    if len(slugs) != len(set(slugs)):
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        raise RuntimeError(f"Duplicate agent slugs: {dupes}")


_assert_unique_slugs()

AGENTS_BY_SLUG: dict[str, type[Agent]] = {cls.slug: cls for cls in AGENTS}
