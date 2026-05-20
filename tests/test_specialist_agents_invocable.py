"""Verify the demoted specialist agents are now plain BaseAgent workers,
invocable via `invoke_agent` (single-turn, no tools).
"""
import pytest

from app.agents.base import BaseAgent, ConversationalAgent
from app.agents.content import ContentOrchestratorAgent
from app.agents.revenue import RevenueRecognitionAgent
from app.integrations.llm import LlmResponse, use_provider
from app.orchestrator.agent_invoke import invoke_agent
from tests.fakes.llm import FakeProvider


def test_revenue_recognition_is_base_agent_only():
    assert issubclass(RevenueRecognitionAgent, BaseAgent)
    assert not issubclass(RevenueRecognitionAgent, ConversationalAgent)
    assert isinstance(RevenueRecognitionAgent.system_prompt, str)
    assert "revenue_delta" in RevenueRecognitionAgent.system_prompt


def test_content_orchestrator_is_base_agent_only():
    assert issubclass(ContentOrchestratorAgent, BaseAgent)
    assert not issubclass(ContentOrchestratorAgent, ConversationalAgent)
    assert isinstance(ContentOrchestratorAgent.system_prompt, str)


@pytest.mark.asyncio
async def test_invoke_agent_works_for_demoted_revenue_agent():
    """The class-level system_prompt path through invoke_agent must work."""
    fake = FakeProvider(
        completions=[
            LlmResponse(
                text="billing_type is one of Fixed Fee | T&M | MSF | Hosting | Retainer.",
                finish_reason="stop",
            )
        ]
    )
    with use_provider(fake):
        response = await invoke_agent(
            "revenue-recognition",
            {"prompt": "What's billing_type?", "max_tokens": 200},
            None,
        )

    assert "Fixed Fee" in response["text"]
    # Verify the class-level system_prompt was wired into messages.
    messages = fake.requests[-1]["messages"]
    assert messages[0]["role"] == "system"
    assert "revenue_delta" in messages[0]["content"]


@pytest.mark.asyncio
async def test_invoke_agent_works_for_demoted_content_agent():
    fake = FakeProvider(
        completions=[LlmResponse(text="Three angles: (1) ... (2) ... (3) ...", finish_reason="stop")]
    )
    with use_provider(fake):
        response = await invoke_agent(
            "content-orchestrator",
            {"prompt": "Three angles about AI agents in revenue ops", "max_tokens": 200},
            None,
        )

    assert "angles" in response["text"]
