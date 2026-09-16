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

One record is one project for one month.

- project_name: project name
- period_month: the month recognized, as an ISO first-of-month date
- revenue_type: fixed_fee | time_and_materials | msf | hosting | retainer
- recognized_amount: dollars recognized *in that month*. This is the period's revenue and \
it is what almost every question is about
- cumulative_recognized: dollars recognized from project inception through that month
- logged_hours: hours logged in that month
- scheduled_hours: forecast hours remaining
- percent_complete: 0-1 (fixed_fee only)
- contracted_fees: total contract value (fixed_fee only)
- invoiced_to_date: amount invoiced
- notes: flags or special notes

## Ranking and aggregation rules

Rank by `recognized_amount` for a period, `cumulative_recognized` for lifetime. Summing \
`cumulative_recognized` across months double-counts — it already contains every earlier \
month.

Revenue per hour is not a field, because the right way to compute it depends on what you \
are showing. For one project-month it is `recognized_amount / logged_hours`. Across several \
rows you must **blend** it — sum the revenue, sum the hours, then divide — never average the \
per-row rates, which would weight a light month the same as a heavy one.

True profitability (revenue minus cost) is not available — cost data is not in this dataset. \
For questions about "profit", "margin", or "most profitable" projects, use the closest proxies \
and name them explicitly:
- revenue per logged hour (blended as above) — best proxy for efficiency / contribution
- `recognized_amount` — for "top earning" in a period
- `cumulative_recognized` — for "top earning" lifetime

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
