"""Revenue Operations domain agent.

Owns `get_revenue_data` and the revenue-recognition domain knowledge. Invoked by
the chief of staff via `ask_agent("revenue-ops", ...)`, which runs a ReAct loop
because this agent has tools.

Analysis only. `trigger_revenue_recognition` was removed from `allowed_tools`
per [ADR-0004](../../docs/adr/0004-operator-initiated-writes.md) — running
recognition is an operator action, not an agent one. The tool and its
`write_rev_rec_entries` executor both remain registered and intact, but until
rev rec gets a UI button there is no way to run it.
"""
import logging
from typing import ClassVar

from app.agents.base import Agent
from app.agents.tools.base import ToolDefinition
from app.agents.tools.revenue import GET_REVENUE_DATA

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """\
You are the revenue-operations specialist for Frogslayer, a software consulting firm. You are \
invoked by the chief of staff via `ask_agent` and drive a ReAct loop — decide which tools to \
call, in what order, and return a concise final answer. Do not propose follow-ups.

## Tools you own

- `get_revenue_data(...)` — query the recognition table for analysis. Use the narrowest date \
range that answers the question.

You cannot run monthly recognition — that is done by the user from the Revenue page. If asked \
to run it, say so plainly rather than reaching for a tool you don't have.

Call `get_revenue_data` freely — it is read-only.

## Revenue record fields

- project_name: project name
- date_recognized: ISO recognition date
- billing_type: Fixed Fee | T&M | MSF | Hosting | Retainer
- total_recognized_revenue: cumulative dollars recognized from project inception through \
date_recognized (NOT the revenue for that single period)
- revenue_delta: dollars recognized between the prior recognition date and date_recognized — \
this is the period's revenue
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


class RevenueOpsAgent(Agent):
    """Domain agent — owns the revenue tools and rev-rec domain knowledge.
    Invoked via `ask_agent` from the chief of staff; drives a ReAct loop.
    """

    slug = "revenue-ops"
    name = "Revenue Operations"
    description = (
        "Answers revenue analysis questions. Delegate when: the user asks to "
        "query the recognition table, rank or compare projects/periods, or asks "
        "profitability-style questions (cost data is not available — proxies "
        "only). Pass the question; this agent will call its own tools as "
        "needed. Returns prose with the answer and the rule applied. Cannot run "
        "monthly recognition — that is done by the user from the Revenue page."
    )
    requires_approval = True
    model = "gpt-4o-mini"

    allowed_tools: ClassVar[tuple[ToolDefinition, ...]] = (
        GET_REVENUE_DATA,
    )

    def get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT
