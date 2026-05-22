"""Tests for the CRM `get_contact_by_email` tool.

HubSpot I/O is mocked at the module-level functions imported into the tool's
namespace (because ToolDefinition.execute captures the function reference at
import time — patches must target the tool module, not the integration).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app.integrations.hubspot import HubSpotAuthError, HubSpotNotConfigured
from app.tools.base import ToolContext
from app.tools.crm.get_contact_by_email import _get_contact_by_email

_CTX = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="bdr")

_CONTACT = {
    "id": "c-1",
    "properties": {
        "email": "lead@example.com",
        "firstname": "Lee",
        "lastname": "Morris",
        "jobtitle": "VP Operations",
        "company": "BrightPath",
        "lifecyclestage": "marketingqualifiedlead",
        "recent_conversion_date": "2026-05-19T12:00:00Z",
        "recent_conversion_event_name": "Contact Us",
    },
}


def _patches(*, contact=_CONTACT, primary_company_id="co-1", search_side_effect=None):
    search_kwargs = (
        {"new": AsyncMock(side_effect=search_side_effect)}
        if search_side_effect
        else {"new": AsyncMock(return_value=contact)}
    )
    return (
        patch(
            "app.tools.crm.get_contact_by_email.search_contact_by_email",
            **search_kwargs,
        ),
        patch(
            "app.tools.crm.get_contact_by_email.get_primary_company_id",
            new=AsyncMock(return_value=primary_company_id),
        ),
    )


async def test_returns_contact_with_primary_company_id():
    p_search, p_company = _patches()
    with p_search, p_company:
        result = (await _get_contact_by_email(_CTX, email_address="lead@example.com")).payload

    assert result["status"] == "success"
    assert result["contact_id"] == "c-1"
    assert result["email"] == "lead@example.com"
    assert result["name"] == "Lee Morris"
    assert result["title"] == "VP Operations"
    assert result["primary_company_id"] == "co-1"
    assert result["recent_conversion_event_name"] == "Contact Us"


async def test_returns_not_found_when_contact_missing():
    p_search, p_company = _patches(contact=None)
    with p_search, p_company:
        result = (await _get_contact_by_email(_CTX, email_address="nobody@example.com")).payload

    assert result["status"] == "not_found"
    assert "nobody@example.com" in result["message"]


async def test_invalid_email_returns_error_without_calling_hubspot():
    with patch(
        "app.tools.crm.get_contact_by_email.search_contact_by_email",
        new=AsyncMock(),
    ) as mock_search:
        result = (await _get_contact_by_email(_CTX, email_address="not-an-email")).payload

    assert result == {"status": "error", "error": "Provide a valid email_address."}
    mock_search.assert_not_awaited()


async def test_hubspot_not_configured_returns_error():
    p_search, p_company = _patches(
        search_side_effect=HubSpotNotConfigured("HUBSPOT_TOKEN is not configured.")
    )
    with p_search, p_company:
        result = (await _get_contact_by_email(_CTX, email_address="lead@example.com")).payload

    assert result["status"] == "error"
    assert "HUBSPOT_TOKEN" in result["error"]


async def test_hubspot_auth_error_returns_error():
    p_search, p_company = _patches(search_side_effect=HubSpotAuthError("Token rejected."))
    with p_search, p_company:
        result = (await _get_contact_by_email(_CTX, email_address="lead@example.com")).payload

    assert result["status"] == "error"


async def test_contact_with_no_associated_company_returns_null_company_id():
    p_search, p_company = _patches(primary_company_id=None)
    with p_search, p_company:
        result = (await _get_contact_by_email(_CTX, email_address="lead@example.com")).payload

    assert result["status"] == "success"
    assert result["primary_company_id"] is None
