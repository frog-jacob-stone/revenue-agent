"""Projects router — which engagements the delivery view shows, and in what order.

The interesting behaviour is all exclusion: the active and archived lists are
disjoint, and non-billable Harvest projects are not engagements at all.
"""
from __future__ import annotations

from datetime import date

from app.db import get_pool

ACME = 5735774


async def _project(
    pool,
    harvest_id: int,
    name: str,
    *,
    is_active: bool = True,
    is_billable: bool = True,
    starts_on: date | None = None,
    ends_on: date | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO harvest_projects
            (harvest_id, name, client_id, client_name, client_currency,
             is_billable, is_active, starts_on, ends_on)
        VALUES ($1,$2,$3,'Acme Corp','USD',$4,$5,$6,$7)
        """,
        harvest_id, name, ACME, is_billable, is_active, starts_on, ends_on,
    )


async def _seed(pool) -> None:
    await pool.execute(
        "INSERT INTO harvest_clients (harvest_id, name, currency) "
        "VALUES ($1, 'Acme Corp', 'USD')", ACME,
    )
    await _project(
        pool, 14307913, "Acme Platform",
        starts_on=date(2025, 2, 3), ends_on=date(2026, 6, 30),
    )
    # No dates at all — Harvest treats both as optional and many projects
    # leave them unset.
    await _project(pool, 14307914, "Acme Mobile")
    # Internal time bucket: a Harvest project, but not an engagement.
    await _project(pool, 14307915, "Acme Internal", is_billable=False)
    await _project(
        pool, 14307916, "Acme Legacy", is_active=False,
        starts_on=date(2023, 1, 9), ends_on=date(2024, 11, 30),
    )
    await _project(
        pool, 14307917, "Acme Pilot", is_active=False,
        starts_on=date(2022, 5, 2), ends_on=date(2025, 3, 31),
    )
    # Archived with no end date — must sort last, not first.
    await _project(pool, 14307918, "Acme Discovery", is_active=False)


async def test_projects_endpoint_requires_auth(unauthed_client):
    res = await unauthed_client.get("/projects")
    assert res.status_code in (401, 403)


async def test_active_list_excludes_archived_and_non_billable(client):
    await _seed(await get_pool())

    res = await client.get("/projects")
    assert res.status_code == 200, res.text
    names = [p["name"] for p in res.json()]

    assert names == ["Acme Mobile", "Acme Platform"]  # alphabetical
    assert "Acme Internal" not in names, "non-billable project leaked into the roster"
    assert "Acme Legacy" not in names, "archived project leaked into the active list"


async def test_archived_list_swaps_rather_than_extends(client):
    await _seed(await get_pool())

    res = await client.get("/projects", params={"archived": "true"})
    assert res.status_code == 200, res.text
    names = [p["name"] for p in res.json()]

    # Newest-closed-first, with the undated archived project last rather than
    # sorting as though it ended at the dawn of time.
    assert names == ["Acme Pilot", "Acme Legacy", "Acme Discovery"]
    assert "Acme Platform" not in names, "active project leaked into the archived list"
    assert "Acme Internal" not in names


async def test_dates_round_trip_and_nulls_stay_null(client):
    await _seed(await get_pool())

    rows = {p["name"]: p for p in (await client.get("/projects")).json()}

    assert rows["Acme Platform"]["starts_on"] == "2025-02-03"
    assert rows["Acme Platform"]["ends_on"] == "2026-06-30"
    # A date Harvest never set stays absent — not coerced to an epoch.
    assert rows["Acme Mobile"]["starts_on"] is None
    assert rows["Acme Mobile"]["ends_on"] is None
    assert rows["Acme Platform"]["client_name"] == "Acme Corp"
    assert rows["Acme Platform"]["synced_at"] is not None
