"""Billing router — wiring, status codes, and the auth requirement."""
from __future__ import annotations

import pytest

from app.db import get_pool

ACME = 5735774


async def _seed(pool):
    await pool.execute(
        "INSERT INTO harvest_clients (harvest_id, name, currency) "
        "VALUES ($1, 'Acme Corp', 'USD')", ACME,
    )
    for pid, name in ((14307913, "Acme Platform"), (14307914, "Acme Mobile")):
        await pool.execute(
            """
            INSERT INTO harvest_projects
                (harvest_id, name, client_id, client_name, client_currency,
                 is_billable, is_active, hourly_rate)
            VALUES ($1,$2,$3,'Acme Corp','USD',true,true,185)
            """,
            pid, name, ACME,
        )


@pytest.fixture(autouse=True)
def _no_harvest(monkeypatch):
    async def _all(cfg, *, from_, to):
        return []
    monkeypatch.setattr(
        "app.services.billing.reconcile.harvest.list_time_entries_all", _all
    )


async def test_billing_endpoints_require_auth(unauthed_client):
    for method, path in (
        ("get", "/billing/groups"),
        ("get", "/billing/health"),
        ("post", "/billing/snapshot/refresh"),
    ):
        res = await getattr(unauthed_client, method)(path)
        assert res.status_code in (401, 403), f"{method.upper()} {path} was not protected"


async def test_create_and_fetch_a_group(client):
    pool = await get_pool()
    await _seed(pool)

    res = await client.post("/billing/groups", json={
        "name": "Acme — Platform + Mobile",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "time_summary_type": "task",
        "include_expenses": True,
        "expense_summary_type": "category",
        "projects": [
            {"harvest_project_id": 14307913, "sort_order": 1},
            {"harvest_project_id": 14307914, "sort_order": 2},
        ],
    })
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["harvest_client_name"] == "Acme Corp"
    assert len(created["projects"]) == 2

    res = await client.get(f"/billing/groups/{created['id']}")
    assert res.status_code == 200
    assert res.json()["name"] == "Acme — Platform + Mobile"


async def test_invalid_group_config_is_a_400_not_a_500(client):
    pool = await get_pool()
    await _seed(pool)

    res = await client.post("/billing/groups", json={
        "name": "Ghost project",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 99999999}],
    })
    assert res.status_code == 400
    assert "not in the Harvest snapshot" in res.json()["detail"]


async def test_expense_summary_type_rejects_a_time_only_value(client):
    """`task` is valid for time summaries and invalid for expenses. The two
    vocabularies are deliberately different types."""
    pool = await get_pool()
    await _seed(pool)

    res = await client.post("/billing/groups", json={
        "name": "Bad expense summary",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "expense_summary_type": "task",
        "projects": [],
    })
    assert res.status_code == 422


async def test_patch_updates_only_supplied_fields(client):
    pool = await get_pool()
    await _seed(pool)
    created = (await client.post("/billing/groups", json={
        "name": "Acme",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "purchase_order": "PO-4471",
        "projects": [{"harvest_project_id": 14307913}],
    })).json()

    res = await client.patch(
        f"/billing/groups/{created['id']}", json={"payment_term": "net 45"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["payment_term"] == "net 45"
    assert body["purchase_order"] == "PO-4471"


async def test_deactivate_then_list_excludes_by_default(client):
    pool = await get_pool()
    await _seed(pool)
    created = (await client.post("/billing/groups", json={
        "name": "Acme",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 14307913}],
    })).json()

    res = await client.post(f"/billing/groups/{created['id']}/deactivate")
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    assert (await client.get("/billing/groups")).json() == []
    listed = (await client.get("/billing/groups?include_inactive=true")).json()
    assert len(listed) == 1


async def test_unknown_group_is_404(client):
    res = await client.get("/billing/groups/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


async def test_health_reports_unmapped_projects(client):
    pool = await get_pool()
    await _seed(pool)

    res = await client.get("/billing/health")
    assert res.status_code == 200
    body = res.json()
    assert {p["harvest_project_id"] for p in body["unmapped_projects"]} == {
        14307913, 14307914,
    }
    assert body["snapshot"]["projects"] == 2


# ── Runs ────────────────────────────────────────────────────────────────────


async def test_run_endpoints_require_auth(unauthed_client):
    for method, path in (("get", "/billing/runs"), ("post", "/billing/runs")):
        res = await getattr(unauthed_client, method)(path)
        assert res.status_code in (401, 403), f"{method.upper()} {path} was not protected"


async def test_plan_a_run_and_read_it_back(client, monkeypatch):
    from tests.fakes.harvest import FakeHarvest

    fake = FakeHarvest()
    fake.add_client(ACME, "Acme Corp")
    fake.add_project(14307913, "Acme Platform", client_id=ACME)
    fake.add_time(14307913, spent_date="2026-07-06", hours=10, rate=185)
    fake.install(monkeypatch)

    await get_pool()
    await client.post("/billing/snapshot/refresh")
    created = (await client.post("/billing/groups", json={
        "name": "Acme — Platform",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "time_summary_type": "task",
        "projects": [{"harvest_project_id": 14307913}],
    })).json()

    res = await client.post("/billing/runs", json={"run_month": "2026-08-01"})
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["status"] == "awaiting_approval"
    assert run["label"] == "August 2026"
    assert run["planned_count"] == 1
    assert run["planned_total"] == pytest.approx(1850.0)
    assert run["items"][0]["billing_group_name"] == "Acme — Platform"
    assert run["items"][0]["billing_group_id"] == created["id"]

    fetched = await client.get(f"/billing/runs/{run['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run["id"]

    listed = (await client.get("/billing/runs")).json()
    assert len(listed) == 1 and listed[0]["id"] == run["id"]

    # A planned run creates nothing in Harvest — the fake has no write surface,
    # and only reads were recorded.
    assert {c for c, _ in fake.calls} <= {
        "get_clients", "list_projects_detailed", "get_invoice_item_categories",
        "get_task_assignments", "list_time_entries", "list_expenses", "list_invoices",
    }


async def test_abandon_a_run(client, monkeypatch):
    from tests.fakes.harvest import FakeHarvest

    FakeHarvest().install(monkeypatch)
    run = (await client.post("/billing/runs", json={"run_month": "2026-08-01"})).json()

    res = await client.post(f"/billing/runs/{run['id']}/abandon")
    assert res.status_code == 200
    assert res.json()["status"] == "abandoned"

    # Abandoning twice is a conflict, not a crash.
    assert (await client.post(f"/billing/runs/{run['id']}/abandon")).status_code == 409


async def test_unknown_run_is_404(client):
    res = await client.get("/billing/runs/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


# ── Harvest catalog (backs the group-config form) ───────────────────────────


async def test_catalog_lists_clients_with_billable_counts(client):
    pool = await get_pool()
    await _seed(pool)

    res = await client.get("/billing/harvest/clients")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["name"] == "Acme Corp"
    assert body[0]["billable_project_count"] == 2


async def test_catalog_shows_which_group_already_claims_a_project(client):
    """A claimed project must be visible and explained in the picker, not
    silently omitted — otherwise the list looks wrong to someone who knows the
    project exists."""
    pool = await get_pool()
    await _seed(pool)
    await client.post("/billing/groups", json={
        "name": "Acme — Platform",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 14307913}],
    })

    rows = (await client.get(f"/billing/harvest/projects?client_id={ACME}")).json()
    by_id = {r["harvest_id"]: r for r in rows}
    assert by_id[14307913]["billing_group_name"] == "Acme — Platform"
    assert by_id[14307914]["billing_group_id"] is None


async def test_catalog_excludes_the_group_being_edited_from_conflicts(client):
    """Editing a group must not report it as conflicting with itself."""
    pool = await get_pool()
    await _seed(pool)
    created = (await client.post("/billing/groups", json={
        "name": "Acme — Platform",
        "harvest_client_id": ACME,
        "billing_type": "time_and_materials",
        "projects": [{"harvest_project_id": 14307913}],
    })).json()

    rows = (await client.get(
        f"/billing/harvest/projects?client_id={ACME}&exclude_group_id={created['id']}"
    )).json()
    by_id = {r["harvest_id"]: r for r in rows}
    assert by_id[14307913]["billing_group_id"] is None


async def test_catalog_requires_auth(unauthed_client):
    for path in ("/billing/harvest/clients", "/billing/harvest/projects"):
        res = await unauthed_client.get(path)
        assert res.status_code in (401, 403), f"{path} was not protected"


# ── Placeholder resolution endpoints ────────────────────────────────────────


async def _placeholder_run(client, pool):
    """A recurring group with one fixed line and one placeholder, planned."""
    await _seed(pool)
    res = await client.post("/billing/groups", json={
        "name": "Acme — Managed",
        "harvest_client_id": ACME,
        "billing_type": "recurring_monthly",
        "billing_timing": "advance",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": 14307913}],
        "recurring_items": [
            {
                "harvest_project_id": 14307913, "description": "Management fee",
                "quantity": 1, "unit_price": 1500, "kind": "Service",
                "is_placeholder": False,
            },
            {
                "harvest_project_id": 14307913, "description": "Hosting",
                "quantity": 1, "unit_price": 0, "kind": "Service",
                "is_placeholder": True,
            },
        ],
    })
    assert res.status_code == 201, res.text

    res = await client.post("/billing/runs", json={"run_month": "2026-08-01"})
    assert res.status_code == 201, res.text
    run = res.json()
    item = next(
        i for i in run["items"] if i["billing_group_name"] == "Acme — Managed"
    )
    line_id = next(
        li["recurring_line_item_id"] for li in item["estimated_line_items"]
        if li["label"] == "Hosting"
    )
    return run, item, line_id


def _path(run, item, line_id):
    return f"/billing/runs/{run['id']}/items/{item['id']}/placeholders/{line_id}"


async def test_pricing_a_placeholder_returns_the_fresh_run(client):
    pool = await get_pool()
    run, item, line_id = await _placeholder_run(client, pool)

    res = await client.post(
        _path(run, item, line_id),
        json={"resolution": "amount", "unit_price": 1240},
    )
    assert res.status_code == 200, res.text

    fresh = next(i for i in res.json()["items"] if i["id"] == item["id"])
    assert fresh["planned_amount"] == 2740
    hosting = next(
        li for li in fresh["planned_payload"]["line_items"]
        if li["description"] == "Hosting"
    )
    assert hosting["unit_price"] == 1240


async def test_omitting_a_placeholder_drops_it_from_the_payload(client):
    pool = await get_pool()
    run, item, line_id = await _placeholder_run(client, pool)

    res = await client.post(
        _path(run, item, line_id),
        json={"resolution": "omitted", "note": "no overage in August"},
    )
    assert res.status_code == 200, res.text

    fresh = next(i for i in res.json()["items"] if i["id"] == item["id"])
    assert fresh["planned_amount"] == 1500
    assert [
        li["description"] for li in fresh["planned_payload"]["line_items"]
    ] == ["Management fee"]


async def test_clearing_a_placeholder_returns_it_to_undecided(client):
    pool = await get_pool()
    run, item, line_id = await _placeholder_run(client, pool)
    await client.post(
        _path(run, item, line_id),
        json={"resolution": "amount", "unit_price": 1240},
    )

    res = await client.delete(_path(run, item, line_id))
    assert res.status_code == 200, res.text

    fresh = next(i for i in res.json()["items"] if i["id"] == item["id"])
    assert fresh["planned_amount"] == 1500
    hosting = next(
        li for li in fresh["estimated_line_items"] if li["label"] == "Hosting"
    )
    assert hosting["placeholder_state"] == "unresolved"


async def test_an_amount_with_no_price_is_a_409(client):
    pool = await get_pool()
    run, item, line_id = await _placeholder_run(client, pool)

    res = await client.post(_path(run, item, line_id), json={"resolution": "amount"})
    assert res.status_code == 409
    assert "unit price" in res.json()["detail"]


async def test_an_unknown_line_is_a_404(client):
    from uuid import uuid4
    pool = await get_pool()
    run, item, _ = await _placeholder_run(client, pool)

    res = await client.post(
        _path(run, item, uuid4()),
        json={"resolution": "amount", "unit_price": 100},
    )
    assert res.status_code == 404


async def test_a_nonsense_resolution_is_a_422(client):
    """Caught by the request model, before any service logic runs."""
    pool = await get_pool()
    run, item, line_id = await _placeholder_run(client, pool)

    res = await client.post(_path(run, item, line_id), json={"resolution": "maybe"})
    assert res.status_code == 422


async def test_an_abandoned_run_is_a_409(client):
    pool = await get_pool()
    run, item, line_id = await _placeholder_run(client, pool)
    await client.post(f"/billing/runs/{run['id']}/abandon")

    res = await client.post(
        _path(run, item, line_id),
        json={"resolution": "amount", "unit_price": 1240},
    )
    assert res.status_code == 409
    assert "under review" in res.json()["detail"]


async def test_placeholder_endpoints_require_auth(unauthed_client):
    from uuid import uuid4
    path = f"/billing/runs/{uuid4()}/items/{uuid4()}/placeholders/{uuid4()}"

    res = await unauthed_client.post(path, json={"resolution": "omitted"})
    assert res.status_code in (401, 403)
    res = await unauthed_client.delete(path)
    assert res.status_code in (401, 403)
