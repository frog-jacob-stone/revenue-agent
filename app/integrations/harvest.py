import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_BASE = "https://api.harvestapp.com/v2"


def _headers(cfg: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.harvest_token}",
        "Harvest-Account-Id": cfg.harvest_account_id,
        "User-Agent": "RevenueAgent/1.0",
    }


async def _get_all(
    cfg: Settings,
    path: str,
    key: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Paginate through all pages of a Harvest API endpoint."""
    results: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            p = {**(params or {}), "page": page, "per_page": 100}
            resp = await client.get(f"{_BASE}{path}", headers=_headers(cfg), params=p)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get(key, []))
            if data.get("next_page") is None:
                break
            page += 1
            await asyncio.sleep(0.1) # be nice to the API or risk a 429 Too Many Requests. 100ms throttle between pages.
    return results


async def _get_one(cfg: Settings, path: str) -> dict[str, Any]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{_BASE}{path}", headers=_headers(cfg))
        resp.raise_for_status()
        return resp.json()


async def get_clients(cfg: Settings) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/clients", "clients", {"is_active": "true"})


async def get_client(cfg: Settings, client_id: int) -> dict[str, Any]:
    return await _get_one(cfg, f"/clients/{client_id}")


async def get_project(cfg: Settings, project_id: int) -> dict[str, Any]:
    return await _get_one(cfg, f"/projects/{project_id}")


async def get_active_projects(cfg: Settings) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/projects", "projects", {"is_active": "true"})


async def get_time_entries_for_period(
    cfg: Settings,
    project_id: int,
    from_date: str,
    to_date: str,
) -> list[dict[str, Any]]:
    """Return all billable time entries for a project within a date range."""
    return await _get_all(
        cfg,
        "/time_entries",
        "time_entries",
        {"project_id": project_id, "from": from_date, "to": to_date, "is_billed": "false"},
    )


async def list_invoices_for_client(cfg: Settings, client_id: int) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/invoices", "invoices", {"client_id": client_id})


async def create_invoice_draft(cfg: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Create a draft invoice in Harvest. Called on approval of generate_invoice action."""
    # v1 stub — enable when operationally ready
    raise NotImplementedError(
        "create_invoice_draft is not active in v1. Remove this guard when ready to enable."
    )


async def send_invoice(cfg: Settings, invoice_id: int) -> dict[str, Any]:
    # v1 stub — enable when operationally ready
    raise NotImplementedError(
        "send_invoice is not active in v1. Remove this guard when ready to enable."
    )


async def delete_invoice(cfg: Settings, invoice_id: int) -> dict[str, Any]:
    # v1 stub — enable when operationally ready
    raise NotImplementedError(
        "delete_invoice is not active in v1. Remove this guard when ready to enable."
    )


async def get_time_entries(cfg: Settings, project_id: int, to_date: str) -> float:
    """Return total hours logged for a project up to and including to_date."""
    entries = await _get_all(
        cfg,
        "/time_entries",
        "time_entries",
        {"project_id": project_id, "to": to_date},
    )
    return round(sum(float(e.get("hours", 0)) for e in entries), 4)


async def get_invoice_totals_by_project(
    cfg: Settings, to_date: str
) -> dict[int, dict[str, Any]]:
    """
    Return invoice totals keyed by Harvest project ID, for invoices issued on or
    before to_date. Each value: { "total_amount": float, "billable_expenses": float }
    """
    invoices = await _get_all(cfg, "/invoices", "invoices")
    totals: dict[int, dict[str, Any]] = {}

    for invoice in invoices:
        issue_date = invoice.get("issue_date", "")
        if not issue_date or issue_date > to_date:
            continue

        for item in invoice.get("line_items", []):
            project = item.get("project")
            if not project:
                continue
            pid = int(project["id"])
            if pid not in totals:
                totals[pid] = {"total_amount": 0.0, "billable_expenses": 0.0}
            amount = float(item.get("amount") or 0)
            totals[pid]["total_amount"] = round(totals[pid]["total_amount"] + amount, 2)
            if str(item.get("kind", "")).lower() == "expense":
                totals[pid]["billable_expenses"] = round(
                    totals[pid]["billable_expenses"] + amount, 2
                )

    return totals
