from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.integrations.airtable import get_revenue_records


def calc_revenue(
    project: dict[str, Any], invoice_data: dict[str, Any]
) -> tuple[float, float | None, str]:
    """Compute recognized revenue for one project + month based on billing type.

    Returns (revenue, percent_complete, notes). `project` must include
    `_hours_logged` and `_forecast_hours` set by the caller.
    """
    billing_type = project.get("Billing Type", "Unknown")
    hours_logged: float = project.get("_hours_logged", 0.0)
    forecast_hours: float = project.get("_forecast_hours", 0.0)
    contracted_fees: float = float(project.get("Contracted Fees") or 0)
    total_projected = hours_logged + forecast_hours
    notes = ""
    percent_complete: float | None = None

    match billing_type:
        case "Fixed Fee":
            if total_projected > 0:
                percent_complete = round(hours_logged / total_projected, 4)
            else:
                percent_complete = 0.0
            revenue = round(contracted_fees * (percent_complete or 0), 2)
            billable_expenses = invoice_data.get("billable_expenses", 0.0)
            if billable_expenses:
                revenue = round(revenue + billable_expenses, 2)
                notes = f"Includes ${billable_expenses:,.2f} in billable expenses"
        case "T&M" | "MSF" | "Hosting":
            revenue = round(invoice_data.get("total_amount", 0.0), 2)
        case "Retainer":
            revenue = 0.0
            notes = "Retainers are not calculated — must do manually"
        case _:
            raise ValueError(f"Unexpected billing type: {billing_type!r}")

    return revenue, percent_complete, notes


_SLIM_FIELDS = {
    "Project Name": "project_name",
    "Date Recognized": "date_recognized",
    "Billing Type": "billing_type",
    "Total Recognized Revenue": "total_recognized_revenue",
    "Logged Hours": "logged_hours",
    "Scheduled Hours": "scheduled_hours",
    "Percentage Complete": "percentage_complete",
    "Contracted Fees": "contracted_fees",
    "Invoiced to Date": "invoiced_to_date",
    "Notes": "notes",
}


async def get_revenue_data_slim(
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Slim, chat-friendly revenue recognition rows with derived blended_rate.

    Defaults to last 12 months when both dates are omitted to keep context size
    manageable.
    """
    if not date_from and not date_to:
        date_from = (date.today().replace(day=1) - timedelta(days=365)).strftime("%Y-%m-%d")

    records = await get_revenue_records(settings, date_from=date_from, date_to=date_to)
    slim: list[dict[str, Any]] = []
    for r in records:
        row: dict[str, Any] = {}
        for airtable_key, slim_key in _SLIM_FIELDS.items():
            row[slim_key] = r.get(airtable_key)
        logged = row.get("logged_hours") or 0
        revenue = row.get("total_recognized_revenue") or 0
        row["blended_rate"] = round(revenue / logged, 2) if logged > 0 else None
        slim.append(row)
    return slim
