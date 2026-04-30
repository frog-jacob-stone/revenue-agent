from datetime import date
from typing import Any

from app.services import revenue as revenue_service
from app.tools.base import ToolContext, ToolDefinition


async def _get_revenue_data(
    ctx: ToolContext,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    return await revenue_service.get_revenue_data_slim(date_from=date_from, date_to=date_to)


GET_REVENUE_DATA = ToolDefinition(
    name="get_revenue_data",
    description=(
        "Fetch revenue recognition records for a date range. "
        "Choose the narrowest range that answers the question — "
        "last quarter for snapshots, last 12 months for trends, omit dates for all-time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "date_from": {
                "type": "string",
                "description": "Start date ISO YYYY-MM-DD (inclusive), optional.",
            },
            "date_to": {
                "type": "string",
                "description": "End date ISO YYYY-MM-DD (inclusive), optional.",
            },
        },
    },
    execute=_get_revenue_data,
)


async def _trigger_revenue_recognition(
    ctx: ToolContext,
    *,
    date_recognized: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    from app.orchestrator import orchestrator
    from app.orchestrator.chains.rev_rec import REV_REC_KIND

    workflow_id = await orchestrator.create_workflow(
        REV_REC_KIND,
        context={"date_recognized": date_recognized or date.today().isoformat()},
        initiated_by="chat",
        trigger_source="manual",
    )
    await orchestrator.resume(workflow_id)
    return {"workflow_id": str(workflow_id)}


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
