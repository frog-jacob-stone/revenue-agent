"""Config reconciliation — the check that stops revenue going missing."""
from __future__ import annotations

from datetime import date

import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import groups as groups_service
from app.services.billing import reconcile

ACME = 5735774
FAIRVIEW = 5735999

AS_OF = date(2026, 8, 7)


async def _client(pool, cid: int, name: str, currency: str = "USD"):
    await pool.execute(
        "INSERT INTO harvest_clients (harvest_id, name, currency) VALUES ($1,$2,$3)",
        cid, name, currency,
    )


async def _project(
    pool, pid: int, name: str, cid: int, cname: str, *,
    billable: bool = True, active: bool = True, fixed_fee: bool = False,
    currency: str = "USD", hourly_rate: float | None = 185.0,
):
    await pool.execute(
        """
        INSERT INTO harvest_projects
            (harvest_id, name, client_id, client_name, client_currency,
             is_billable, is_active, is_fixed_fee, hourly_rate)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """,
        pid, name, cid, cname, currency, billable, active, fixed_fee, hourly_rate,
    )


def _codes(report) -> list[str]:
    return [f["code"] for f in report["flags"]]


def _patch_time(monkeypatch, entries_by_project: dict[int, list] | None = None):
    """Patch the account-wide time sweep reconcile uses.

    Reconcile asks for every entry in the window once and groups client-side,
    rather than one query per project — before any config exists, every
    billable project is unmapped, so per-project queries would mean hundreds of
    requests on first page load.
    """
    flat = [e for es in (entries_by_project or {}).values() for e in es]

    async def _all(cfg, *, from_, to):
        return flat

    monkeypatch.setattr(
        "app.services.billing.reconcile.harvest.list_time_entries_all", _all
    )


def _entry(project_id: int, hours: float, rate: float | None = 185.0):
    return {
        "hours": hours, "rounded_hours": hours, "billable": True,
        "is_billed": False, "billable_rate": rate,
        "project": {"id": project_id}, "task": {"id": 1},
    }


@pytest.fixture
def no_time(monkeypatch):
    """Default: Harvest reports no time. Individual tests override."""
    _patch_time(monkeypatch)


async def test_unmapped_project_with_time_is_an_error(monkeypatch):
    pool = await get_pool()
    await _client(pool, FAIRVIEW, "Fairview Dental")
    await _project(pool, 14309902, "Website Refresh", FAIRVIEW, "Fairview Dental")

    _patch_time(monkeypatch, {14309902: [_entry(14309902, 40.0), _entry(14309902, 22.5)]})

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)

    assert "UNMAPPED_PROJECT" in _codes(report)
    unmapped = report["unmapped_projects"][0]
    assert unmapped["uninvoiced_hours"] == 62.5
    assert unmapped["estimated_value"] == pytest.approx(11562.50)
    assert report["counts"]["error"] == 1


async def test_unmapped_project_without_time_is_a_warning_not_an_error(no_time):
    """A project nobody has logged time to isn't lost revenue — it's a config
    gap. Visible as a warning, but never competing with the errors, which are
    money on the floor right now."""
    pool = await get_pool()
    await _client(pool, FAIRVIEW, "Fairview Dental")
    await _project(pool, 14309902, "Website Refresh", FAIRVIEW, "Fairview Dental")

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)

    assert len(report["unmapped_projects"]) == 1
    assert "UNMAPPED_PROJECT" not in _codes(report)
    assert "UNMAPPED_PROJECT_NO_TIME" in _codes(report)
    assert report["counts"] == {"error": 0, "warning": 1, "info": 0}

    flag = next(f for f in report["flags"] if f["code"] == "UNMAPPED_PROJECT_NO_TIME")
    assert flag["context"]["harvest_project_id"] == 14309902
    assert "Website Refresh" in flag["message"]


async def test_a_project_is_never_flagged_both_ways(no_time, monkeypatch):
    """The two unmapped flags partition the set — one project, one flag."""
    pool = await get_pool()
    await _client(pool, FAIRVIEW, "Fairview Dental")
    await _project(pool, 14309902, "Website Refresh", FAIRVIEW, "Fairview Dental")
    await _project(pool, 14309903, "Retainer", FAIRVIEW, "Fairview Dental")
    _patch_time(monkeypatch, {14309902: [_entry(14309902, 10.0)]})

    codes = _codes(await reconcile.reconcile_config(pool, settings, as_of=AS_OF))
    assert sorted(codes) == ["UNMAPPED_PROJECT", "UNMAPPED_PROJECT_NO_TIME"]


async def test_a_manual_group_suppresses_the_no_time_warning_too(no_time):
    """`manual` answers "where does this bill?" whether or not time exists —
    otherwise every milestone project warns forever."""
    pool = await get_pool()
    await _client(pool, ACME, "Kestrel Media")
    await _project(pool, 14308714, "Kestrel Rebuild", ACME, "Kestrel Media")
    await groups_service.create_group(pool, {
        "name": "Kestrel Media — Milestone Build",
        "harvest_client_id": ACME,
        "billing_type": "manual",
        "projects": [{"harvest_project_id": 14308714}],
    })

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert "UNMAPPED_PROJECT_NO_TIME" not in _codes(report)


async def test_manual_group_suppresses_unmapped_project(monkeypatch):
    """The entire reason `manual` exists. Without it, every milestone-billed
    project raises an error on every single run."""
    pool = await get_pool()
    await _client(pool, ACME, "Kestrel Media")
    await _project(pool, 14308714, "Kestrel Rebuild", ACME, "Kestrel Media")

    _patch_time(monkeypatch, {14308714: [_entry(14308714, 80.0, 200.0)]})

    before = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert "UNMAPPED_PROJECT" in _codes(before)

    await groups_service.create_group(pool, {
        "name": "Kestrel Media — Milestone Build",
        "harvest_client_id": ACME,
        "billing_type": "manual",
        "projects": [{"harvest_project_id": 14308714}],
    })

    after = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert "UNMAPPED_PROJECT" not in _codes(after)
    assert after["unmapped_projects"] == []


async def test_type_mismatch_is_a_warning(no_time):
    pool = await get_pool()
    await _client(pool, ACME, "Acme Corp")
    await _project(pool, 14308912, "Ridgeway ERP", ACME, "Acme Corp", fixed_fee=True)
    await groups_service.create_group(pool, {
        "name": "Ridgeway — Integration",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 14308912}],
    })

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)

    assert "TYPE_MISMATCH" in _codes(report)
    assert report["counts"]["error"] == 0


async def test_currency_mismatch_within_a_group_is_an_error(no_time):
    pool = await get_pool()
    await _client(pool, ACME, "Acme Corp")
    await _project(pool, 1, "US work", ACME, "Acme Corp", currency="USD")
    await _project(pool, 2, "EU work", ACME, "Acme Corp", currency="EUR")
    await groups_service.create_group(pool, {
        "name": "Acme — mixed currency",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 1}, {"harvest_project_id": 2}],
    })

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert "CURRENCY_MISMATCH" in _codes(report)


async def test_archived_project_still_grouped_is_a_warning(no_time):
    pool = await get_pool()
    await _client(pool, ACME, "Acme Corp")
    await _project(pool, 14307440, "POS Pilot", ACME, "Acme Corp", active=True)
    await groups_service.create_group(pool, {
        "name": "Acme — POS",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 14307440}],
    })
    await pool.execute(
        "UPDATE harvest_projects SET is_active = false WHERE harvest_id = 14307440"
    )

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert "INACTIVE_PROJECT_WITH_TIME" in _codes(report)


async def test_non_billable_project_is_never_unmapped(no_time):
    """Internal projects aren't invoiceable, so they must not generate noise."""
    pool = await get_pool()
    await _client(pool, ACME, "Frogslayer")
    await _project(pool, 14309955, "Internal — Sales Enablement", ACME,
                   "Frogslayer", billable=False)

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert report["unmapped_projects"] == []


async def test_include_time_false_skips_harvest_entirely(monkeypatch):
    """The fast structural check must not touch the API."""
    pool = await get_pool()
    await _client(pool, FAIRVIEW, "Fairview Dental")
    await _project(pool, 14309902, "Website Refresh", FAIRVIEW, "Fairview Dental")

    async def _boom(*a, **kw):
        raise AssertionError("Harvest must not be called when include_time=False")
    monkeypatch.setattr(
        "app.services.billing.reconcile.harvest.list_time_entries_all", _boom
    )

    report = await reconcile.reconcile_config(
        pool, settings, include_time=False, as_of=AS_OF
    )
    assert len(report["unmapped_projects"]) == 1
    assert report["unmapped_projects"][0]["uninvoiced_hours"] == 0.0
    # Zero hours here means "not measured", so it must not be reported as
    # "no uninvoiced time" — that would be a guess dressed as a finding.
    assert "UNMAPPED_PROJECT_NO_TIME" not in _codes(report)


async def test_clean_config_reports_nothing(no_time):
    pool = await get_pool()
    await _client(pool, ACME, "Acme Corp")
    await _project(pool, 14307913, "Acme Platform", ACME, "Acme Corp")
    await groups_service.create_group(pool, {
        "name": "Acme — Platform",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 14307913}],
    })

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)
    assert report["flags"] == []
    assert report["unmapped_projects"] == []
    assert report["snapshot"]["projects"] == 1


async def test_pricing_uses_one_sweep_regardless_of_project_count(monkeypatch):
    """Guards a real performance defect.

    Before any config exists every billable project is unmapped — which is
    exactly when this screen is first opened. Querying per project meant
    hundreds of paginated requests against one rate-limit bucket and a hung
    page load. Reconcile must ask once and group client-side.
    """
    pool = await get_pool()
    await _client(pool, ACME, "Acme Corp")
    for pid in range(9001, 9026):  # 25 unmapped projects
        await _project(pool, pid, f"Project {pid}", ACME, "Acme Corp")

    sweeps = 0

    async def _all(cfg, *, from_, to):
        nonlocal sweeps
        sweeps += 1
        return [_entry(9001, 10.0), _entry(9002, 5.0)]

    async def _per_project(*a, **kw):
        raise AssertionError("reconcile must not query time per project")

    monkeypatch.setattr(
        "app.services.billing.reconcile.harvest.list_time_entries_all", _all
    )
    monkeypatch.setattr(
        "app.services.billing.reconcile.harvest.list_time_entries", _per_project
    )

    report = await reconcile.reconcile_config(pool, settings, as_of=AS_OF)

    assert sweeps == 1
    assert len(report["unmapped_projects"]) == 25
    # Only the two projects with entries carry hours.
    priced = {p["harvest_project_id"]: p["uninvoiced_hours"]
              for p in report["unmapped_projects"] if p["uninvoiced_hours"] > 0}
    assert priced == {9001: 10.0, 9002: 5.0}
