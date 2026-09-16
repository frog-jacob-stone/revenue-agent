from datetime import date, timedelta
from decimal import Decimal
from typing import Any, NamedTuple

from app.db import get_pool
from app.services import revenue_ledger

#: What a retainer's computed figure means: nothing yet. Surfaced on the draft
#: entry so the reason a zero needs a human is visible next to the zero, and
#: matched by `revenue_run.finalize_run`, which refuses to close a month while
#: an undecided retainer is still sitting at zero.
RETAINER_NOTE = "Retainers are not calculated — enter the amount manually"


class RevenueResult(NamedTuple):
    """One project's recognition for one period.

    `amount` is **cumulative-to-date**, not the period's revenue. Every formula
    below naturally produces a cumulative figure — percent complete against the
    whole contract, or Harvest's invoiced-to-date — and the caller turns it into
    a period amount by subtracting what has already been recognized. Doing that
    subtraction here would mean this function needed the ledger, which is the
    one thing that keeps it a pure function.
    """

    amount: float
    percent_complete: float | None
    notes: str


def calc_revenue(
    *,
    revenue_type: str,
    hours_logged: float = 0.0,
    forecast_hours: float = 0.0,
    contracted_fees: float | None = None,
    invoiced_to_date: float = 0.0,
    billable_expenses: float = 0.0,
) -> RevenueResult:
    """Cumulative recognized revenue for one project, by recognition method.

    Keyword-only, and typed. This took an Airtable record and an invoice dict
    before, with the caller stashing `_hours_logged` and `_forecast_hours` into
    the record under underscore keys on the way in — which meant the two most
    load-bearing inputs were invisible in the signature and unchecked at the
    boundary.

    The math is unchanged from that version.
    """
    total_projected = hours_logged + forecast_hours
    fees = float(contracted_fees or 0)
    notes = ""
    percent_complete: float | None = None

    match revenue_type:
        case "fixed_fee":
            # Percent complete is hours-based: what fraction of the projected
            # effort has been spent. Zero projected hours is 0%, not a division
            # error — a project nobody has worked or scheduled has earned
            # nothing, which is the honest answer rather than an exception.
            percent_complete = (
                round(hours_logged / total_projected, 4) if total_projected > 0 else 0.0
            )
            revenue = round(fees * percent_complete, 2)
            # Expenses are passed through at cost and are not part of the
            # contract value, so they sit on top of the earned fee rather than
            # being scaled by completion.
            if billable_expenses:
                revenue = round(revenue + billable_expenses, 2)
                notes = f"Includes ${billable_expenses:,.2f} in billable expenses"
        case "time_and_materials" | "msf" | "hosting":
            # Recognized as invoiced. Harvest's invoiced-to-date is already
            # cumulative, so it is the cumulative figure directly.
            revenue = round(invoiced_to_date, 2)
        case "retainer":
            revenue = 0.0
            notes = RETAINER_NOTE
        case _:
            raise ValueError(f"Unexpected revenue type: {revenue_type!r}")

    return RevenueResult(revenue, percent_complete, notes)


#: Ledger column -> the key an LLM sees. The renames are the point, not
#: cosmetics: the Airtable vocabulary invited a specific mistake.
#:
#: `Total Recognized Revenue` was cumulative-to-date, and `Revenue Delta` was
#: the period's actual revenue — so the field that sounded like "the revenue"
#: was the one you almost never wanted, and the prompt had to spend a paragraph
#: warning about it. `recognized_amount` and `cumulative_recognized` say which
#: is which without being told.
_SLIM_FIELDS = {
    "harvest_project_name": "project_name",
    "period_month": "period_month",
    "revenue_type": "revenue_type",
    "recognized_amount": "recognized_amount",
    "cumulative_recognized": "cumulative_recognized",
    "logged_hours": "logged_hours",
    "scheduled_hours": "scheduled_hours",
    "percent_complete": "percent_complete",
    "contracted_fees": "contracted_fees",
    "invoiced_to_date": "invoiced_to_date",
    "notes": "notes",
}


def _iso_month(value: str | None) -> date | None:
    """An ISO date from the caller -> the first of its month.

    Periods are first-of-month, so a mid-month bound would silently exclude the
    month the caller meant. An unparseable date is ignored rather than raised
    on: the caller is an LLM, and an unbounded range is a far better failure
    than a tool error it will retry three ways.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).replace(day=1)
    except ValueError:
        return None


async def get_revenue_data_slim(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Slim, chat-friendly ledger rows.

    Defaults to the last 12 months when both dates are omitted, to keep context
    size manageable.

    Floats, not `Decimal`: this is the one consumer where exactness does not
    matter and serializability does — the rows go into a JSON tool result for an
    LLM to reason over, not into anything that reconciles.

    No `blended_rate`. The old one divided *cumulative* revenue by a *single*
    period's hours, so it climbed every month regardless of performance and
    could not be trended. The two numbers it was built from are both here, and
    revenue per hour is `recognized_amount / logged_hours` for a single row —
    across several rows it has to be blended (sum of revenue / sum of hours),
    which is a thing the caller decides, not a column.
    """
    if not date_from and not date_to:
        date_from = (date.today().replace(day=1) - timedelta(days=365)).isoformat()

    rows = await revenue_ledger.list_entries(
        await get_pool(),
        date_from=_iso_month(date_from),
        date_to=_iso_month(date_to),
    )

    slim: list[dict[str, Any]] = []
    for row in rows:
        out: dict[str, Any] = {}
        for column, key in _SLIM_FIELDS.items():
            value = row.get(column)
            if isinstance(value, Decimal):
                value = float(value)
            elif isinstance(value, date):
                value = value.isoformat()
            out[key] = value
        slim.append(out)
    return slim
