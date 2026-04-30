"""Revenue recognition chain — pattern #1 (supervised_automation).

  1. tool_call  — Sync Harvest → Airtable, fetch projects, validate completeness
                  (writes status: "incomplete" or "ready" into result)
  2. checkpoint — Configure incomplete projects (skipped if validation passed)
                  on_approve = requeue a fresh validation cycle so the human
                  can keep iterating until the data is clean
  3. tool_call  — Compute revenue entries (skipped if validation incomplete)
  4. execution  — Write entries to Airtable (skipped if validation incomplete)

Two divergent endings live in one chain via `skip_if` predicates. The
`configure_rev_rec_projects` and `write_rev_rec` action_types are preserved
so existing inbox UI rendering works unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from app.config import settings
from app.integrations import airtable, forecast, harvest
from app.models.workflows import WorkflowPattern
from app.orchestrator.chain import Chain, register_chain
from app.orchestrator.state import StepContext
from app.orchestrator.steps import (
    CheckpointStep,
    ExecutionStep,
    ToolCallStep,
)
from app.services import airtable_sync

logger = logging.getLogger(__name__)

REV_REC_KIND = "rev_rec_monthly"
REV_REC_AGENT_SLUG = "revenue-recognition"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _last_day_of_prev_month() -> str:
    return (date.today().replace(day=1) - timedelta(days=1)).isoformat()


def _round2(value: float) -> float:
    return round(value, 2)


def _round4(value: float) -> float:
    return round(value, 4)


def _calc_revenue(project: dict[str, Any], invoice_data: dict[str, Any]) -> tuple[float, float | None, str]:
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


# -----------------------------------------------------------------------------
# Step handlers
# -----------------------------------------------------------------------------

async def _sync_and_validate(ctx: StepContext) -> dict[str, Any]:
    """Sync Harvest data, fetch projects, and check that each has the fields
    needed for downstream calculation. Result drives the chain's branching."""
    payload = await ctx.conn.fetchval(
        "SELECT trigger_payload FROM workflows WHERE id = $1",
        ctx.workflow_id,
    )
    context = payload or {}
    date_recognized = context.get("date_recognized") or _last_day_of_prev_month()
    month_label = date_recognized[:7]

    # Duplicate guard: refuse to run twice for the same period.
    most_recent = await airtable.get_most_recent_revenue_entry(settings)
    if most_recent:
        last_date = (most_recent.get("Date Recognized") or "")[:10]
        if last_date >= date_recognized:
            raise ValueError(
                f"Revenue entries for {date_recognized} already exist "
                f"(most recent: {last_date}). Aborting to prevent duplicates."
            )

    # Sync Harvest -> Airtable so projects/clients exist before validation.
    await airtable_sync.run_sync(settings)

    projects = await airtable.get_projects(settings)
    if not projects:
        raise ValueError("No active projects found in Airtable.")

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
        return {
            "status": "incomplete",
            "date_recognized": date_recognized,
            "month_label": month_label,
            "incomplete_projects": incomplete,
            "context": context,
        }

    # Cache project rows on the result so the compute step doesn't re-fetch.
    return {
        "status": "ready",
        "date_recognized": date_recognized,
        "month_label": month_label,
        "projects": projects,
        "context": context,
    }


async def _compute_entries(ctx: StepContext) -> dict[str, Any]:
    """Heavy work: pull supporting data and compute revenue per project."""
    val = ctx.state.latest_for_step(0)
    payload = val.result if val and val.result else {}
    date_recognized = payload["date_recognized"]
    projects = payload["projects"]

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
        project["_forecast_hours"] = float(scheduled_hours_map.get(int(harvest_id or 0), 0))
        invoice_data = invoice_totals_map.get(int(harvest_id or 0), {})

        revenue, percent_complete, notes = _calc_revenue(project, invoice_data)
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
    logger.info(
        "rev_rec_monthly: %d projects, $%.2f total for %s",
        len(entries), total_recognized, date_recognized,
    )
    return {
        "date_recognized": date_recognized,
        "entries": entries,
        "total_recognized": total_recognized,
    }


async def _propose_configure(ctx: StepContext) -> dict[str, Any]:
    """Surface incomplete projects for human triage in the inbox."""
    val = ctx.state.latest_for_step(0)
    r = (val.result or {}) if val else {}
    return {
        "date_recognized": r.get("date_recognized"),
        "incomplete_projects": r.get("incomplete_projects", []),
        "context": r.get("context", {}),
    }


async def _on_configure_approved(ctx: StepContext) -> dict[str, Any]:
    """User has fixed projects in Airtable; queue a fresh validation cycle."""
    from app.orchestrator import orchestrator  # avoid circular import at module load

    val = ctx.state.latest_for_step(0)
    prior_context = ((val.result or {}) if val else {}).get("context", {}) or {}
    new_workflow_id = await orchestrator.create_workflow(
        REV_REC_KIND,
        context=prior_context,
        initiated_by="rev-rec-loop",
        trigger_source="manual",
    )
    asyncio.create_task(orchestrator.resume(new_workflow_id))
    logger.info("rev_rec_monthly: requeued workflow=%s after configure approval", new_workflow_id)
    return {"requeued_workflow_id": str(new_workflow_id)}


async def _propose_write(ctx: StepContext) -> dict[str, Any]:
    """Surface the computed entries for human review."""
    val = ctx.state.latest_for_step(2)
    return val.result if val and val.result else {}


async def _write_entries(ctx: StepContext) -> dict[str, Any]:
    """Write the (possibly human-edited) entries to Airtable."""
    payload = ctx.executed_payload or {}
    raw_entries = payload.get("entries", [])
    # Strip internal _ fields before writing.
    clean_entries = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in raw_entries
    ]
    records = await airtable.create_revenue_records(settings, clean_entries)
    logger.info(
        "rev_rec_monthly: wrote %d revenue records to Airtable for %s",
        len(records), payload.get("date_recognized"),
    )
    return {
        "records_created": len(records),
        "airtable_ids": [r["id"] for r in records],
    }


# -----------------------------------------------------------------------------
# Skip predicates
# -----------------------------------------------------------------------------

def _validation_passed(ctx: StepContext) -> bool:
    val = ctx.state.latest_for_step(0)
    return bool(val and val.result and val.result.get("status") == "ready")


def _validation_failed(ctx: StepContext) -> bool:
    val = ctx.state.latest_for_step(0)
    return bool(val and val.result and val.result.get("status") == "incomplete")


# -----------------------------------------------------------------------------
# Chain registration
# -----------------------------------------------------------------------------

REV_REC_CHAIN = Chain(
    kind=REV_REC_KIND,
    pattern=WorkflowPattern.supervised_automation,
    agent_slug=REV_REC_AGENT_SLUG,
    steps=(
        ToolCallStep("Sync Harvest → Airtable and validate projects", _sync_and_validate),
        CheckpointStep(
            "Configure incomplete projects",
            propose_handler=_propose_configure,
            on_approve=_on_configure_approved,
            skip_if=_validation_passed,
            action_type="configure_rev_rec_projects",
            risk_level="low",
        ),
        ToolCallStep(
            "Compute revenue entries",
            _compute_entries,
            skip_if=_validation_failed,
        ),
        ExecutionStep(
            "Write revenue recognition entries to Airtable",
            executor=_write_entries,
            propose_handler=_propose_write,
            skip_if=_validation_failed,
            action_type="write_rev_rec",
            risk_level="low",
        ),
    ),
)


def register() -> None:
    from app.orchestrator.chain import has_chain

    if has_chain(REV_REC_KIND):
        return
    register_chain(REV_REC_CHAIN)
