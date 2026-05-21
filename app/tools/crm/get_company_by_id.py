from __future__ import annotations

from typing import Any

from app.config import settings
from app.integrations.hubspot import HubSpotError, get_company
from app.tools.base import ToolContext, ToolDefinition


async def _get_company_by_id(
    ctx: ToolContext,
    *,
    company_id: str,
    **_: Any,
) -> dict[str, Any]:
    cid = (company_id or "").strip()
    if not cid:
        return {"status": "error", "error": "Provide a company_id."}

    try:
        company = await get_company(settings, cid)
    except HubSpotError as exc:
        return {"status": "error", "error": str(exc)}

    if company is None:
        return {
            "status": "not_found",
            "message": f"No HubSpot company found for id {cid}.",
        }

    props = dict(company.get("properties") or {})
    return {
        "status": "success",
        "company_id": str(company.get("id") or cid),
        "name": props.get("name"),
        "domain": props.get("domain"),
        "industry": props.get("industry"),
        "employees": props.get("numberofemployees"),
        "annual_revenue": props.get("annualrevenue"),
        "city": props.get("city"),
        "state": props.get("state"),
        "country": props.get("country"),
        "website": props.get("website"),
        "description": props.get("description"),
    }


GET_COMPANY_BY_ID = ToolDefinition(
    name="get_company_by_id",
    description=(
        "Fetch a HubSpot company by its id and return the firmographic "
        "profile (name, domain, industry, size, location)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "company_id": {
                "type": "string",
                "description": "HubSpot company id (returned by `get_contact_by_email` as `primary_company_id`).",
            },
        },
        "required": ["company_id"],
    },
    execute=_get_company_by_id,
)
