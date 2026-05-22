"""End-to-end test: revenue-ops asks BDR to draft an inbound reply.

Covers the full delegation path:
  revenue-ops (ask_agent) → BDR (run_agent_task) →
    CRM tools (get_contact_by_email, get_form_submission, get_company_by_id) →
    final draft returned to revenue-ops.

HubSpot and the LLM are both mocked; the test DB is used for agent_messages
and audit rows.

Patches target the module-level HubSpot functions imported into each tool's
namespace — ToolDefinition.execute captures the function reference at import
time, so patching the integration module directly would not intercept.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

from app.agents.bdr_agent import BDRAgent
from app.integrations.llm import LlmResponse, ToolCall, use_provider
from app.tools import ToolContext
from app.tools.agent.ask_agent import _ask_agent
from tests.fakes.llm import FakeProvider

_HUBSPOT_CONTACT = {
    "id": "c-1",
    "properties": {
        "email": "lead@example.com",
        "firstname": "Lee",
        "lastname": "Morris",
        "jobtitle": "VP Operations",
        "recent_conversion_event_name": "Contact Us",
        "recent_conversion_date": "2026-05-19T12:00:00Z",
    },
}

_HUBSPOT_COMPANY = {
    "id": "co-1",
    "properties": {"name": "BrightPath Systems", "industry": "Logistics"},
}

_HUBSPOT_FORM_MATCH = {
    "form_id": "form-1",
    "submission": {
        "submittedAt": 1748700000000,
        "pageUrl": "https://frogslayer.com/contact",
    },
    "submitted_at": "2026-05-20T15:00:00+00:00",
    "values": {
        "email": "lead@example.com",
        "message": "We need help replacing a legacy operations portal.",
    },
}


def _hubspot_patches(
    *, contact=_HUBSPOT_CONTACT, company=_HUBSPOT_COMPANY, form_match=_HUBSPOT_FORM_MATCH
):
    return (
        patch(
            "app.tools.crm.get_contact_by_email.search_contact_by_email",
            new=AsyncMock(return_value=contact),
        ),
        patch(
            "app.tools.crm.get_contact_by_email.get_primary_company_id",
            new=AsyncMock(return_value=(company or {}).get("id") if company else None),
        ),
        patch(
            "app.tools.crm.get_company_by_id.get_company",
            new=AsyncMock(return_value=company),
        ),
        patch(
            "app.tools.crm.get_form_submission.find_form_submission_for_email",
            new=AsyncMock(return_value=form_match),
        ),
        patch(
            "app.tools.crm.get_form_submission.settings.hubspot_form_id",
            "form-1",
        ),
    )


async def test_ask_agent_bdr_chains_crm_tools_to_draft():
    """revenue-ops delegates to BDR; BDR calls contact → company → form, then drafts."""

    call_log: list[str] = []

    def _respond(request: dict) -> LlmResponse:
        messages = request.get("messages", [])
        tool_results = [m for m in messages if m.get("role") == "tool"]

        if len(tool_results) == 0:
            call_log.append("contact")
            return LlmResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call-c",
                        name="get_contact_by_email",
                        arguments=json.dumps({"email_address": "lead@example.com"}),
                    )
                ],
                finish_reason="tool_calls",
            )
        if len(tool_results) == 1:
            payload = json.loads(tool_results[0]["content"])
            assert payload["status"] == "success"
            assert payload["primary_company_id"] == "co-1"
            call_log.append("company")
            return LlmResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call-co",
                        name="get_company_by_id",
                        arguments=json.dumps({"company_id": "co-1"}),
                    )
                ],
                finish_reason="tool_calls",
            )
        if len(tool_results) == 2:
            company_payload = json.loads(tool_results[1]["content"])
            assert company_payload["industry"] == "Logistics"
            call_log.append("form")
            return LlmResponse(
                text="",
                tool_calls=[
                    ToolCall(
                        id="call-f",
                        name="get_form_submission",
                        arguments=json.dumps({"email_address": "lead@example.com"}),
                    )
                ],
                finish_reason="tool_calls",
            )

        form_payload = json.loads(tool_results[2]["content"])
        assert form_payload["status"] == "success"
        assert form_payload["submission_fields"]["message"].startswith("We need help")
        call_log.append("draft")
        return LlmResponse(
            text=(
                "Hi Lee — saw your note about replacing the legacy operations portal. "
                "Frogslayer helps logistics teams modernise core platforms without stopping "
                "the business. Worth a quick call this week?"
            ),
            finish_reason="stop",
        )

    provider = FakeProvider(respond=_respond)
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="revenue-ops")

    p1, p2, p3, p4, p5 = _hubspot_patches()
    with p1, p2, p3, p4, p5, use_provider(provider):
        result = (await _ask_agent(
            ctx,
            target_slug="bdr",
            prompt="Draft a reply for the form submission from lead@example.com.",
        )).payload

    assert result["answer"]
    assert "Lee" in result["answer"]
    assert result["thread_id"]
    assert call_log == ["contact", "company", "form", "draft"]


async def test_ask_agent_bdr_uses_bdr_system_prompt():
    """BDR system prompt must be in the first LLM request."""
    provider = FakeProvider(completions=[LlmResponse(text="Draft.", finish_reason="stop")])
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="revenue-ops")

    p1, p2, p3, p4, p5 = _hubspot_patches(
        contact=None, company=None, form_match=None
    )
    with p1, p2, p3, p4, p5, use_provider(provider):
        await _ask_agent(
            ctx,
            target_slug="bdr",
            prompt="Draft a reply for nobody@example.com.",
        )

    first_request = provider.requests[0]
    system_msg = next(
        m for m in first_request["messages"] if m.get("role") == "system"
    )
    assert BDRAgent.system_prompt in system_msg["content"]


async def test_ask_agent_no_workflow_started():
    """Agentic task must not spawn a workflow."""
    provider = FakeProvider(completions=[LlmResponse(text="Draft.", finish_reason="stop")])
    ctx = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="revenue-ops")

    p1, p2, p3, p4, p5 = _hubspot_patches(
        contact=None, company=None, form_match=None
    )
    with p1, p2, p3, p4, p5, patch(
        "app.orchestrator.runner.runner.start",
        new=AsyncMock(),
    ) as mock_start, use_provider(provider):
        await _ask_agent(
            ctx,
            target_slug="bdr",
            prompt="Draft a reply for lead@example.com.",
        )

    mock_start.assert_not_awaited()
