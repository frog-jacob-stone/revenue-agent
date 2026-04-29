"""Registry of all agents in the system.

The agent class is the single source of truth for slug, name, description,
requires_approval, and allowed_tools. This module only declares the list of
classes and exposes a slug → class lookup. Never add metadata here — put it
on the class.
"""
from app.agents.base import BaseAgent
from app.agents.invoice_analytics import InvoiceAnalyticsAgent
from app.agents.invoice_operations import InvoiceOperationsAgent
from app.agents.planned import (
    AccuracyCriticAgent,
    ContentWriterAgent,
    OutreachAgent,
    ProposalGeneratorAgent,
    SDRResearcherAgent,
    SlideDeckAgent,
    VoiceCriticAgent,
)
from app.agents.revenue_recognition import RevenueRecognitionAgent

AGENTS: tuple[type[BaseAgent], ...] = (
    SDRResearcherAgent,
    OutreachAgent,
    VoiceCriticAgent,
    AccuracyCriticAgent,
    ContentWriterAgent,
    ProposalGeneratorAgent,
    SlideDeckAgent,
    RevenueRecognitionAgent,
    InvoiceOperationsAgent,
    InvoiceAnalyticsAgent,
)


def _assert_unique_slugs() -> None:
    slugs = [cls.slug for cls in AGENTS]
    if len(slugs) != len(set(slugs)):
        dupes = sorted({s for s in slugs if slugs.count(s) > 1})
        raise RuntimeError(f"Duplicate agent slugs: {dupes}")


_assert_unique_slugs()

AGENTS_BY_SLUG: dict[str, type[BaseAgent]] = {cls.slug: cls for cls in AGENTS}
