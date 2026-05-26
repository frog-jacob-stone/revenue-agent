from typing import Any

from app.services import revenue as revenue_service
from app.agents.tools.base import Done, ToolContext, ToolDefinition, ToolReturn


async def _get_revenue_data(
    ctx: ToolContext,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    **_: Any,
) -> ToolReturn:
    records = await revenue_service.get_revenue_data_slim(date_from=date_from, date_to=date_to)
    return Done({"records": records})


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
