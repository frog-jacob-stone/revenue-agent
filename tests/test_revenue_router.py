"""The /revenue endpoints.

Read-only, so what is worth pinning is the contract the UI codes against: the
shapes, the orderings (the trend reads oldest-first and everything else
newest-first), that money survives as an exact decimal rather than a float, and
that every route is behind auth (Unbreakable Rule #2).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.db import get_pool

ACME = 14307913
_MISSING = "00000000-0000-0000-0000-000000000000"

ROUTES = [
    "/revenue/entries",
    "/revenue/summary",
    "/revenue/clients",
    "/revenue/runs",
    f"/revenue/runs/{_MISSING}",
]


async def _seed(conn) -> dict[date, str]:
    runs: dict[date, str] = {}
    for period, amount in [
        (date(2026, 1, 1), "1000.00"),
        (date(2026, 2, 1), "500.00"),
    ]:
        runs[period] = await conn.fetchval(
            "INSERT INTO revenue_runs (period_month, status, created_by) "
            "VALUES ($1, 'recognized', 'test') RETURNING id",
            period,
        )
        await conn.execute(
            """
            INSERT INTO revenue_entries (
                revenue_run_id, period_month, harvest_project_id,
                harvest_project_name, revenue_type, recognized_amount,
                computed_amount, logged_hours
            ) VALUES ($1, $2, $3, 'Acme Platform', 'fixed_fee', $4, $4, 10)
            """,
            runs[period], period, ACME, Decimal(amount),
        )
    return runs


async def test_every_route_requires_auth(unauthed_client):
    for route in ROUTES:
        res = await unauthed_client.get(route)
        assert res.status_code == 401, route


async def test_entries_serve_the_ledger_shape(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get("/revenue/entries")
    assert res.status_code == 200
    rows = res.json()

    assert [r["period_month"] for r in rows] == ["2026-02-01", "2026-01-01"]
    # Exact decimals, serialized as strings. The ledger reconciles to the cent
    # and a float round-trip is the one place that would stop being true.
    assert rows[0]["recognized_amount"] == "500.00"
    assert rows[0]["cumulative_recognized"] == "1500.00"
    assert rows[0]["harvest_project_name"] == "Acme Platform"
    assert rows[0]["revenue_type"] == "fixed_fee"


async def test_entry_date_bounds_are_month_granular(client):
    """A caller passing any date in a month gets that whole month — periods are
    first-of-month, so a mid-month bound would otherwise exclude it."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get("/revenue/entries", params={"date_from": "2026-02-14"})
    assert [r["period_month"] for r in res.json()] == ["2026-02-01"]


async def test_entries_can_exclude_projects_with_nothing_in_the_window(client):
    """What the Overview grid asks for: its rows are its projects, so a project
    with no revenue this period should never be sent rather than sent and
    hidden."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        runs = await _seed(conn)
        await conn.execute(
            """
            INSERT INTO revenue_entries (
                revenue_run_id, period_month, harvest_project_id,
                harvest_project_name, revenue_type, recognized_amount,
                computed_amount
            ) VALUES ($1, '2026-01-01', 999, 'Dormant', 'hosting', 0, 0)
            """,
            runs[date(2026, 1, 1)],
        )

    assert len((await client.get("/revenue/entries")).json()) == 3

    res = await client.get(
        "/revenue/entries", params={"exclude_empty_projects": "true"}
    )
    assert {r["harvest_project_id"] for r in res.json()} == {ACME}


async def test_client_filter_narrows_entries_and_summary_alike(client):
    """Repeated query params, and both endpoints take them — the chart and the
    grid beside it must show the same book."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects (harvest_id, name, client_id, client_name) "
            "VALUES ($1, 'Acme Platform', 500, 'Acme'), (999, 'Beta App', 600, 'Beta')",
            ACME,
        )
        runs = await _seed(conn)
        await conn.execute(
            "INSERT INTO revenue_entries (revenue_run_id, period_month, "
            "harvest_project_id, harvest_project_name, revenue_type, "
            "recognized_amount, computed_amount) "
            "VALUES ($1, '2026-01-01', 999, 'Beta App', 'hosting', 250, 250)",
            runs[date(2026, 1, 1)],
        )

    res = await client.get("/revenue/entries", params={"client_ids": [500]})
    assert {r["harvest_project_id"] for r in res.json()} == {ACME}
    assert res.json()[0]["client_id"] == 500

    res = await client.get("/revenue/summary", params={"client_ids": [500]})
    assert [m["recognized_amount"] for m in res.json()] == ["1000.00", "500.00"]

    # Both clients at once.
    res = await client.get("/revenue/summary", params={"client_ids": [500, 600]})
    assert res.json()[0]["recognized_amount"] == "1250.00"


async def test_client_options_endpoint(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects (harvest_id, name, client_id, client_name) "
            "VALUES ($1, 'Acme Platform', 500, 'Acme')", ACME,
        )
        await _seed(conn)

    res = await client.get("/revenue/clients")
    assert res.status_code == 200
    assert res.json() == [{
        "client_id": 500,
        "client_name": "Acme",
        "recognized_amount": "1500.00",
        "project_count": 1,
    }]


async def test_entries_filter_by_project(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get("/revenue/entries", params={"harvest_project_id": 999})
    assert res.json() == []


async def test_summary_reads_oldest_first(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get("/revenue/summary")
    assert res.status_code == 200
    rows = res.json()
    # The one list that reads forwards: it is a chart, not a lookup.
    assert [r["period_month"] for r in rows] == ["2026-01-01", "2026-02-01"]
    assert rows[0]["recognized_amount"] == "1000.00"
    assert rows[0]["logged_hours"] == "10.00"
    assert rows[0]["entry_count"] == 1


async def test_summary_bounds_match_the_entries_bounds(client):
    """Inclusive and month-granular, so a caller can hand the same pair to
    both endpoints and get windows that agree."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get(
        "/revenue/summary", params={"date_from": "2026-02-20", "date_to": "2026-02-01"}
    )
    assert [r["period_month"] for r in res.json()] == ["2026-02-01"]


async def test_summary_unbounded_returns_every_month(client):
    """What the Overview tab asks for, so its period filter can offer exactly
    the years the ledger covers instead of guessing at a range."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get("/revenue/summary")
    assert [r["period_month"] for r in res.json()] == ["2026-01-01", "2026-02-01"]


async def test_runs_list_newest_first_with_totals(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _seed(conn)

    res = await client.get("/revenue/runs")
    assert res.status_code == 200
    rows = res.json()
    assert [r["period_month"] for r in rows] == ["2026-02-01", "2026-01-01"]
    assert rows[0]["status"] == "recognized"
    assert rows[0]["total_recognized"] == "500.00"
    assert rows[0]["entry_count"] == 1


async def test_run_detail_includes_its_entries(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        runs = await _seed(conn)

    res = await client.get(f"/revenue/runs/{runs[date(2026, 2, 1)]}")
    assert res.status_code == 200
    run = res.json()
    assert run["period_month"] == "2026-02-01"
    assert run["total_recognized"] == "500.00"
    assert len(run["entries"]) == 1
    assert run["entries"][0]["harvest_project_id"] == ACME


async def test_run_detail_serves_a_draft(client):
    """The review payload. Under ADR-0004 reading this is what makes the
    subsequent finalize click an authorization, so it cannot be filtered to
    finalized runs."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        draft = await conn.fetchval(
            "INSERT INTO revenue_runs (period_month, status, created_by) "
            "VALUES ('2026-03-01', 'draft', 'test') RETURNING id"
        )

    res = await client.get(f"/revenue/runs/{draft}")
    assert res.status_code == 200
    assert res.json()["status"] == "draft"
    assert res.json()["total_recognized"] == "0.00"


async def test_unknown_run_is_a_404(client):
    res = await client.get("/revenue/runs/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


async def test_endpoints_are_empty_rather_than_broken_with_no_data(client):
    """The state the app is in until the backfill runs."""
    assert (await client.get("/revenue/entries")).json() == []
    assert (await client.get("/revenue/summary")).json() == []
    assert (await client.get("/revenue/runs")).json() == []


# ── The operator-initiated half ─────────────────────────────────────────────
#
# No approval rows, by ADR-0004: the operator reads the exact entries via
# GET /revenue/runs/{id}, none of this is agent-reachable, and every transition
# audits. See tests/test_no_agent_approval_tools.py for the structural half.


async def test_write_routes_require_auth(unauthed_client):
    routes = [
        ("GET", "/revenue/config"),
        ("PATCH", f"/revenue/config/{ACME}"),
        ("DELETE", f"/revenue/config/{ACME}"),
        ("POST", "/revenue/runs"),
        ("POST", f"/revenue/runs/{_MISSING}/finalize"),
        ("POST", f"/revenue/runs/{_MISSING}/abandon"),
        ("PATCH", f"/revenue/runs/{_MISSING}/entries/{_MISSING}"),
    ]
    for method, path in routes:
        res = await unauthed_client.request(method, path, json={})
        assert res.status_code == 401, f"{method} {path}"


async def test_config_round_trip(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects "
            "(harvest_id, name, client_id, client_name, is_billable, is_active) "
            "VALUES ($1, 'Acme Platform', 500, 'Acme', true, true)",
            ACME,
        )

    listed = (await client.get("/revenue/config")).json()
    assert listed[0]["revenue_type"] is None  # unconfigured is the marker

    res = await client.patch(
        f"/revenue/config/{ACME}",
        json={"revenue_type": "fixed_fee", "contracted_fees": "250000.00"},
    )
    assert res.status_code == 200
    assert res.json()["contracted_fees"] == "250000.00"

    assert (await client.delete(f"/revenue/config/{ACME}")).status_code == 204
    # Deleting what is not there is a 404, not a silent success — usually a
    # wrong id.
    assert (await client.delete(f"/revenue/config/{ACME}")).status_code == 404


async def test_fixed_fee_without_fees_is_a_422(client):
    res = await client.patch(
        f"/revenue/config/{ACME}", json={"revenue_type": "fixed_fee"}
    )
    assert res.status_code == 422
    assert "contracted fees" in res.json()["detail"]


async def test_planning_without_config_is_a_409_naming_the_projects(client):
    """The UI turns this into "N projects need configuration" with a link, so
    the list has to come back in the body rather than only in the message."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO harvest_projects "
            "(harvest_id, name, client_id, is_billable, is_active) "
            "VALUES ($1, 'Acme Platform', 500, true, true)",
            ACME,
        )

    res = await client.post("/revenue/runs", json={"period_month": "2026-01-01"})
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["unconfigured_projects"][0]["harvest_project_name"] == "Acme Platform"


async def test_finalize_and_abandon_report_a_missing_run(client):
    for action in ["finalize", "abandon"]:
        res = await client.post(f"/revenue/runs/{_MISSING}/{action}")
        assert res.status_code == 404


async def test_overriding_a_finalized_run_is_a_409(client):
    pool = await get_pool()
    async with pool.acquire() as conn:
        runs = await _seed(conn)
        entry_id = await conn.fetchval(
            "SELECT id FROM revenue_entries WHERE revenue_run_id = $1",
            runs[date(2026, 1, 1)],
        )

    res = await client.patch(
        f"/revenue/runs/{runs[date(2026, 1, 1)]}/entries/{entry_id}",
        json={"recognized_amount": "1.00", "override_reason": "nope"},
    )
    assert res.status_code == 409
    assert "not a draft" in res.json()["detail"]
