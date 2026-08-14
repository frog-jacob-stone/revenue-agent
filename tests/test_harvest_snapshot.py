"""Harvest snapshot upsert — the project date fields.

`starts_on` / `ends_on` arrive from Harvest as date-only strings and land in
`date` columns. asyncpg will not coerce one into the other, so the `_date()`
helper is load-bearing: without it the whole snapshot raises rather than
silently storing a wrong value. These tests pin that, and pin that a project
Harvest has no dates for stores NULL rather than a placeholder.
"""
from __future__ import annotations

from datetime import date

from app.db import get_pool
from app.services.billing.harvest_snapshot import _date, _upsert_projects


def _harvest_project(pid: int, name: str, **overrides) -> dict:
    """A project shaped the way Harvest's /v2/projects returns one."""
    return {
        "id": pid,
        "name": name,
        "code": None,
        "client": {"id": 5735774, "name": "Acme Corp", "currency": "USD"},
        "is_billable": True,
        "is_fixed_fee": False,
        "bill_by": "People",
        "hourly_rate": "185.0",
        "fee": None,
        "budget": None,
        "budget_by": "project",
        "budget_is_monthly": False,
        "is_active": True,
        **overrides,
    }


def test_date_helper_handles_what_harvest_actually_sends():
    assert _date("2025-02-03") == date(2025, 2, 3)
    # Harvest omits the key, sends null, or sends empty for an unset date.
    assert _date(None) is None
    assert _date("") is None
    # Unreadable is indistinguishable from absent, and must not raise —
    # one malformed project cannot be allowed to fail the whole snapshot.
    assert _date("not-a-date") is None


async def test_project_dates_persist(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _upsert_projects(conn, [
            _harvest_project(
                14307913, "Acme Platform",
                starts_on="2025-02-03", ends_on="2026-06-30",
            ),
            # Harvest sends nulls for a project with no dates set.
            _harvest_project(14307914, "Acme Mobile", starts_on=None, ends_on=None),
        ])

    rows = {
        r["name"]: r for r in await pool.fetch(
            "SELECT name, starts_on, ends_on FROM harvest_projects"
        )
    }

    assert rows["Acme Platform"]["starts_on"] == date(2025, 2, 3)
    assert rows["Acme Platform"]["ends_on"] == date(2026, 6, 30)
    assert rows["Acme Mobile"]["starts_on"] is None
    assert rows["Acme Mobile"]["ends_on"] is None


async def test_resync_updates_a_slipped_end_date(client):
    """A project that slips gets a new `ends_on` in Harvest; the upsert must
    carry that through rather than keeping the first value it saw."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _upsert_projects(conn, [
            _harvest_project(14307913, "Acme Platform", ends_on="2026-06-30"),
        ])
        await _upsert_projects(conn, [
            _harvest_project(14307913, "Acme Platform", ends_on="2026-11-20"),
        ])

    assert await pool.fetchval(
        "SELECT ends_on FROM harvest_projects WHERE harvest_id = 14307913"
    ) == date(2026, 11, 20)
