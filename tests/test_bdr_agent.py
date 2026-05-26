"""Tests for BDRAgent — domain agent with autonomous tool execution."""
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


def test_bdr_has_crm_tools():
    tool_names = {t.name for t in BDRAgent.allowed_tools}
    assert {
        "get_contact_by_email",
        "get_company_by_id",
        "get_form_submission",
    }.issubset(tool_names)


def test_bdr_tool_schemas_have_openai_shape():
    for tool in BDRAgent.allowed_tools:
        schema = tool.as_openai_schema()
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "parameters" in schema["function"]


def test_bdr_cannot_delegate_by_default():
    """Domain agents have empty `available_agents` — they can't call ask_agent."""
    assert BDRAgent.available_agents == ()
