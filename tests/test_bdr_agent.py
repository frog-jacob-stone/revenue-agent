"""Smoke tests for the placeholder BDRAgent worker."""
from app.agents.base import BaseAgent, ConversationalAgent
from app.agents.bdr_agent import BDRAgent
from app.agents.registry import AGENTS_BY_SLUG


def test_bdr_registered():
    assert "bdr" in AGENTS_BY_SLUG
    assert AGENTS_BY_SLUG["bdr"] is BDRAgent


def test_bdr_is_worker_not_conversational():
    """BDR is a worker invoked via ask_agent / invoke_agent — NOT a chat agent."""
    assert issubclass(BDRAgent, BaseAgent)
    assert not issubclass(BDRAgent, ConversationalAgent)


def test_bdr_has_identity_level_system_prompt():
    prompt = BDRAgent.system_prompt
    assert isinstance(prompt, str)
    assert len(prompt) > 200
    # Identity, not task. Should describe who the BDR is, not a specific task.
    assert "Frogslayer" in prompt
    assert "Business Development Representative" in prompt or "BDR" in prompt


def test_bdr_no_allowed_tools():
    """Workers don't use tools. Tool use lives on the front-door agent."""
    assert not getattr(BDRAgent, "allowed_tools", ())
