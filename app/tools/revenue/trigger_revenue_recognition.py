"""trigger_revenue_recognition — run the monthly rev-rec flow inline (ADR-0002).

One tool call performs validate-and-sync and (if no blockers) computes entries
and returns AwaitingApproval. The `write_rev_rec_entries` executor performs
the actual Airtable write after a human approves.

Returns one of:
  - Blocked: duplicate run for the period, no active projects, or one+
    projects incomplete (user must fix in Airtable and re-trigger)
  - AwaitingApproval: entries computed, ready for human review and write
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.integrations import airtable, forecast, harvest
from app.services import airtable_sync
from app.services.revenue import calc_revenue
from app.tools.base import (
    AwaitingApproval,
    Blocked,
    ProgressEmitter,
    ToolContext,
    ToolDefinition,
    ToolReturn,
)

logger = logging.getLogger(__name__)

TOOL_NAME = "trigger_revenue_recognition"
ACTION_TYPE_WRITE = "write_rev_rec"


def _emit(progress: ProgressEmitter | None, event: dict[str, Any]) -> None:
    if progress is not None:
        progress.emit(event)


def _last_day_of_prev_month() -> str:
    return (date.today().replace(day=1) - timedelta(days=1)).isoformat()


def _round2(value: float) -> float:
    return round(value, 2)


async def _validate_and_sync(
    date_recognized: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, str | None]:
    """Returns (projects, incomplete, duplicate_date).

    - duplicate_date is non-None iff Airtable already has entries on/after
      date_recognized; projects and incomplete are empty in that case.
    - incomplete is non-None (possibly empty) when no further work should
      run; the caller distinguishes "no projects" from "incomplete projects"
      by inspecting both lists.
    - projects is non-empty iff everything is ready to compute.
    """
    most_recent = await airtable.get_most_recent_revenue_entry(settings)
    if most_recent:
        last_date = (most_recent.get("Date Recognized") or "")[:10]
        if last_date >= date_recognized:
            return [], None, last_date

    await airtable_sync.run_sync(settings)

    projects = await airtable.get_projects(settings)
    if not projects:
        return [], [], None

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
        return [], incomplete, None
    return projects, None, None


async def _compute_entries(
    *, date_recognized: str, projects: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], float]:
    next_day = (date.fromisoformat(date_recognized) + timedelta(days=1)).isoformat()
    scheduled_hours_map, invoice_totals_map = await asyncio.gather(
        forecast.get_scheduled_hours_by_harvest_id(settings, next_day),
        harvest.get_invoice_totals_by_project(settings, date_recognized),
    )

    entries: list[dict[str, Any]] = []
    for project in projects:
        harvest_id = project.get("Harvest Id")
        hours_logged = 0.0
        if harvest_id:
            hours_logged = await harvest.get_time_entries(
                settings, int(harvest_id), date_recognized
            )

        project["_hours_logged"] = hours_logged
        project["_forecast_hours"] = float(
            scheduled_hours_map.get(int(harvest_id or 0), 0)
        )
        invoice_data = invoice_totals_map.get(int(harvest_id or 0), {})

        revenue, percent_complete, notes = calc_revenue(project, invoice_data)
        total_projected = hours_logged + project["_forecast_hours"]
        blended_rate = _round2(revenue / hours_logged) if hours_logged > 0 else None

        entries.append({
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
        })

    total_recognized = _round2(sum(e["Total Recognized Revenue"] for e in entries))
    return entries, total_recognized


async def _trigger_revenue_recognition(
    ctx: ToolContext,
    *,
    date_recognized: str | None = None,
    **_: Any,
) -> ToolReturn:
    resolved_date = date_recognized or _last_day_of_prev_month()
    month_label = resolved_date[:7]
    progress = ctx.progress

    # Step 1: validate + sync
    _emit(progress, {
        "type": "tool_step_started", "tool": TOOL_NAME, "step": "validate_and_sync",
    })
    projects, incomplete, duplicate_date = await _validate_and_sync(resolved_date)
    _emit(progress, {
        "type": "tool_step_completed", "tool": TOOL_NAME, "step": "validate_and_sync",
    })

    if duplicate_date:
        return Blocked(
            reason=(
                f"Revenue entries for {resolved_date} already exist "
                f"(most recent: {duplicate_date})."
            ),
            hint={"most_recent_date": duplicate_date, "date_recognized": resolved_date},
        )

    if incomplete is not None and not projects:
        if incomplete:
            return Blocked(
                reason=(
                    f"{len(incomplete)} project(s) need configuration before "
                    f"computing revenue for {month_label}."
                ),
                hint={
                    "incomplete_projects": incomplete,
                    "date_recognized": resolved_date,
                    "month_label": month_label,
                },
            )
        return Blocked(
            reason="No active projects found in Airtable.",
            hint={"date_recognized": resolved_date},
        )

    # Step 2: compute
    _emit(progress, {
        "type": "tool_step_started", "tool": TOOL_NAME, "step": "compute_entries",
    })
    entries, total_recognized = await _compute_entries(
        date_recognized=resolved_date, projects=projects,
    )
    _emit(progress, {
        "type": "tool_step_completed", "tool": TOOL_NAME, "step": "compute_entries",
    })

    return AwaitingApproval(
        executor="write_rev_rec_entries",
        payload={
            "date_recognized": resolved_date,
            "total_recognized": total_recognized,
            "entries": entries,
        },
        summary=(
            f"Write {len(entries)} revenue entries (${total_recognized:.2f}) "
            f"for {resolved_date}"
        ),
        action_type=ACTION_TYPE_WRITE,
        risk_level="low",
    )


TRIGGER_REVENUE_RECOGNITION = ToolDefinition(
    name="trigger_revenue_recognition",
    description=(
        "Run the monthly revenue recognition flow. Validates that all projects "
        "have required configuration, computes revenue entries from Harvest, "
        "Forecast, and Airtable data, and proposes the entries for human "
        "approval before any Airtable write. Returns awaiting_approval (entries "
        "ready to review in the inbox), or blocked (incomplete projects, "
        "duplicate run, or no projects)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "date_recognized": {
                "type": "string",
                "description": (
                    "Recognition date ISO YYYY-MM-DD. Defaults to the last day "
                    "of the previous month."
                ),
            },
        },
    },
    execute=_trigger_revenue_recognition,
)
