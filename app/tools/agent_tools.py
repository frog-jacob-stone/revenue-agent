from datetime import date
from typing import Any

from app.tools.base import ToolContext, ToolDefinition


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
