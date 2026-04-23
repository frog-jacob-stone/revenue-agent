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
    return results


async def get_clients(cfg: Settings) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/clients", "clients", {"is_active": "true"})


async def get_active_projects(cfg: Settings) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/projects", "projects", {"is_active": "true"})


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
