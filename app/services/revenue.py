from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.integrations.airtable import get_revenue_records

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
