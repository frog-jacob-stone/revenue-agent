from __future__ import annotations

import json as _json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.lib.nomalize_utils import normalize_email

logger = logging.getLogger(__name__)

_LOG_BODY_MAX = 2000

_BASE = "https://api.hubapi.com"
_CONTACT_PROPERTIES = [
    "email",
    "firstname",
    "lastname",
    "jobtitle",
    "company",
    "phone",
    "website",
    "lifecyclestage",
    "createdate",
    "lastmodifieddate",
    "recent_conversion_date",
    "recent_conversion_event_name",
]
_COMPANY_PROPERTIES = [
    "name",
    "domain",
    "industry",
    "numberofemployees",
    "annualrevenue",
    "city",
    "state",
    "country",
    "website",
    "description",
]


class HubSpotError(RuntimeError):
    """Base class for HubSpot read errors surfaced to tools."""


class HubSpotNotConfigured(HubSpotError):
    """Raised when HUBSPOT_TOKEN is not configured."""


class HubSpotAuthError(HubSpotError):
    """Raised when HubSpot rejects the configured token."""


class HubSpotApiError(HubSpotError):
    """Raised for non-auth HubSpot API failures."""


def _headers(cfg: Settings) -> dict[str, str]:
    token = cfg.hubspot_token.strip()
    if not token:
        raise HubSpotNotConfigured("HUBSPOT_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _new_http_client() -> httpx.AsyncClient:
    """Factory for the httpx client. Tests patch this to inject MockTransport."""
    return httpx.AsyncClient(timeout=20.0)


def _truncate(s: str, limit: int = _LOG_BODY_MAX) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... [truncated, {len(s)} chars total]"


async def _request(
    cfg: Settings,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body_repr = _truncate(_json.dumps(json)) if json is not None else None
    logger.info(
        "HubSpot → %s %s params=%s body=%s",
        method,
        path,
        params or {},
        body_repr,
    )
    async with _new_http_client() as client:
        try:
            resp = await client.request(
                method,
                f"{_BASE}{path}",
                headers=_headers(cfg),
                params=params,
                json=json,
            )
            logger.info(
                "HubSpot ← %d %s %s %s",
                resp.status_code,
                method,
                path,
                _truncate(resp.text),
            )
            if resp.status_code in {401, 403}:
                raise HubSpotAuthError(
                    "HubSpot rejected HUBSPOT_TOKEN. Check the private app token and scopes."
                )
            if resp.status_code == 429:
                raise HubSpotApiError("HubSpot rate limit reached.")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HubSpotApiError(
                f"HubSpot API request failed with status {exc.response.status_code}."
            ) from exc


def _submitted_at(value: Any) -> datetime | None:
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(raw / 1000, tz=UTC)


def _submission_values(submission: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in submission.get("values") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        values[name] = str(item.get("value") or "").strip()
    return values


def submission_matches_email(submission: dict[str, Any], email: str) -> bool:
    needle = normalize_email(email)
    for name, value in _submission_values(submission).items():
        if normalize_email(value) != needle:
            continue
        return name.lower() == "email" or "email" in name.lower() or "@" in value
    return False


async def list_forms(cfg: Settings) -> list[dict[str, Any]]:
    forms: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 100, "formTypes": "all"}
        if after:
            params["after"] = after
        data = await _request(cfg, "GET", "/marketing/v3/forms", params=params)
        forms.extend(data.get("results") or [])
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return forms


async def iter_form_submissions(
    cfg: Settings,
    form_guid: str,
) -> AsyncIterator[dict[str, Any]]:
    after: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 50}
        if after:
            params["after"] = after
        data = await _request(
            cfg,
            "GET",
            f"/form-integrations/v1/submissions/forms/{form_guid}",
            params=params,
        )
        for submission in data.get("results") or []:
            yield submission
        after = ((data.get("paging") or {}).get("next") or {}).get("after")
        if not after:
            return


async def search_contact_by_email(
    cfg: Settings,
    email: str,
) -> dict[str, Any] | None:
    data = await _request(
        cfg,
        "POST",
        "/crm/objects/2026-03/contacts/search",
        json={
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "EQ",
                            "value": normalize_email(email),
                        }
                    ]
                }
            ],
            "properties": _CONTACT_PROPERTIES,
            "limit": 1,
        },
    )
    results = data.get("results") or []
    return results[0] if results else None


async def get_primary_company_id(
    cfg: Settings,
    contact_id: str,
) -> str | None:
    """Return the id of the contact's primary associated company, if any."""
    assoc = await _request(
        cfg,
        "GET",
        f"/crm/objects/2026-03/contacts/{contact_id}/associations/companies",
    )
    company_refs = assoc.get("results") or []
    if not company_refs:
        return None

    def is_primary(ref: dict[str, Any]) -> bool:
        for assoc_type in ref.get("associationTypes") or []:
            if assoc_type.get("label") == "Primary" or assoc_type.get("typeId") == 1:
                return True
        return False

    selected = next((ref for ref in company_refs if is_primary(ref)), company_refs[0])
    company_id = str(selected.get("toObjectId") or selected.get("id") or "")
    return company_id or None


async def get_company(
    cfg: Settings,
    company_id: str,
) -> dict[str, Any] | None:
    """Fetch a company by id with the standard property set."""
    params = {"properties": ",".join(_COMPANY_PROPERTIES)}
    return await _request(
        cfg,
        "GET",
        f"/crm/objects/2026-03/companies/{company_id}",
        params=params,
    )


async def find_form_submission_for_email(
    cfg: Settings,
    form_id: str,
    email: str,
    *,
    lookback_days: int = 14,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Find the most recent submission in `form_id` that matches `email`.

    Iterates the form's submissions newest-first and returns the first match
    within the lookback window. Returns None if no match is found.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=lookback_days)

    async for submission in iter_form_submissions(cfg, form_id):
        submitted_at = _submitted_at(submission.get("submittedAt"))
        if submitted_at is None:
            continue
        if submitted_at < cutoff:
            return None
        if not submission_matches_email(submission, email):
            continue
        return {
            "form_id": form_id,
            "submission": submission,
            "submitted_at": submitted_at.isoformat(),
            "values": _submission_values(submission),
        }

    return None
