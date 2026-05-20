"""Verify the demoted specialist agents are now plain BaseAgent workers,
invocable via `invoke_agent` (single-turn, no tools).
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.base import BaseAgent, ConversationalAgent
from app.agents.content import ContentOrchestratorAgent
from app.agents.revenue import RevenueRecognitionAgent
from app.orchestrator.agent_invoke import invoke_agent


def test_revenue_recognition_is_base_agent_only():
    assert issubclass(RevenueRecognitionAgent, BaseAgent)
    assert not issubclass(RevenueRecognitionAgent, ConversationalAgent)
    assert isinstance(RevenueRecognitionAgent.system_prompt, str)
    assert "revenue_delta" in RevenueRecognitionAgent.system_prompt


def test_content_orchestrator_is_base_agent_only():
    assert issubclass(ContentOrchestratorAgent, BaseAgent)
    assert not issubclass(ContentOrchestratorAgent, ConversationalAgent)
    assert isinstance(ContentOrchestratorAgent.system_prompt, str)


def _completion(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text, tool_calls=None, role="assistant"),
            finish_reason="stop",
        )],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=10, total_tokens=20,
            model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        ),
    )


@pytest.mark.asyncio
async def test_invoke_agent_works_for_demoted_revenue_agent():
    """The class-level system_prompt path through invoke_agent must work."""
    seen: dict = {}

    async def fake_call(**kwargs):
        seen["messages"] = kwargs.get("messages")
        return _completion("billing_type is one of Fixed Fee | T&M | MSF | Hosting | Retainer.")

    with patch("app.orchestrator.agent_invoke.call_openai_chat", side_effect=fake_call):
        response = await invoke_agent(
            "revenue-recognition",
            {"prompt": "What's billing_type?", "max_tokens": 200},
            None,
        )

    assert "Fixed Fee" in response["text"]
    # Verify the class-level system_prompt was wired into messages.
    assert seen["messages"][0]["role"] == "system"
    assert "revenue_delta" in seen["messages"][0]["content"]


@pytest.mark.asyncio
async def test_invoke_agent_works_for_demoted_content_agent():
    async def fake_call(**kwargs):
        return _completion("Three angles: (1) ... (2) ... (3) ...")

    with patch("app.orchestrator.agent_invoke.call_openai_chat", side_effect=fake_call):
        response = await invoke_agent(
            "content-orchestrator",
            {"prompt": "Three angles about AI agents in revenue ops", "max_tokens": 200},
            None,
        )

    assert "angles" in response["text"]
