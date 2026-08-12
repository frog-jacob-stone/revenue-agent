import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_BASE = "https://api.airtable.com/v0"

# Matches app/integrations/harvest.py. httpx does default to 5s, so the bare
# `AsyncClient()` these calls used was not unbounded — but 5s is per-read, and
# `_get_all` wraps an unbounded pagination loop, so total wall time was. With one
# replica every hung request holds an event-loop slot.
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# A stop on the pagination loop. 100 records a page, so this is 10k records —
# far past any table here, and the difference between a bug that returns wrong
# data and one that never returns at all.
_MAX_PAGES = 100


def _headers(cfg: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {cfg.airtable_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }


async def _get_all(
    cfg: Settings,
    table_id: str,
    params: dict | None = None,
) -> list[dict[str, Any]]:
    """Paginate through all records in an Airtable table."""
    url = f"{_BASE}/{cfg.airtable_base_id}/{table_id}"
    records: list[dict] = []
    offset: str | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for _ in range(_MAX_PAGES):
            p = {**(params or {}), "pageSize": 100}
            if offset:
                p["offset"] = offset
            resp = await client.get(url, headers=_headers(cfg), params=p)
            resp.raise_for_status()
            data = resp.json()
            records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                return records
    raise RuntimeError(
        f"Airtable pagination exceeded {_MAX_PAGES} pages for table {table_id} — "
        "refusing to keep looping. Either the table is far larger than expected "
        "or the API is returning a repeating offset."
    )


async def get_projects(cfg: Settings) -> list[dict[str, Any]]:
    """Return non-archived projects from Airtable, flattened to include airtableId."""
    records = await _get_all(
        cfg, cfg.airtable_projects_table_id, {"filterByFormula": "NOT({Archive})"}
    )
    return [{"airtableId": r["id"], **r["fields"]} for r in records]


async def get_revenue_records(
    cfg: Settings,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return revenue recognition records, optionally filtered by date range."""
    parts: list[str] = []
    if date_from:
        parts.append(f"DATESTR({{Date Recognized}}) >= '{date_from}'")
    if date_to:
        parts.append(f"DATESTR({{Date Recognized}}) <= '{date_to}'")

    params: dict[str, Any] = {
        "sort[0][field]": "Date Recognized",
        "sort[0][direction]": "desc",
    }
    if len(parts) == 2:
        params["filterByFormula"] = f"AND({parts[0]}, {parts[1]})"
    elif len(parts) == 1:
        params["filterByFormula"] = parts[0]

    records = await _get_all(cfg, cfg.airtable_revenue_table_id, params)
    return [{"airtableId": r["id"], **r["fields"]} for r in records]


async def get_most_recent_revenue_entry(cfg: Settings) -> dict[str, Any] | None:
    """Return the most recently recognized revenue entry, or None if table is empty."""
    url = f"{_BASE}/{cfg.airtable_base_id}/{cfg.airtable_revenue_table_id}"
    params = {
        "sort[0][field]": "Date Recognized",
        "sort[0][direction]": "desc",
        "pageSize": 1,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i in range(0, len(entries), 10):
            batch = entries[i : i + 10]
            body = {"records": [{"fields": e} for e in batch]}
            resp = await client.post(url, headers=_headers(cfg), json=body)
            resp.raise_for_status()
            results.extend(resp.json().get("records", []))
    return results
