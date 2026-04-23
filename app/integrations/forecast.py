import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_BASE = "https://api.forecastapp.com"


def _headers(cfg: Settings) -> dict[str, str]:
    # Forecast uses the same Personal Access Token as Harvest
    return {
        "Authorization": f"Bearer {cfg.harvest_token}",
        "Forecast-Account-Id": cfg.forecast_account_id,
    }


async def _get_projects(cfg: Settings) -> list[dict[str, Any]]:
    """Return all Forecast projects (includes harvest_id mapping)."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{_BASE}/projects", headers=_headers(cfg))
        resp.raise_for_status()
        return resp.json().get("projects", [])


async def _get_future_scheduled_hours_raw(
    cfg: Settings, from_date: str
) -> dict[int, float]:
    """Return aggregate scheduled hours keyed by Forecast project ID."""
    url = f"{_BASE}/aggregate/future_scheduled_hours/{from_date}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(cfg))
        resp.raise_for_status()
        data = resp.json()

    allocations = data.get("future_scheduled_hours", [])
    totals: dict[int, float] = {}
    for entry in allocations:
        pid = entry["project_id"]
        totals[pid] = totals.get(pid, 0.0) + float(entry.get("allocation", 0))
    return totals


async def get_scheduled_hours_by_harvest_id(
    cfg: Settings, from_date: str
) -> dict[int, float]:
    """
    Return scheduled future hours keyed by Harvest project ID.
    Joins Forecast project list (which has harvest_id) with the aggregated hours.
    """
    import asyncio

    projects, hours_by_forecast_id = await asyncio.gather(
        _get_projects(cfg),
        _get_future_scheduled_hours_raw(cfg, from_date),
    )

    result: dict[int, float] = {}
    for proj in projects:
        harvest_id = proj.get("harvest_id")
        if harvest_id is None:
            continue
        forecast_id = proj["id"]
        if forecast_id in hours_by_forecast_id:
            result[int(harvest_id)] = hours_by_forecast_id[forecast_id]
    return result
