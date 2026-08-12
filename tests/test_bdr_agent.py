"""Tests for BDRAgent — a toolless drafting agent.

The BDR had three HubSpot CRM read tools until HubSpot was removed from the
system. It is deliberately toolless now, not accidentally: `test_bdr_has_no_tools`
guards that, because a stray tool would silently turn a single-turn draft back
into a ReAct loop.
"""
from app.agents.base import Agent
from app.agents.bdr_agent import BDRAgent
from app.agents.registry import AGENTS_BY_SLUG


def test_bdr_registered():
    assert "bdr" in AGENTS_BY_SLUG
    assert AGENTS_BY_SLUG["bdr"] is BDRAgent


def test_bdr_is_an_agent():
    assert issubclass(BDRAgent, Agent)


def test_bdr_has_identity_level_system_prompt():
    prompt = BDRAgent().get_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 200
    assert "Frogslayer" in prompt
    assert "Business Development Representative" in prompt or "BDR" in prompt


def test_bdr_has_no_tools():
    """No CRM to read, so nothing to call — the draft comes from the prompt alone."""
    assert BDRAgent.allowed_tools == ()


def test_bdr_prompt_does_not_promise_tools():
    """A prompt telling the model to fetch context it cannot fetch invites invention."""
    prompt = BDRAgent().get_system_prompt().lower()
    assert "hubspot" not in prompt
    assert "no tools" in prompt


def test_bdr_cannot_delegate_by_default():
    """Domain agents have empty `available_agents` — they can't call ask_agent."""
    assert BDRAgent.available_agents == ()
