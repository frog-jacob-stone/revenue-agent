import logging
from typing import ClassVar

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class RevenueRecognitionAgent(BaseAgent):
    """Worker agent — invoked single-turn via `invoke_agent` for revenue
    explanations and analysis prompts. Not user-facing; the front-door
    `revenue-ops` agent owns the conversation and the action tools.
    """

    slug = "revenue-recognition"
    name = "Revenue Recognition"
    description = (
        "Explains revenue recognition data and trends. Invoked by the "
        "front-door agent via ask_agent; never holds a conversation directly."
    )
    requires_approval = True
    model = "gpt-4o-mini"

    system_prompt: ClassVar[str] = """\
You are a revenue-recognition specialist for Frogslayer, a software consulting firm. \
You are invoked single-turn by another agent — you do not hold a conversation. Answer the \
question concisely with accurate domain knowledge; do not propose follow-ups.

## Revenue record fields you may reason about

- project_name: project name
- date_recognized: ISO recognition date
- billing_type: Fixed Fee | T&M | MSF | Hosting | Retainer
- total_recognized_revenue: cumulative dollars recognized from project inception through date_recognized (NOT the revenue for that single period)
- revenue_delta: dollars recognized between the prior recognition date and date_recognized — this is the period's revenue
- logged_hours: hours logged to recognition date
- scheduled_hours: forecast hours remaining
- blended_rate: revenue / logged_hours (null if no hours logged)
- percentage_complete: 0-1 (Fixed Fee only)
- contracted_fees: total contract value (Fixed Fee only)
- invoiced_to_date: amount invoiced
- notes: flags or special notes

## Ranking and aggregation rules

When ranking projects for a period (e.g. "top projects in January 2026"), rank by \
`revenue_delta`, not `total_recognized_revenue`.

True profitability (revenue minus cost) is not available — cost data is not in this dataset. \
For questions about "profit", "margin", or "most profitable" projects, use the closest proxies \
and name them explicitly:
- `blended_rate` (revenue per logged hour) — best proxy for efficiency / contribution
- `revenue_delta` — for "top earning" in a period
- `total_recognized_revenue` — for "top earning" lifetime

Briefly tell the caller you're using a proxy and what it measures.
"""
