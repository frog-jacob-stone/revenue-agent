"""Smoke tests for the front-door RevenueOpsAgent."""
from uuid import uuid4

from app.agents.base import ConversationalAgent
from app.agents.registry import AGENTS_BY_SLUG
from app.agents.revenue_ops_agent import RevenueOpsAgent


def test_revenue_ops_registered():
    assert "revenue-ops" in AGENTS_BY_SLUG
    assert AGENTS_BY_SLUG["revenue-ops"] is RevenueOpsAgent


def test_revenue_ops_is_conversational():
    assert issubclass(RevenueOpsAgent, ConversationalAgent)
    assert RevenueOpsAgent.slug == "revenue-ops"


def test_system_prompt_non_empty():
    inst = RevenueOpsAgent(agent_id=uuid4(), config={})
    prompt = inst.get_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 200
    # Must reference the slugs it knows how to delegate to.
    assert "revenue-recognition" in prompt
    assert "content-orchestrator" in prompt


def test_allowed_tools_include_mandatory_set():
    inst = RevenueOpsAgent(agent_id=uuid4(), config={})
    mandatory = {
        "ask_agent",
        "trigger_revenue_recognition",
        "get_revenue_data",
        "create_post",
        "get_posts",
        "rewrite_post",
        "reject_post",
        "publish_post",
        "export_posts",
    }
    assert mandatory.issubset(set(inst.allowed_tools))


def test_get_tools_returns_schemas_for_allowed_tools():
    inst = RevenueOpsAgent(agent_id=uuid4(), config={})
    schemas = inst.get_tools()
    names = {s["function"]["name"] for s in schemas}
    # ask_agent is the load-bearing one — delegation entry point.
    assert "ask_agent" in names
    assert "trigger_revenue_recognition" in names
