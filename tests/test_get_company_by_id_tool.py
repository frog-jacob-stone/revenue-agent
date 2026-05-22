"""Tests for the CRM `get_company_by_id` tool."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app.integrations.hubspot import HubSpotNotConfigured
from app.tools.base import ToolContext
from app.tools.crm.get_company_by_id import _get_company_by_id

_CTX = ToolContext(agent_id=uuid.UUID(int=0), agent_slug="bdr")

_COMPANY = {
    "id": "co-1",
    "properties": {
        "name": "BrightPath Systems",
        "domain": "brightpath.example",
        "industry": "Logistics",
        "numberofemployees": "320",
        "annualrevenue": "75000000",
        "city": "Dallas",
        "state": "TX",
        "country": "US",
    },
}


def _patch(company=_COMPANY, side_effect=None):
    kwargs = (
        {"new": AsyncMock(side_effect=side_effect)}
        if side_effect
        else {"new": AsyncMock(return_value=company)}
    )
    return patch("app.tools.crm.get_company_by_id.get_company", **kwargs)


async def test_returns_company_profile():
    with _patch():
        result = (await _get_company_by_id(_CTX, company_id="co-1")).payload

    assert result["status"] == "success"
    assert result["company_id"] == "co-1"
    assert result["name"] == "BrightPath Systems"
    assert result["domain"] == "brightpath.example"
    assert result["industry"] == "Logistics"
    assert result["employees"] == "320"
    assert result["city"] == "Dallas"


async def test_returns_not_found_when_company_missing():
    with _patch(company=None):
        result = (await _get_company_by_id(_CTX, company_id="missing")).payload

    assert result["status"] == "not_found"
    assert "missing" in result["message"]


async def test_blank_company_id_returns_error_without_calling_hubspot():
    with patch(
        "app.tools.crm.get_company_by_id.get_company", new=AsyncMock()
    ) as mock_get:
        result = (await _get_company_by_id(_CTX, company_id="   ")).payload

    assert result == {"status": "error", "error": "Provide a company_id."}
    mock_get.assert_not_awaited()


async def test_hubspot_not_configured_returns_error():
    with _patch(side_effect=HubSpotNotConfigured("HUBSPOT_TOKEN is not configured.")):
        result = (await _get_company_by_id(_CTX, company_id="co-1")).payload

    assert result["status"] == "error"
    assert "HUBSPOT_TOKEN" in result["error"]
