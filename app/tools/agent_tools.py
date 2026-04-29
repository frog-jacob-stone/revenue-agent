from datetime import date
from typing import Any

from app.tools.base import ToolContext, ToolDefinition


async def _trigger_invoice_generation(
    ctx: ToolContext,
    *,
    client_id: int,
    period_start: str,
    period_end: str,
    project_id: int | None = None,
    notes: str = "",
    **_: Any,
) -> dict[str, Any]:
    # Lazy import: avoids a circular (tools → agents → tools).
    from app.agents.invoice_operations import InvoiceOperationsAgent

    return await InvoiceOperationsAgent.trigger(
        context={
            "client_id": int(client_id),
            "period_start": period_start,
            "period_end": period_end,
            "project_id": project_id,
            "notes": notes,
        },
        initiated_by="chat",
    )


TRIGGER_INVOICE_GENERATION = ToolDefinition(
    name="trigger_invoice_generation",
    description=(
        "Generate a draft invoice for a Harvest client and billing period. "
        "Creates a proposed action in the Approval Inbox — does not send anything. "
        "Use list_harvest_clients and list_client_projects first to confirm IDs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "client_id": {"type": "integer", "description": "Harvest client ID."},
            "period_start": {
                "type": "string",
                "description": "Start of billing period, ISO YYYY-MM-DD (inclusive).",
            },
            "period_end": {
                "type": "string",
                "description": "End of billing period, ISO YYYY-MM-DD (inclusive).",
            },
            "project_id": {
                "type": "integer",
                "description": "Specific Harvest project ID. Omit to include all active projects for the client.",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes to include on the invoice.",
            },
        },
        "required": ["client_id", "period_start", "period_end"],
    },
    execute=_trigger_invoice_generation,
)


async def _trigger_revenue_recognition(
    ctx: ToolContext,
    *,
    date_recognized: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    from app.agents.revenue_recognition import RevenueRecognitionAgent

    return await RevenueRecognitionAgent.trigger(
        context={"date_recognized": date_recognized or date.today().isoformat()},
        initiated_by="chat",
    )


TRIGGER_REVENUE_RECOGNITION = ToolDefinition(
    name="trigger_revenue_recognition",
    description=(
        "Trigger the monthly revenue recognition process. "
        "Use when the user asks to run, kick off, or start revenue recognition. "
        "This creates a proposed action in the Approval Inbox."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "date_recognized": {
                "type": "string",
                "description": "Recognition date ISO YYYY-MM-DD. Defaults to today.",
            },
        },
    },
    execute=_trigger_revenue_recognition,
)
