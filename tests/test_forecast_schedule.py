"""Projected end dates from Forecast.

The derivation is the interesting part: thousands of assignment rows reduce to
one date per project, and the rules about *which* rows count (people, not
placeholders) and what a null means (nobody booked, not a failed sync) are what
these pin.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.db import get_pool
from app.integrations import forecast
from app.services import forecast_snapshot, projects
from app.services.billing import harvest_snapshot

ACME = 5735774
PLATFORM, MOBILE, HOSTING = 14307913, 14307914, 14307915


async def _seed(pool) -> None:
    await pool.execute(
        "INSERT INTO harvest_clients (harvest_id, name, currency) "
        "VALUES ($1, 'Acme Corp', 'USD')", ACME,
    )
    for pid, name, ends in (
        (PLATFORM, "Acme Platform", date(2026, 6, 30)),
        (MOBILE, "Acme Mobile", date(2026, 12, 31)),
        (HOSTING, "Acme Hosting", None),
    ):
        await pool.execute(
            """
            INSERT INTO harvest_projects
                (harvest_id, name, client_id, client_name, is_billable, is_active, ends_on)
            VALUES ($1,$2,$3,'Acme Corp',true,true,$4)
            """,
            pid, name, ACME, ends,
        )


def _fake_forecast(monkeypatch, payload):
    async def _fake(cfg, *, today=None):
        return payload
    monkeypatch.setattr(forecast, "get_last_scheduled_by_harvest_id", _fake)
    monkeypatch.setattr(
        forecast_snapshot.forecast, "get_last_scheduled_by_harvest_id", _fake
    )


@pytest.fixture(autouse=True)
def _no_harvest(monkeypatch):
    """`POST /projects/refresh` pulls Harvest too. Stubbed to nothing here so
    these tests stay about the Forecast half; the roster is seeded directly."""
    async def _empty(*a, **kw):
        return []
    monkeypatch.setattr(harvest_snapshot.harvest, "get_clients", _empty)
    monkeypatch.setattr(harvest_snapshot.harvest, "list_projects_detailed", _empty)
    monkeypatch.setattr(
        harvest_snapshot.harvest, "get_invoice_item_categories", _empty
    )


# ── The derivation, against raw Forecast payloads ───────────────────────────


def _assignments(rows):
    return [
        {
            "project_id": pid,
            "person_id": person,
            "placeholder_id": None if person else 9,
            "start_date": "2026-01-01",
            "end_date": end,
        }
        for pid, person, end in rows
    ]


async def test_last_scheduled_takes_the_max_end_date(monkeypatch):
    monkeypatch.setattr(
        forecast, "_get_projects",
        lambda cfg: _async([{"id": 400, "harvest_id": PLATFORM}]),
    )
    monkeypatch.setattr(
        forecast, "_get_assignments",
        lambda cfg, s, e: _async(_assignments([
            (400, 1, "2026-09-01"),
            (400, 2, "2026-11-20"),   # the latest — this is the answer
            (400, 3, "2026-10-15"),
        ])),
    )
    out = await forecast.get_last_scheduled_by_harvest_id(settings, today=date(2026, 8, 14))
    assert out[PLATFORM]["last_scheduled_on"] == "2026-11-20"
    assert out[PLATFORM]["assignment_count"] == 3


async def test_placeholder_bookings_do_not_count(monkeypatch):
    """A placeholder is capacity held open, not someone scheduled. Counting it
    would push a project later on a booking with no name against it."""
    monkeypatch.setattr(
        forecast, "_get_projects",
        lambda cfg: _async([{"id": 400, "harvest_id": PLATFORM}]),
    )
    monkeypatch.setattr(
        forecast, "_get_assignments",
        lambda cfg, s, e: _async(_assignments([
            (400, 1, "2026-09-01"),
            (400, None, "2027-06-30"),   # placeholder, far later — ignored
        ])),
    )
    out = await forecast.get_last_scheduled_by_harvest_id(settings, today=date(2026, 8, 14))
    assert out[PLATFORM]["last_scheduled_on"] == "2026-09-01"
    assert out[PLATFORM]["assignment_count"] == 1


async def test_project_with_no_bookings_is_present_with_a_null(monkeypatch):
    """Present-and-null is a different fact from absent: hosting is linked to
    Forecast and genuinely has nobody on it."""
    monkeypatch.setattr(
        forecast, "_get_projects",
        lambda cfg: _async([
            {"id": 400, "harvest_id": PLATFORM},
            {"id": 402, "harvest_id": HOSTING},
            {"id": 403, "harvest_id": None},   # Forecast-only, nothing to attach to
        ]),
    )
    monkeypatch.setattr(
        forecast, "_get_assignments",
        lambda cfg, s, e: _async(_assignments([(400, 1, "2026-09-01")])),
    )
    out = await forecast.get_last_scheduled_by_harvest_id(settings, today=date(2026, 8, 14))
    assert out[HOSTING]["last_scheduled_on"] is None
    assert out[HOSTING]["assignment_count"] == 0
    assert set(out) == {PLATFORM, HOSTING}


def _async(value):
    async def _run():
        return value
    return _run()


# ── The cache, and what /projects serves ────────────────────────────────────


async def test_refresh_populates_projected_end_dates(client, monkeypatch):
    pool = await get_pool()
    await _seed(pool)
    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": "2026-11-20",
                   "assignment_count": 3},
        HOSTING: {"forecast_project_id": 402, "last_scheduled_on": None,
                  "assignment_count": 0},
    })

    res = await client.post("/projects/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["forecast"] == {
        "projects": 2, "with_schedule": 1, "without_schedule": 1, "pruned": 0,
    }
    assert body["forecast_error"] is None
    # Both sources ran. Refreshing only one would leave half of every row stale
    # while looking like the whole page had updated.
    assert body["harvest"] is not None

    rows = {p["name"]: p for p in (await client.get("/projects")).json()}
    assert rows["Acme Platform"]["projected_end_date"] == "2026-11-20"
    assert rows["Acme Hosting"]["projected_end_date"] is None
    # Never linked to Forecast at all — still listed, just without a forecast.
    assert rows["Acme Mobile"]["projected_end_date"] is None


async def test_resync_clears_a_schedule_that_went_away(client, monkeypatch):
    """The reason this cache needs pruning: it stores a derivation, so a project
    whose assignments were all deleted must go to null rather than keep serving
    yesterday's date."""
    pool = await get_pool()
    await _seed(pool)

    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": "2026-11-20",
                   "assignment_count": 3},
    })
    await client.post("/projects/refresh")

    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": None,
                   "assignment_count": 0},
    })
    await client.post("/projects/refresh")

    rows = {p["name"]: p for p in (await client.get("/projects")).json()}
    assert rows["Acme Platform"]["projected_end_date"] is None


async def test_unlinking_a_project_prunes_its_row(client, monkeypatch):
    pool = await get_pool()
    await _seed(pool)

    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": "2026-11-20",
                   "assignment_count": 3},
        HOSTING: {"forecast_project_id": 402, "last_scheduled_on": "2026-09-01",
                  "assignment_count": 1},
    })
    await client.post("/projects/refresh")

    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": "2026-11-20",
                   "assignment_count": 3},
    })
    res = await client.post("/projects/refresh")
    assert res.json()["forecast"]["pruned"] == 1

    rows = {p["name"]: p for p in (await client.get("/projects")).json()}
    assert rows["Acme Hosting"]["projected_end_date"] is None


async def test_refresh_requires_auth(unauthed_client):
    res = await unauthed_client.post("/projects/refresh")
    assert res.status_code in (401, 403)


async def test_unconfigured_forecast_is_a_partial_success_not_a_failure(
    client, monkeypatch
):
    """Harvest has already committed by the time Forecast is attempted, so a
    5xx would tell the operator nothing happened when half of it did. The
    shortfall is reported in the body instead — and must be reported, or the
    projected-end column would look merely empty."""
    await _seed(await get_pool())
    monkeypatch.setattr(settings, "forecast_account_id", "")

    res = await client.post("/projects/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["harvest"] is not None
    assert body["forecast"] is None
    assert "FORECAST_ACCOUNT_ID" in body["forecast_error"]


async def test_a_forecast_outage_does_not_lose_the_harvest_refresh(
    client, monkeypatch
):
    async def _boom(cfg, *, today=None):
        raise RuntimeError("Forecast returned 500")
    monkeypatch.setattr(
        forecast_snapshot.forecast, "get_last_scheduled_by_harvest_id", _boom
    )
    await _seed(await get_pool())

    res = await client.post("/projects/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["harvest"] is not None
    assert body["forecast"] is None
    assert "Forecast returned 500" in body["forecast_error"]


async def test_refresh_writes_an_audit_event(client, monkeypatch):
    pool = await get_pool()
    await _seed(pool)
    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": "2026-11-20",
                   "assignment_count": 3},
    })
    await client.post("/projects/refresh")

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'forecast.schedule.refreshed'"
    )
    assert payload is not None
    assert payload["with_schedule"] == 1


@pytest.mark.parametrize("archived", [False, True])
async def test_the_endpoint_serves_the_field_for_both_views(
    client, monkeypatch, archived
):
    """The API stays uniform. The tab *renders* projected end only for active
    work — archived has already ended, so a forecast of when it will end is
    noise — but that is a presentation choice, and an endpoint that dropped the
    field for archived would make "no forecast" and "not provided" the same
    answer for any other consumer."""
    pool = await get_pool()
    await _seed(pool)
    if archived:
        await pool.execute("UPDATE harvest_projects SET is_active = false")
    _fake_forecast(monkeypatch, {
        PLATFORM: {"forecast_project_id": 400, "last_scheduled_on": "2026-11-20",
                   "assignment_count": 3},
    })
    await client.post("/projects/refresh")

    rows = (await client.get("/projects", params={"archived": str(archived).lower()})).json()
    assert {p["name"]: p["projected_end_date"] for p in rows}["Acme Platform"] == "2026-11-20"
