"""The write endpoints — status codes, auth, and what the operator is told.

The status code is the contract here. §8 distinguishes "nothing happened" from
"it failed" from "we don't know", and an operator who cannot tell those apart
from the response will either re-click (duplicate invoice) or walk away from a
locked draw.
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from app.config import settings
from app.db import get_pool
from app.integrations import harvest
from app.services.billing import draws, harvest_snapshot
from app.services.billing import groups as groups_service
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735774
ERP = 14308912
LAST_MONTH = (date.today().replace(day=1) - timedelta(days=1)).replace(day=15)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Ridgeway Industrial")
    f.add_project(ERP, "Ridgeway ERP", client_id=CLIENT, is_fixed_fee=True)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


async def _ready_draw(pool, *, release: bool = True):
    group = await groups_service.create_group(pool, {
        "name": "Ridgeway ERP — Implementation",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "subject_template": "{client_name} — {draw_description}",
        "projects": [{"harvest_project_id": ERP}],
        "schedule_items": [{
            "harvest_project_id": ERP,
            "description": "Draw 1 — Signing",
            "amount": 37500,
            "kind": "Service",
            "scheduled_date": LAST_MONTH,
        }],
    })
    draw_id = (await draws.list_draws(pool, group_id=group["id"]))[0]["id"]
    if release:
        await draws.set_release(pool, draw_id, released=True, actor="jacob@f.com")
    return draw_id


# ── Auth ────────────────────────────────────────────────────────────────────


async def test_write_endpoints_require_auth(unauthed_client):
    """The Harvest write must never be reachable unauthenticated."""
    zero = "00000000-0000-0000-0000-000000000000"
    res = await unauthed_client.get("/billing/in-flight")
    assert res.status_code in (401, 403), "GET /billing/in-flight was not protected"

    for path in (
        f"/billing/draws/{zero}/invoice",
        f"/billing/runs/{zero}/items/{zero}/resolve",
    ):
        res = await unauthed_client.post(path, json={})
        assert res.status_code in (401, 403), f"POST {path} was not protected"


# ── 200 ─────────────────────────────────────────────────────────────────────


async def test_invoicing_a_ready_draw_returns_the_invoice(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)

    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["harvest_invoice_id"] == fake.created_invoices[0]["invoice"]["id"]
    assert body["harvest_invoice_number"].startswith("INV-")
    assert body["planned_amount"] == 37500.0
    assert body["variance"] == 0.0

    # And the draw is now visibly consumed through the read API.
    listed = await client.get("/billing/draws")
    assert [d["state"] for d in listed.json()] == ["invoiced"]


async def test_the_response_carries_the_dates_actually_used(client, fake, monkeypatch):
    """The UI shows these instead of its own preview, which may be days old."""
    pool = await get_pool()
    draw_id = await _ready_draw(pool)

    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 12))
    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    body = res.json()
    assert body["issue_date"] == "2026-08-12"
    # net 30 on this fixture; Harvest derives it, we report the same arithmetic.
    assert body["due_date"] == "2026-09-11"
    assert body["payment_term"] == "net 30"


async def test_a_preview_from_a_previous_day_does_not_date_the_invoice(
    client, fake, monkeypatch,
):
    """The operator's case: look on the 10th, click on the 12th.

    The preview is what authorized the write, but it is not what dates it — the
    server recomputes, so the client's payment clock starts when the draft exists.
    """
    pool = await get_pool()
    draw_id = await _ready_draw(pool)

    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 10))
    seen = await client.get(f"/billing/draws/{draw_id}/preview")
    assert seen.json()["issue_date"] == "2026-08-10"

    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 12))
    created = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    assert created.json()["issue_date"] == "2026-08-12"
    assert fake.created_invoices[0]["payload"]["issue_date"] == "2026-08-12"


async def test_the_authenticated_user_is_the_recorded_actor(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)

    await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    actors = await pool.fetch(
        "SELECT DISTINCT actor FROM audit_log WHERE event_type LIKE 'billing.invoice%'"
    )
    assert len(actors) == 1
    # Whoever the test token belongs to — the point is that it is not "system".
    assert actors[0]["actor"] not in (None, "", "system")


# ── 409: refused, nothing attempted ─────────────────────────────────────────


async def test_a_billed_draw_is_still_listed_with_its_invoice(client, fake):
    """The disappearing-draw bug, through the API the UI actually reads.

    A billed draw leaves both queues. If `GET /billing/draws` did not carry the
    invoice identity there would be no screen anywhere showing what the system
    created — which is how the first live invoice went unconfirmed.
    """
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    created = (await client.post(
        f"/billing/draws/{draw_id}/invoice", json={}
    )).json()

    rows = (await client.get("/billing/draws?state=invoiced")).json()

    assert len(rows) == 1
    row = rows[0]
    assert row["harvest_invoice_number"] == created["harvest_invoice_number"]
    assert row["harvest_invoice_id"] == created["harvest_invoice_id"]
    assert row["invoice_issue_date"] == created["issue_date"]
    assert row["invoice_due_date"] == created["due_date"]
    assert row["invoiced_amount"] == created["actual_amount"]
    assert row["invoiced_at"] is not None
    assert row["live_run_id"] == created["billing_run_id"]


async def test_the_billed_list_and_totals(client, fake):
    """The Billed tab's two calls. Kind-agnostic by design — monthly rows will
    appear here unchanged once that execution ships."""
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    created = (await client.post(
        f"/billing/draws/{draw_id}/invoice", json={}
    )).json()

    listed = await client.get("/billing/invoices")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "draw"
    assert rows[0]["harvest_invoice_number"] == created["harvest_invoice_number"]
    assert rows[0]["draw_description"] == "Draw 1 — Signing"

    totals = (await client.get("/billing/invoices/totals")).json()
    assert totals == {
        "count": 1, "draw_count": 1, "monthly_count": 0,
        "total_amount": 37500.0, "unverified_count": 0,
    }


async def test_the_billed_list_rejects_an_unknown_filter(client, fake):
    res = await client.get("/billing/invoices?kind=quarterly")
    assert res.status_code == 400
    assert "Unknown kind" in res.json()["detail"]


async def test_the_billed_list_requires_auth(unauthed_client):
    for path in ("/billing/invoices", "/billing/invoices/totals"):
        res = await unauthed_client.get(path)
        assert res.status_code in (401, 403), f"GET {path} was not protected"


async def test_health_exposes_the_harvest_base_uri(client, fake, monkeypatch):
    """The UI cannot build a link to an invoice without it, and no Harvest
    endpoint provides it."""
    monkeypatch.setattr(settings, "harvest_base_uri", "https://acme.harvestapp.com/")

    res = await client.get("/billing/health?include_time=false")

    # Trailing slash stripped so the UI can concatenate without doubling it.
    assert res.json()["snapshot"]["harvest_base_uri"] == "https://acme.harvestapp.com"


async def test_an_unreleased_draw_is_409(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool, release=False)

    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    assert res.status_code == 409
    assert "not billable" in res.json()["detail"]
    assert fake.created_invoices == []


async def test_a_second_invoice_attempt_is_409(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    assert res.status_code == 409
    assert len(fake.created_invoices) == 1


async def test_a_missing_draw_is_409_not_500(client, fake):
    res = await client.post(
        "/billing/draws/00000000-0000-0000-0000-000000000000/invoice", json={}
    )
    assert res.status_code == 409
    assert "not found" in res.json()["detail"].lower()


# ── 422: Harvest refused ────────────────────────────────────────────────────


async def test_harvest_validation_failure_is_422(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    fake.fail_create_invoice(harvest.HarvestValidationError(
        "Harvest 422", status=422, path="/invoices",
        body={"message": "Project does not belong to client"},
    ))

    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    assert res.status_code == 422
    assert "does not belong" in res.json()["detail"]

    # Nothing was created, so the draw is billable again.
    listed = await client.get("/billing/draws")
    assert listed.json()[0]["state"] == "ready"


# ── 502: unknown outcome ────────────────────────────────────────────────────


async def test_a_timeout_is_502_and_says_how_to_recover(client, fake):
    """The operator must leave this response knowing to go look at Harvest."""
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))

    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "may exist in Harvest" in detail["message"]
    assert "resolve the in-flight row" in detail["remedy"]
    # The ids needed to resolve it come back in the error itself.
    assert detail["billing_run_id"]
    assert detail["billing_run_item_id"]

    listed = await client.get("/billing/draws")
    assert listed.json()[0]["state"] == "in_flight"


# ── Resolution ──────────────────────────────────────────────────────────────


async def _stick(client, fake, draw_id):
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))
    res = await client.post(f"/billing/draws/{draw_id}/invoice", json={})
    return res.json()["detail"]


async def test_in_flight_queue_lists_the_stuck_row(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    await _stick(client, fake, draw_id)

    res = await client.get("/billing/in-flight")

    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["draw_description"] == "Draw 1 — Signing"
    assert rows[0]["planned_amount"] == 37500.0


async def test_in_flight_queue_is_empty_when_all_is_well(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    await client.post(f"/billing/draws/{draw_id}/invoice", json={})

    res = await client.get("/billing/in-flight")
    assert res.json() == []


async def test_linking_resolves_the_row(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    detail = await _stick(client, fake, draw_id)

    res = await client.post(
        f"/billing/runs/{detail['billing_run_id']}/items/"
        f"{detail['billing_run_item_id']}/resolve",
        json={"resolution": "link", "harvest_invoice_id": 4242,
              "harvest_invoice_number": "INV-4242"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "created"
    assert (await client.get("/billing/draws")).json()[0]["state"] == "invoiced"
    assert (await client.get("/billing/in-flight")).json() == []


async def test_resolving_as_failed_frees_the_draw(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    detail = await _stick(client, fake, draw_id)

    res = await client.post(
        f"/billing/runs/{detail['billing_run_id']}/items/"
        f"{detail['billing_run_item_id']}/resolve",
        json={"resolution": "failed"},
    )

    assert res.status_code == 200, res.text
    assert (await client.get("/billing/draws")).json()[0]["state"] == "ready"


async def test_linking_without_an_invoice_id_is_409(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    detail = await _stick(client, fake, draw_id)

    res = await client.post(
        f"/billing/runs/{detail['billing_run_id']}/items/"
        f"{detail['billing_run_item_id']}/resolve",
        json={"resolution": "link"},
    )

    assert res.status_code == 409
    assert "requires the Harvest invoice id" in res.json()["detail"]


async def test_an_unknown_resolution_is_422(client, fake):
    """Rejected by the schema, before any service code runs."""
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    detail = await _stick(client, fake, draw_id)

    res = await client.post(
        f"/billing/runs/{detail['billing_run_id']}/items/"
        f"{detail['billing_run_item_id']}/resolve",
        json={"resolution": "probably_fine"},
    )

    assert res.status_code == 422


async def test_resolving_a_row_that_is_not_in_flight_is_409(client, fake):
    pool = await get_pool()
    draw_id = await _ready_draw(pool)
    created = (await client.post(
        f"/billing/draws/{draw_id}/invoice", json={}
    )).json()

    res = await client.post(
        f"/billing/runs/{created['billing_run_id']}/items/"
        f"{created['billing_run_item_id']}/resolve",
        json={"resolution": "failed"},
    )

    assert res.status_code == 409
    assert "not 'in_flight'" in res.json()["detail"]


async def test_resolving_a_nonexistent_row_is_404(client, fake):
    zero = "00000000-0000-0000-0000-000000000000"
    res = await client.post(
        f"/billing/runs/{zero}/items/{zero}/resolve", json={"resolution": "failed"}
    )
    assert res.status_code == 404
