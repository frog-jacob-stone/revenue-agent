"""Smoke tests for the domain RevenueOpsAgent (revenue tools + rev-rec knowledge)."""
from uuid import uuid4

from app.agents.registry import AGENTS_BY_SLUG
from app.agents.revenue_ops_agent import RevenueOpsAgent


def test_revenue_ops_registered():
    assert "revenue-ops" in AGENTS_BY_SLUG
    assert AGENTS_BY_SLUG["revenue-ops"] is RevenueOpsAgent


def test_revenue_ops_owns_revenue_tools():
    inst = RevenueOpsAgent(agent_id=uuid4())
    names = {t.name for t in inst.allowed_tools}
    assert names == {"trigger_revenue_recognition", "get_revenue_data"}


def test_system_prompt_carries_domain_rules():
    inst = RevenueOpsAgent(agent_id=uuid4())
    prompt = inst.get_system_prompt()
    # Key domain rules the rev-rec specialist must encode.
    assert "revenue_delta" in prompt
    assert "blended_rate" in prompt
    assert "trigger_revenue_recognition" in prompt
