"""Smoke tests for the domain RevenueOpsAgent (revenue tools + rev-rec knowledge)."""
from uuid import uuid4

from app.agents.registry import AGENTS_BY_SLUG
from app.agents.revenue_ops_agent import RevenueOpsAgent


def test_revenue_ops_registered():
    assert "revenue-ops" in AGENTS_BY_SLUG
    assert AGENTS_BY_SLUG["revenue-ops"] is RevenueOpsAgent


def test_revenue_ops_is_analysis_only():
    """ADR-0004: `trigger_revenue_recognition` is no longer agent-reachable.

    Running recognition is an operator action. The tool and its executor still
    exist — see tests/test_no_agent_approval_tools.py — but nothing an LLM holds
    can reach them.
    """
    inst = RevenueOpsAgent(agent_id=uuid4())
    names = {t.name for t in inst.allowed_tools}
    assert names == {"get_revenue_data"}


def test_system_prompt_carries_domain_rules():
    inst = RevenueOpsAgent(agent_id=uuid4())
    prompt = inst.get_system_prompt()
    # Key domain rules the rev-rec specialist must encode.
    assert "revenue_delta" in prompt
    assert "blended_rate" in prompt


def test_system_prompt_says_it_cannot_run_rev_rec():
    """Silent absence invites the LLM to claim it ran rev rec, or to hunt for a
    tool it doesn't have. The prompt has to name the limit."""
    prompt = RevenueOpsAgent(agent_id=uuid4()).get_system_prompt()
    assert "cannot run monthly recognition" in prompt.lower()
