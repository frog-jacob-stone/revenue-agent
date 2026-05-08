"""rev_rec_monthly — Phase 2 of the LangGraph migration.

Six nodes, two interrupt gates, one conditional edge, one loop edge:

    [entry] → validate_and_sync ──ready──→ compute_entries → propose_write_entries
                       │                                           │
                       │                                  [interrupt_before
                       │                                   write_entries]
                       │                                           │
                       │                                           ▼
                       │                                    write_entries → END
                       │
                       └─incomplete──→ propose_configure
                                              │
                                       [interrupt_before
                                        apply_configure_or_loop]
                                              │
                                              ▼ (after approve)
                                       apply_configure_or_loop
                                              │
                                              └──loop──→ validate_and_sync

The loop replaces v1's `on_approve = create_workflow + resume` pattern, which
split one conceptual job across N workflow_ids. v2 keeps the whole iteration
inside a single workflow_id and audit trail.

Payload shapes for `configure_rev_rec_projects` and `write_rev_rec` match v1
exactly so the existing inbox renderer (ui/src/pages/Inbox/InboxList.tsx)
works unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Literal, NotRequired

from langgraph.graph import END, StateGraph

from app.config import settings
from app.integrations import airtable, forecast, harvest
from app.orchestrator.runner import GraphSpec
from app.orchestrator.state import BaseGraphState
from app.services import airtable_sync
from app.services.revenue import calc_revenue

logger = logging.getLogger(__name__)


REV_REC_KIND = "rev_rec_monthly"
REV_REC_AGENT_SLUG = "revenue-recognition"

ACTION_TYPE_CONFIGURE = "configure_rev_rec_projects"
ACTION_TYPE_WRITE = "write_rev_rec"


# ── State ────────────────────────────────────────────────────────────────────


class RevRecState(BaseGraphState, total=False):
    # From initial_state / trigger
    date_recognized: NotRequired[str]
    context: NotRequired[dict[str, Any]]

    # Set by validate_and_sync (overwritten on each iteration)
    validation_status: NotRequired[Literal["ready", "incomplete"]]
    incomplete_projects: NotRequired[list[dict[str, Any]]]
    projects: NotRequired[list[dict[str, Any]]]
    month_label: NotRequired[str]

    # Set by compute_entries
    entries: NotRequired[list[dict[str, Any]]]
    total_recognized: NotRequired[float]

    # Pushed in by the runner on approval resume
    executed_payload: NotRequired[dict[str, Any]]

    # Final
    result: NotRequired[dict[str, Any]]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _last_day_of_prev_month() -> str:
    return (date.today().replace(day=1) - timedelta(days=1)).isoformat()


def _round2(value: float) -> float:
    return round(value, 2)


# ── Nodes ────────────────────────────────────────────────────────────────────


async def validate_and_sync(state: RevRecState) -> RevRecState:
    """Sync Harvest → Airtable, fetch projects, classify each as ready/incomplete.

    Runs once per iteration. The loop edge from `apply_configure_or_loop` re-enters
    this node so the human can fix data externally and have it re-validated.
    """
    date_recognized = state.get("date_recognized") or _last_day_of_prev_month()
    month_label = date_recognized[:7]
    context = state.get("context") or {}

    # Duplicate guard: refuse to run twice for the same period.
    most_recent = await airtable.get_most_recent_revenue_entry(settings)
    if most_recent:
        last_date = (most_recent.get("Date Recognized") or "")[:10]
        if last_date >= date_recognized:
            raise ValueError(
                f"Revenue entries for {date_recognized} already exist "
                f"(most recent: {last_date}). Aborting to prevent duplicates."
            )

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
            "validation_status": "incomplete",
            "date_recognized": date_recognized,
            "month_label": month_label,
            "incomplete_projects": incomplete,
            "projects": [],
            "context": context,
        }

    return {
        "validation_status": "ready",
        "date_recognized": date_recognized,
        "month_label": month_label,
        "projects": projects,
        "incomplete_projects": [],
        "context": context,
    }


def route_after_validate(state: RevRecState) -> str:
    """Conditional edge: branch on `validation_status` set by validate_and_sync."""
    if state.get("validation_status") == "incomplete":
        return "propose_configure"
    return "compute_entries"


async def propose_configure(state: RevRecState) -> RevRecState:
    """Surface incomplete projects for human triage. Runner pauses on next node."""
    return {
        "_propose": {
            "action_type": ACTION_TYPE_CONFIGURE,
            "agent_slug": REV_REC_AGENT_SLUG,
            "risk_level": "low",
            "summary": (
                f"Configure {len(state.get('incomplete_projects') or [])} incomplete "
                f"project(s) before computing revenue for {state.get('month_label', '')}"
            ),
            "proposed_payload": {
                "date_recognized": state.get("date_recognized"),
                "incomplete_projects": state.get("incomplete_projects") or [],
                "context": state.get("context") or {},
            },
        }
    }


async def apply_configure_or_loop(state: RevRecState) -> RevRecState:
    """No-op POC: human approval of `propose_configure` is treated as a signal
    that they fixed the data externally. The deterministic edge from this node
    routes back to `validate_and_sync` for a fresh check.

    Future propose+execute upgrade path: when the agent learns to propose
    *specific* Airtable updates, this node will read `state["executed_payload"]`
    (the human-edited update list) and apply each update to Airtable before
    looping. The graph topology stays identical; only this body grows.
    """
    return {}


async def compute_entries(state: RevRecState) -> RevRecState:
    """Pull supporting data and compute revenue per project."""
    date_recognized = state["date_recognized"]
    projects = list(state.get("projects") or [])

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
    logger.info(
        "rev_rec_monthly v2: %d projects, $%.2f total for %s",
        len(entries), total_recognized, date_recognized,
    )
    return {
        "entries": entries,
        "total_recognized": total_recognized,
    }


async def propose_write_entries(state: RevRecState) -> RevRecState:
    """Surface the computed entries for human review. Runner pauses on next node."""
    return {
        "_propose": {
            "action_type": ACTION_TYPE_WRITE,
            "agent_slug": REV_REC_AGENT_SLUG,
            "risk_level": "low",
            "summary": (
                f"Write {len(state.get('entries') or [])} revenue entries "
                f"(${state.get('total_recognized', 0):.2f}) for "
                f"{state.get('date_recognized', '')}"
            ),
            "proposed_payload": {
                "date_recognized": state.get("date_recognized"),
                "total_recognized": state.get("total_recognized"),
                "entries": state.get("entries") or [],
            },
        }
    }


async def write_entries(state: RevRecState) -> RevRecState:
    """Write the (possibly human-edited) entries to Airtable."""
    payload = state.get("executed_payload") or {}
    raw_entries = payload.get("entries") or state.get("entries") or []
    clean_entries = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in raw_entries
    ]
    records = await airtable.create_revenue_records(settings, clean_entries)
    logger.info(
        "rev_rec_monthly v2: wrote %d revenue records to Airtable for %s",
        len(records), payload.get("date_recognized") or state.get("date_recognized"),
    )
    return {
        "result": {
            "records_created": len(records),
            "airtable_ids": [r["id"] for r in records],
        },
    }


# ── Graph factory ────────────────────────────────────────────────────────────


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(RevRecState)

    g.add_node("validate_and_sync", validate_and_sync)
    g.add_node("propose_configure", propose_configure)
    g.add_node("apply_configure_or_loop", apply_configure_or_loop)
    g.add_node("compute_entries", compute_entries)
    g.add_node("propose_write_entries", propose_write_entries)
    g.add_node("write_entries", write_entries)

    g.set_entry_point("validate_and_sync")
    g.add_conditional_edges(
        "validate_and_sync",
        route_after_validate,
        {
            "propose_configure": "propose_configure",
            "compute_entries": "compute_entries",
        },
    )
    g.add_edge("propose_configure", "apply_configure_or_loop")
    g.add_edge("apply_configure_or_loop", "validate_and_sync")  # loop back
    g.add_edge("compute_entries", "propose_write_entries")
    g.add_edge("propose_write_entries", "write_entries")
    g.add_edge("write_entries", END)

    return GraphSpec(
        graph=g,
        interrupt_before=("apply_configure_or_loop", "write_entries"),
    )
