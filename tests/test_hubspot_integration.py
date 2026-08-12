from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx

from app.config import Settings
from app.integrations.hubspot import (
    find_form_submission_for_email,
    get_company,
    get_primary_company_id,
    search_contact_by_email,
)


def _settings() -> Settings:
    return Settings(
        env="test",
        database_url="postgresql://postgres:postgres@127.0.0.1:54322/postgres_test",
        hubspot_token="test-token",
    )


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _patched_client(handler):
    """Patch _new_http_client to use a MockTransport-backed AsyncClient."""
    return patch(
        "app.integrations.hubspot._new_http_client",
        side_effect=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# ── search_contact_by_email ───────────────────────────────────────────────────


async def test_search_contact_by_email_sends_normalized_email_in_filter():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/objects/2026-03/contacts/search":
            bodies.append(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "contact-1",
                            "properties": {"email": "lead@example.com", "firstname": "Lee"},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _patched_client(handler):
        contact = await search_contact_by_email(_settings(), "Lead@Example.com")

    assert contact["id"] == "contact-1"
    assert bodies[0]["filterGroups"][0]["filters"][0] == {
        "propertyName": "email",
        "operator": "EQ",
        "value": "lead@example.com",
    }
    # Conversion props must be in the request so consumers can read them off the contact.
    assert "recent_conversion_date" in bodies[0]["properties"]
    assert "recent_conversion_event_name" in bodies[0]["properties"]


async def test_search_contact_by_email_returns_none_when_no_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    with _patched_client(handler):
        contact = await search_contact_by_email(_settings(), "nobody@example.com")

    assert contact is None


# ── get_primary_company_id ────────────────────────────────────────────────────


async def test_get_primary_company_id_picks_primary_association():
    """Prefer the association tagged Primary/typeId=1 over others."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/objects/2026-03/contacts/contact-1/associations/companies":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"toObjectId": "company-2", "associationTypes": [{"typeId": 279}]},
                        {
                            "toObjectId": "company-1",
                            "associationTypes": [{"typeId": 1, "label": "Primary"}],
                        },
                    ]
                },
            )
        return httpx.Response(404)

    with _patched_client(handler):
        company_id = await get_primary_company_id(_settings(), "contact-1")

    assert company_id == "company-1"


async def test_get_primary_company_id_falls_back_to_first_association():
    """If no Primary tag is present, return the first associated company."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/objects/2026-03/contacts/contact-1/associations/companies":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"toObjectId": "company-2", "associationTypes": [{"typeId": 279}]},
                    ]
                },
            )
        return httpx.Response(404)

    with _patched_client(handler):
        company_id = await get_primary_company_id(_settings(), "contact-1")

    assert company_id == "company-2"


async def test_get_primary_company_id_returns_none_when_no_associations():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/associations/companies"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404)

    with _patched_client(handler):
        company_id = await get_primary_company_id(_settings(), "contact-1")

    assert company_id is None


# ── get_company ───────────────────────────────────────────────────────────────


async def test_get_company_fetches_by_id_with_property_list():
    seen_params: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crm/objects/2026-03/companies/company-1":
            seen_params.append(request.url.query.decode())
            return httpx.Response(
                200,
                json={"id": "company-1", "properties": {"name": "Acme Corp"}},
            )
        return httpx.Response(404)

    with _patched_client(handler):
        company = await get_company(_settings(), "company-1")

    assert company["id"] == "company-1"
    # All standard company properties must be requested.
    assert "name" in seen_params[0]
    assert "industry" in seen_params[0]


# ── find_form_submission_for_email ────────────────────────────────────────────


async def test_find_form_submission_returns_matching_submission():
    now = datetime(2026, 5, 20, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/form-integrations/v1/submissions/forms/form-1":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "submittedAt": _ms(now - timedelta(days=2)),
                            "values": [
                                {"name": "email", "value": "lead@example.com"},
                                {"name": "message", "value": "Need help with portal."},
                            ],
                            "pageUrl": "https://frogslayer.com/contact",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _patched_client(handler):
        match = await find_form_submission_for_email(
            _settings(), "form-1", "lead@example.com", now=now
        )

    assert match is not None
    assert match["form_id"] == "form-1"
    assert match["values"]["message"] == "Need help with portal."


async def test_find_form_submission_returns_none_when_email_not_in_form():
    now = datetime(2026, 5, 20, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/form-integrations/v1/submissions/forms/form-1":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "submittedAt": _ms(now - timedelta(days=2)),
                            "values": [
                                {"name": "email", "value": "someone-else@example.com"},
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _patched_client(handler):
        match = await find_form_submission_for_email(
            _settings(), "form-1", "lead@example.com", now=now
        )

    assert match is None


async def test_find_form_submission_skips_entries_outside_lookback():
    """Submissions older than lookback_days stop iteration."""
    now = datetime(2026, 5, 20, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/form-integrations/v1/submissions/forms/form-1":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "submittedAt": _ms(now - timedelta(days=30)),
                            "values": [
                                {"name": "email", "value": "lead@example.com"},
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with _patched_client(handler):
        match = await find_form_submission_for_email(
            _settings(),
            "form-1",
            "lead@example.com",
            lookback_days=14,
            now=now,
        )

    assert match is None
