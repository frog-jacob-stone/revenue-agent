"""Smoke tests for the front-door ChiefOfStaffAgent."""
from uuid import uuid4

from app.agents.chief_of_staff_agent import ChiefOfStaffAgent
from app.agents.registry import AGENTS_BY_SLUG


def test_chief_of_staff_registered():
    assert "chief-of-staff" in AGENTS_BY_SLUG
    assert AGENTS_BY_SLUG["chief-of-staff"] is ChiefOfStaffAgent


def test_system_prompt_non_empty():
    inst = ChiefOfStaffAgent(agent_id=uuid4())
    prompt = inst.get_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 200
    # Must reference the slugs it knows how to delegate to.
    assert "revenue-ops" in prompt
    assert "linkedin" in prompt
    assert "bdr" in prompt


def test_allowed_tools_is_just_ask_agent():
    inst = ChiefOfStaffAgent(agent_id=uuid4())
    assert {t.name for t in inst.allowed_tools} == {"ask_agent"}


def test_domain_tools_not_on_chief_of_staff():
    """Domain tools are owned by their domain agents, not the front door."""
    inst = ChiefOfStaffAgent(agent_id=uuid4())
    names = {t.name for t in inst.allowed_tools}
    assert "trigger_revenue_recognition" not in names
    assert "get_revenue_data" not in names
    assert "create_post" not in names
    assert "publish_post" not in names


def test_get_tools_returns_schemas_for_allowed_tools():
    inst = ChiefOfStaffAgent(agent_id=uuid4())
    schemas = inst.get_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "ask_agent" in names
