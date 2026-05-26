"""Tests for the CRM `get_form_submission` tool."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app.integrations.hubspot import HubSpotNotConfigured
from app.agents.tools.base import ToolContext
from app.agents.tools.crm.get_form_submission import _get_form_submission

_CTX = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="bdr")

_MATCH = {
    "form_id": "form-1",
    "submission": {
        "submittedAt": 1748700000000,
        "pageUrl": "https://frogslayer.com/contact",
    },
    "submitted_at": "2026-05-20T15:00:00+00:00",
    "values": {
        "email": "lead@example.com",
        "message": "We need help with a legacy portal.",
        "firstname": "Lee",
    },
}


def _patch(match=_MATCH, side_effect=None):
    kwargs = (
        {"new": AsyncMock(side_effect=side_effect)}
        if side_effect
        else {"new": AsyncMock(return_value=match)}
    )
    return patch(
        "app.agents.tools.crm.get_form_submission.find_form_submission_for_email", **kwargs
    )


def _patch_settings_form_id(value: str):
    return patch("app.agents.tools.crm.get_form_submission.settings.hubspot_form_id", value)


async def test_returns_submission_using_explicit_form_id():
    with _patch():
        result = (await _get_form_submission(
            _CTX, email_address="lead@example.com", form_id="form-1"
        )).payload

    assert result["status"] == "success"
    assert result["form_id"] == "form-1"
    assert result["page_url"] == "https://frogslayer.com/contact"
    assert result["submission_fields"]["message"] == "We need help with a legacy portal."
    # PII-ish fields stripped from submission_fields
    assert "email" not in result["submission_fields"]
    assert "firstname" not in result["submission_fields"]


async def test_falls_back_to_configured_form_id():
    captured = {}

    async def fake_find(cfg, form_id, email, **kwargs):
        captured["form_id"] = form_id
        return _MATCH

    with _patch_settings_form_id("default-form-99"), patch(
        "app.agents.tools.crm.get_form_submission.find_form_submission_for_email",
        new=fake_find,
    ):
        result = (await _get_form_submission(_CTX, email_address="lead@example.com")).payload

    assert result["status"] == "success"
    assert captured["form_id"] == "default-form-99"


async def test_missing_form_id_returns_error():
    with _patch_settings_form_id(""), patch(
        "app.agents.tools.crm.get_form_submission.find_form_submission_for_email",
        new=AsyncMock(),
    ) as mock_find:
        result = (await _get_form_submission(_CTX, email_address="lead@example.com")).payload

    assert result["status"] == "error"
    assert "HUBSPOT_FORM_ID" in result["error"]
    mock_find.assert_not_awaited()


async def test_invalid_email_returns_error():
    with patch(
        "app.agents.tools.crm.get_form_submission.find_form_submission_for_email",
        new=AsyncMock(),
    ) as mock_find:
        result = (await _get_form_submission(
            _CTX, email_address="not-an-email", form_id="form-1"
        )).payload

    assert result == {"status": "error", "error": "Provide a valid email_address."}
    mock_find.assert_not_awaited()


async def test_no_match_returns_not_found():
    with _patch(match=None):
        result = (await _get_form_submission(
            _CTX, email_address="nobody@example.com", form_id="form-1"
        )).payload

    assert result["status"] == "not_found"
    assert "nobody@example.com" in result["message"]
    assert "form-1" in result["message"]


async def test_hubspot_not_configured_returns_error():
    with _patch(side_effect=HubSpotNotConfigured("HUBSPOT_TOKEN is not configured.")):
        result = (await _get_form_submission(
            _CTX, email_address="lead@example.com", form_id="form-1"
        )).payload

    assert result["status"] == "error"
    assert "HUBSPOT_TOKEN" in result["error"]
