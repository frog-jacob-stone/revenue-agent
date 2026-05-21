from __future__ import annotations

from typing import Any

from app.config import settings
from app.integrations.hubspot import (
    HubSpotError,
    get_primary_company_id,
    search_contact_by_email,
)
from app.lib.nomalize_utils import normalize_email
from app.tools.base import ToolContext, ToolDefinition


async def _get_contact_by_email(
    ctx: ToolContext,
    *,
    email_address: str,
    **_: Any,
) -> dict[str, Any]:
    email = normalize_email(email_address)
    if email is None:
        return {"status": "error", "error": "Provide a valid email_address."}

    try:
        contact = await search_contact_by_email(settings, email)
        if contact is None:
            return {
                "status": "not_found",
                "message": f"No HubSpot contact found for {email}.",
            }
        primary_company_id = await get_primary_company_id(
            settings, str(contact.get("id"))
        )
    except HubSpotError as exc:
        return {"status": "error", "error": str(exc)}

    props = dict(contact.get("properties") or {})
    name = " ".join(p for p in [props.get("firstname"), props.get("lastname")] if p)

    return {
        "status": "success",
        "contact_id": str(contact.get("id") or ""),
        "email": email,
        "name": name or None,
        "title": props.get("jobtitle"),
        "company_name": props.get("company"),
        "lifecycle_stage": props.get("lifecyclestage"),
        "recent_conversion_event_name": props.get("recent_conversion_event_name"),
        "recent_conversion_date": props.get("recent_conversion_date"),
        "primary_company_id": primary_company_id,
    }


GET_CONTACT_BY_EMAIL = ToolDefinition(
    name="get_contact_by_email",
    description=(
        "Look up a HubSpot contact by email. Returns the contact's basic "
        "profile and the id of their primary associated company (use that "
        "id with `get_company_by_id` to fetch full company context)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "email_address": {
                "type": "string",
                "description": "Email address to look up.",
            },
        },
        "required": ["email_address"],
    },
    execute=_get_contact_by_email,
)
