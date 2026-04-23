import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_BASE = "https://api.airtable.com/v0"


def _headers(cfg: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.airtable_api_key}",
        "Content-Type": "application/json",
    }


async def _get_all(cfg: Settings, table_id: str, params: dict | None = None) -> list[dict[str, Any]]:
    """Paginate through all records in an Airtable table."""
    url = f"{_BASE}/{cfg.airtable_base_id}/{table_id}"
    records: list[dict] = []
    offset: str | None = None
    async with httpx.AsyncClient() as client:
        while True:
            p = {**(params or {}), "pageSize": 100}
            if offset:
                p["offset"] = offset
            resp = await client.get(url, headers=_headers(cfg), params=p)
            resp.raise_for_status()
            data = resp.json()
            records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                break
    return records


async def get_projects(cfg: Settings) -> list[dict[str, Any]]:
    """Return non-archived projects from Airtable, flattened to include airtableId."""
    records = await _get_all(cfg, cfg.airtable_projects_table_id, {"filterByFormula": "NOT({Archive})"})
    return [{"airtableId": r["id"], **r["fields"]} for r in records]


async def get_most_recent_revenue_entry(cfg: Settings) -> dict[str, Any] | None:
    """Return the most recently recognized revenue entry, or None if table is empty."""
    url = f"{_BASE}/{cfg.airtable_base_id}/{cfg.airtable_revenue_table_id}"
    params = {
        "sort[0][field]": "Date Recognized",
        "sort[0][direction]": "desc",
        "pageSize": 1,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers(cfg), params=params)
        resp.raise_for_status()
        records = resp.json().get("records", [])
    if not records:
        return None
    r = records[0]
    return {"airtableId": r["id"], **r["fields"]}


async def upsert_records(
    cfg: Settings,
    table_id: str,
    records: list[dict[str, Any]],
    merge_on: list[str],
) -> list[dict[str, Any]]:
    """Upsert records into an Airtable table in batches of 10."""
    url = f"{_BASE}/{cfg.airtable_base_id}/{table_id}"
    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(records), 10):
            batch = records[i : i + 10]
            body = {
                "records": [{"fields": r} for r in batch],
                "performUpsert": {"fieldsToMergeOn": merge_on},
            }
            resp = await client.patch(url, headers=_headers(cfg), json=body)
            resp.raise_for_status()
            results.extend(resp.json().get("records", []))
    return results


async def create_revenue_records(
    cfg: Settings,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create revenue recognition records in Airtable in batches of 10."""
    url = f"{_BASE}/{cfg.airtable_base_id}/{cfg.airtable_revenue_table_id}"
    results: list[dict] = []
    async with httpx.AsyncClient() as client:
        for i in range(0, len(entries), 10):
            batch = entries[i : i + 10]
            body = {"records": [{"fields": e} for e in batch]}
            resp = await client.post(url, headers=_headers(cfg), json=body)
            resp.raise_for_status()
            results.extend(resp.json().get("records", []))
    return results
