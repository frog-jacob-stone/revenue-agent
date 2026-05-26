from __future__ import annotations

from typing import Any

from app.config import settings
from app.integrations.hubspot import HubSpotError, find_form_submission_for_email
from app.lib.nomalize_utils import normalize_email
from app.agents.tools.base import Done, ToolContext, ToolDefinition, ToolReturn


def _interesting_fields(values: dict[str, str]) -> dict[str, str]:
    ignored = {"email", "firstname", "lastname", "first_name", "last_name"}
    return {k: v for k, v in values.items() if v and k.lower() not in ignored}


async def _get_form_submission(
    ctx: ToolContext,
    *,
    email_address: str,
    form_id: str | None = None,
    lookback_days: int = 14,
    **_: Any,
) -> ToolReturn:
    email = normalize_email(email_address)
    if email is None:
        return Done({"status": "error", "error": "Provide a valid email_address."})

    target_form_id = (form_id or settings.hubspot_form_id or "").strip()
    if not target_form_id:
        return Done({
            "status": "error",
            "error": (
                "No form_id provided and HUBSPOT_FORM_ID is not configured. "
                "Set HUBSPOT_FORM_ID in the environment or pass form_id explicitly."
            ),
        })

    if lookback_days < 1:
        lookback_days = 14

    try:
        match = await find_form_submission_for_email(
            settings, target_form_id, email, lookback_days=lookback_days
        )
    except HubSpotError as exc:
        return Done({"status": "error", "error": str(exc)})

    if match is None:
        return Done({
            "status": "not_found",
            "message": (
                f"No submission for {email} in form {target_form_id} "
                f"within the last {lookback_days} days."
            ),
        })

    submission = match.get("submission") or {}
    return Done({
        "status": "success",
        "form_id": target_form_id,
        "submitted_at": match["submitted_at"],
        "page_url": submission.get("pageUrl"),
        "submission_fields": _interesting_fields(match["values"]),
    })


GET_FORM_SUBMISSION = ToolDefinition(
    name="get_form_submission",
    description=(
        "Find the most recent HubSpot form submission for an email in a "
        "specific form. If `form_id` is omitted, falls back to the "
        "HUBSPOT_FORM_ID environment value."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "email_address": {
                "type": "string",
                "description": "Email address to look up.",
            },
            "form_id": {
                "type": "string",
                "description": "HubSpot form id. Optional — defaults to HUBSPOT_FORM_ID setting.",
            },
            "lookback_days": {
                "type": "integer",
                "description": "How many days back to search. Defaults to 14.",
                "minimum": 1,
                "maximum": 90,
            },
        },
        "required": ["email_address"],
    },
    execute=_get_form_submission,
)
