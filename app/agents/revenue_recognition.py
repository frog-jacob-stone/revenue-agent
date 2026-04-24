import asyncio
import logging
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from app.agents.base import BaseAgent
from app.config import settings
from app.integrations import airtable, forecast, harvest
from app.services import airtable_sync

logger = logging.getLogger(__name__)


def _last_day_of_prev_month() -> str:
    return (date.today().replace(day=1) - timedelta(days=1)).isoformat()


def _round2(value: float) -> float:
    return round(value, 2)


def _round4(value: float) -> float:
    return round(value, 4)


def _calc_revenue(project: dict[str, Any], invoice_data: dict[str, Any]) -> tuple[float, float | None, str]:
    """
    Run billing-type-specific revenue recognition.
    Returns (recognized_revenue, percent_complete, notes).
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
                percent_complete = _round4(hours_logged / total_projected)
            else:
                percent_complete = 0.0
            revenue = _round2(contracted_fees * (percent_complete or 0))
            billable_expenses = invoice_data.get("billable_expenses", 0.0)
            if billable_expenses:
                revenue = _round2(revenue + billable_expenses)
                notes = f"Includes ${billable_expenses:,.2f} in billable expenses"
        case "T&M" | "MSF" | "Hosting":
            revenue = _round2(invoice_data.get("total_amount", 0.0))
        case "Retainer":
            revenue = 0.0
            notes = "Retainers are not calculated — must do manually"
        case _:
            raise ValueError(f"Unexpected billing type: {billing_type!r}")

    return revenue, percent_complete, notes


class RevenueRecognitionAgent(BaseAgent):
    slug = "revenue-recognition"
    name = "Revenue Recognition"

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        date_recognized = context.get("date_recognized") or _last_day_of_prev_month()
        month_label = date_recognized[:7]  # e.g. "2026-03"

        # 1. Sync Harvest → Airtable (ensures all clients + projects exist)
        await airtable_sync.run_sync(settings)

        # 2. Duplicate guard — abort if revenue entries already exist for this period
        most_recent = await airtable.get_most_recent_revenue_entry(settings)
        if most_recent:
            last_date = (most_recent.get("Date Recognized") or "")[:10]
            if last_date >= date_recognized:
                raise ValueError(
                    f"Revenue entries for {date_recognized} already exist "
                    f"(most recent: {last_date}). Aborting to prevent duplicates."
                )

        # 3. Fetch all projects from Airtable
        projects = await airtable.get_projects(settings)
        if not projects:
            raise ValueError("No active projects found in Airtable.")

        # 4. Data integrity check
        incomplete: list[dict[str, Any]] = []
        for p in projects:
            missing: list[str] = []
            if not p.get("Billing Type"):
                missing.append("Billing Type")
            if not p.get("Client Id"):
                missing.append("Client Id")
            if p.get("Billing Type") == "Fixed Fee" and not p.get("Contracted Fees"):
                missing.append("Contracted Fees (required for Fixed Fee)")
            if missing:
                incomplete.append({
                    "project_name": p.get("Project Name", "Unnamed"),
                    "harvest_id": p.get("Harvest Id"),
                    "airtable_id": p.get("airtableId"),
                    "missing_fields": missing,
                })

        if incomplete:
            count = len(incomplete)
            logger.warning(
                "revenue-recognition: %d project(s) missing required fields", count
            )
            return [
                {
                    "action_type": "configure_rev_rec_projects",
                    "summary": (
                        f"{count} project{'s' if count > 1 else ''} need configuration "
                        f"in Airtable before revenue recognition can run for {month_label}"
                    ),
                    "proposed_payload": {
                        "date_recognized": date_recognized,
                        "incomplete_projects": incomplete,
                        "context": context,
                    },
                    "reasoning": (
                        "Revenue recognition requires each project to have a Billing Type, "
                        "Client Id, and (for Fixed Fee) Contracted Fees set in Airtable. "
                        "Update the listed projects in Airtable, then approve this action "
                        "to automatically re-trigger recognition."
                    ),
                    "risk_level": "low",
                }
            ]
        
        # 5. Fetch supporting data in parallel
        # date_recognized + 1 day as the Forecast "from" date (matches n8n behavior)
        rec_date = date.fromisoformat(date_recognized)
        next_day = (rec_date + timedelta(days=1)).isoformat()

        scheduled_hours_map, invoice_totals_map = await asyncio.gather(
            forecast.get_scheduled_hours_by_harvest_id(settings, next_day),
            harvest.get_invoice_totals_by_project(settings, date_recognized),
        )

        # 6. Per-project: fetch time entries and compute recognition
        async def process_project(project: dict[str, Any]) -> dict[str, Any]:
            harvest_id = project.get("Harvest Id")
            hours_logged = 0.0
            if harvest_id:
                hours_logged = await harvest.get_time_entries(
                    settings, int(harvest_id), date_recognized
                )

            project["_hours_logged"] = hours_logged
            project["_forecast_hours"] = float(scheduled_hours_map.get(int(harvest_id or 0), 0))
            invoice_data = invoice_totals_map.get(int(harvest_id or 0), {})

            revenue, percent_complete, notes = _calc_revenue(project, invoice_data)
            total_projected = hours_logged + project["_forecast_hours"]
            blended_rate = _round2(revenue / hours_logged) if hours_logged > 0 else None

            return {
                "Harvest Id": harvest_id,
                "Project Name": project.get("Project Name", "Unnamed"),
                "Date Recognized": date_recognized,
                "Total Recognized Revenue": revenue,
                "Percentage Complete": percent_complete,
                "Scheduled Hours": project["_forecast_hours"],
                "Logged Hours": hours_logged,
                "Contracted Fees": project.get("Contracted Fees"),
                "Billing Type": project.get("Billing Type"),
                "Total Projected Hours": _round2(total_projected),
                "Notes": notes,
                "Invoiced to Date": invoice_data.get("total_amount", 0.0),
                "Project Id": [project["airtableId"]] if project.get("airtableId") else [],
                "_blended_rate": blended_rate,
            }
        
        entries = []
        for p in projects:
            entries.append(await process_project(p))

        total_recognized = _round2(sum(e["Total Recognized Revenue"] for e in entries))
        logger.info(
            "revenue-recognition: %d projects, $%.2f total for %s",
            len(entries),
            total_recognized,
            date_recognized,
        )

        return [
            {
                "action_type": "write_rev_rec",
                "summary": (
                    f"Revenue recognition for {month_label} — "
                    f"{len(entries)} projects, ${total_recognized:,.2f} total recognized"
                ),
                "proposed_payload": {
                    "date_recognized": date_recognized,
                    "entries": list(entries),
                    "total_recognized": total_recognized,
                },
                "reasoning": (
                    "End-of-month revenue recognition across all active billable projects. "
                    "Fixed Fee projects use % complete (hours-based); T&M/MSF/Hosting use "
                    "invoiced amounts. Approve to write all records to Airtable at once."
                ),
                "risk_level": "low",
            }
        ]
