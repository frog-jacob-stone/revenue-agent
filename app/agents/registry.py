"""Registry of all agents in the system.

The agent class is the single source of truth for slug, name, description,
requires_approval, and allowed_tools. This module only declares the list of
classes and exposes a slug → class lookup. Never add metadata here — put it
on the class.

After the inline-LLM-tools cleanup (plan 12), this registry contains only
identity-bearing agents:
  - `revenue-ops` — the single conversational front door
  - `bdr` — Business Development Representative (worker; outreach attribution)
  - `revenue-recognition` — domain worker; rev-rec approval attribution
  - `content-orchestrator` — domain worker; content approval attribution

Single-turn LLM calls made by graph nodes are NOT agents. Their prompts live
inline in the graph files as `MODEL` + `SYSTEM_PROMPT` constants, attributed
to free-form slug strings via `Attribution(agent_slug=..., purpose=...)` on the
dispatcher.
"""
from app.agents.base import BaseAgent
from app.agents.bdr import BDRAgent
from app.agents.content import ContentOrchestratorAgent
from app.agents.revenue import RevenueRecognitionAgent
from app.agents.revenue_ops import RevenueOpsAgent

AGENTS: tuple[type[BaseAgent], ...] = (
    RevenueOpsAgent,
    BDRAgent,
    RevenueRecognitionAgent,
    ContentOrchestratorAgent,
)


def _assert_unique_slugs() -> None:
    slugs = [cls.slug for cls in AGENTS]
    if len(slugs) != len(set(slugs)):
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        raise RuntimeError(f"Duplicate agent slugs: {dupes}")


_assert_unique_slugs()

AGENTS_BY_SLUG: dict[str, type[BaseAgent]] = {cls.slug: cls for cls in AGENTS}
