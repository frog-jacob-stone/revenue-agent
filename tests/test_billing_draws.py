"""Fixed-fee draws — release-gated, billed one at a time, off-cycle.

The two things that must hold no matter what: a draw cannot bill without a
human confirming delivery, and a draw cannot bill twice.
"""
from __future__ import annotations

from datetime import date, timedelta

import asyncpg
import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import draws, harvest_snapshot, planner
from app.services.billing import groups as groups_service
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735774
ERP = 14308912
PORTAL = 14308913

TODAY = date.today()
LAST_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=15)
NEXT_MONTH = (TODAY.replace(day=28) + timedelta(days=10)).replace(day=15)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Ridgeway Industrial")
    f.add_project(ERP, "Ridgeway ERP", client_id=CLIENT, is_fixed_fee=True)
    f.add_project(PORTAL, "Ridgeway Portal", client_id=CLIENT, is_fixed_fee=True)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


def _draw(description: str, amount: float, when: date, **over):
    return {
        "harvest_project_id": ERP,
        "description": description,
        "amount": amount,
        "kind": "Service",
        "scheduled_date": when,
        **over,
    }


async def _group(pool, items, **over):
    return await groups_service.create_group(pool, {
        "name": "Ridgeway ERP — Implementation",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "custom",
        "custom_net_days": 10,
        "subject_template": "{client_name} — {draw_description}",
        "projects": [
            {"harvest_project_id": ERP},
            {"harvest_project_id": PORTAL},
        ],
        "schedule_items": items,
        **over,
    })


async def _ids(pool, group_id):
    rows = await draws.list_draws(pool, group_id=group_id)
    return {r["description"]: r for r in rows}


# ── The gate ────────────────────────────────────────────────────────────────


async def test_a_draw_starts_pending_and_cannot_be_billed(fake):
    """The scheduled date is a commitment, not a trigger."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1 — Signing", 37500, LAST_MONTH)])
    draw = (await _ids(pool, group["id"]))["Draw 1 — Signing"]

    assert draw["state"] == "pending"
    assert draw["released_at"] is None

    # The preview still renders — an operator may want to see what a draw would
    # bill before confirming delivery — but it is not billable.
    preview = await draws.preview_draw_invoice(pool, draw["id"])
    assert preview["billable"] is False
    assert preview["state"] == "pending"


async def test_confirming_delivery_makes_it_billable(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1 — Signing", 37500, LAST_MONTH)])
    draw = (await _ids(pool, group["id"]))["Draw 1 — Signing"]

    released = await draws.set_release(
        pool, draw["id"], released=True, actor="jacob@frogslayer.com"
    )
    assert released["state"] == "ready"
    assert released["released_by"] == "jacob@frogslayer.com"
    assert released["released_at"] is not None


async def test_delivery_confirmation_can_be_withdrawn(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1 — Signing", 37500, LAST_MONTH)])
    draw = (await _ids(pool, group["id"]))["Draw 1 — Signing"]

    await draws.set_release(pool, draw["id"], released=True)
    back = await draws.set_release(pool, draw["id"], released=False)
    assert back["state"] == "pending"
    assert back["released_by"] is None


async def test_withdrawal_is_refused_once_execution_has_started(fake):
    """Otherwise the ledger would hold a live row for a draw the system now
    considers undelivered."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1 — Signing", 37500, LAST_MONTH)])
    draw = (await _ids(pool, group["id"]))["Draw 1 — Signing"]

    await draws.set_release(pool, draw["id"], released=True)
    await _begin_execution(pool, draw["id"], group["id"])

    with pytest.raises(draws.DrawError, match="in flight"):
        await draws.set_release(pool, draw["id"], released=False)


# ── Billing ─────────────────────────────────────────────────────────────────


async def test_the_preview_is_one_line_for_the_scheduled_amount(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 3 — UAT sign-off", 50000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 3 — UAT sign-off"]
    await draws.set_release(pool, draw["id"], released=True)

    preview = await draws.preview_draw_invoice(
        pool, draw["id"], issue_date=date(2026, 9, 14)
    )

    assert preview["billable"] is True
    assert preview["amount"] == pytest.approx(50000.0)
    assert len(preview["estimated_line_items"]) == 1

    payload = preview["planned_payload"]
    assert payload["line_items"] == [{
        "project_id": ERP,
        "kind": "Service",
        "description": "Draw 3 — UAT sign-off",
        "quantity": 1,
        "unit_price": 50000.0,
    }]
    # {draw_description} is what identifies a draw invoice — it covers no period.
    assert payload["subject"] == "Ridgeway Industrial — Draw 3 — UAT sign-off"
    assert payload["issue_date"] == "2026-09-14"
    assert payload["payment_term"] == "custom"
    assert payload["due_date"] == "2026-09-24"


async def test_draw_number_is_a_subject_token(fake):
    """A fixed-fee client recognises "Draw 2 of the schedule", not a date."""
    pool = await get_pool()
    group = await _group(
        pool,
        [_draw("Signing", 10000, TODAY), _draw("UAT", 20000, NEXT_MONTH)],
        subject_template="{client_name} — Draw {draw_number}: {draw_description}",
    )
    by_desc = await _ids(pool, group["id"])

    first = await draws.preview_draw_invoice(pool, by_desc["Signing"]["id"])
    second = await draws.preview_draw_invoice(pool, by_desc["UAT"]["id"])
    assert first["subject"] == "Ridgeway Industrial — Draw 1: Signing"
    assert second["subject"] == "Ridgeway Industrial — Draw 2: UAT"


async def test_draw_count_is_the_length_of_the_schedule(fake):
    pool = await get_pool()
    group = await _group(
        pool,
        [
            _draw("Signing", 10000, TODAY),
            _draw("UAT", 20000, NEXT_MONTH),
            _draw("Go-live", 30000, NEXT_MONTH),
        ],
        subject_template="Draw {draw_number} of {draw_count}",
    )
    by_desc = await _ids(pool, group["id"])

    assert (await draws.preview_draw_invoice(pool, by_desc["UAT"]["id"]))["subject"] \
        == "Draw 2 of 3"


async def test_draw_count_grows_when_the_schedule_is_extended(fake):
    """Both tokens read the schedule as it stands now, so adding a draw
    re-labels every invoice not yet created."""
    pool = await get_pool()
    group = await _group(
        pool,
        [_draw("Signing", 10000, TODAY)],
        subject_template="Draw {draw_number} of {draw_count}",
    )
    signing = (await _ids(pool, group["id"]))["Signing"]
    assert (await draws.preview_draw_invoice(pool, signing["id"]))["subject"] \
        == "Draw 1 of 1"

    await groups_service.update_group(pool, group["id"], {
        "schedule_items": [
            {**_draw("Signing", 10000, TODAY), "id": str(signing["id"])},
            _draw("UAT", 20000, NEXT_MONTH),
        ],
    })
    assert (await draws.preview_draw_invoice(pool, signing["id"]))["subject"] \
        == "Draw 1 of 2"


async def test_draw_number_follows_the_schedule_order(fake):
    """`sequence` is authoritative from list position, so inserting a draw
    ahead of another re-numbers what has not billed yet."""
    pool = await get_pool()
    group = await _group(
        pool,
        [_draw("UAT", 20000, NEXT_MONTH)],
        subject_template="Draw {draw_number}",
    )
    uat = (await _ids(pool, group["id"]))["UAT"]

    await groups_service.update_group(pool, group["id"], {
        "schedule_items": [
            _draw("Signing", 10000, TODAY),
            {**_draw("UAT", 20000, NEXT_MONTH), "id": str(uat["id"])},
        ],
    })

    after = await _ids(pool, group["id"])
    assert (await draws.preview_draw_invoice(pool, after["UAT"]["id"]))["subject"] \
        == "Draw 2"


async def test_a_draw_invoice_has_no_service_period(fake):
    """Arrears/advance answers "which month's work?" — a draw covers no month."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)

    preview = await draws.preview_draw_invoice(pool, draw["id"])
    assert "period_start" not in preview["planned_payload"]
    assert "period_end" not in preview["planned_payload"]


async def test_a_draw_cannot_be_billed_twice(fake):
    """Structural, not conventional: the second live ledger row is refused by
    the database, whatever the calling code believes."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    await _begin_execution(pool, draw["id"], group["id"])

    # The draw has left the billable queue entirely...
    assert (await _ids(pool, group["id"]))["Draw 1"]["state"] == "in_flight"
    assert await draws.list_draws(pool, state="ready") == []

    # ...and the database refuses a second live row regardless. Inside a
    # savepoint, because the violation aborts the surrounding transaction and
    # the whole test runs in one.
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await _begin_execution(pool, draw["id"], group["id"])


async def test_two_draws_in_one_month_both_bill(fake):
    """The regression the original C6 index would have caused.

    One live ledger row per group per *month* is right when the duplication
    risk is billing the same period twice. For draws the unit is the draw —
    two milestones landing in one month is ordinary.
    """
    pool = await get_pool()
    group = await _group(pool, [
        _draw("Draw 3 — UAT", 50000, TODAY),
        _draw("Draw 4 — Go-live", 25000, TODAY),
    ])
    by_desc = await _ids(pool, group["id"])
    for d in by_desc.values():
        await draws.set_release(pool, d["id"], released=True)

    first = await _begin_execution(pool, by_desc["Draw 3 — UAT"]["id"], group["id"])
    second = await _begin_execution(pool, by_desc["Draw 4 — Go-live"]["id"], group["id"])
    assert first != second


async def test_billing_a_draw_on_an_inactive_group_is_refused(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    await groups_service.deactivate_group(pool, group["id"])

    assert (await draws.preview_draw_invoice(pool, draw["id"]))["billable"] is False


# ── Editing the schedule ────────────────────────────────────────────────────


async def test_editing_the_schedule_preserves_release_state(fake):
    """A slipped date is edited routinely. Delete-and-reinsert would silently
    un-confirm every delivered draw."""
    pool = await get_pool()
    group = await _group(pool, [
        _draw("Draw 1", 1000, LAST_MONTH),
        _draw("Draw 2", 2000, NEXT_MONTH),
    ])
    by_desc = await _ids(pool, group["id"])
    await draws.set_release(pool, by_desc["Draw 1"]["id"], released=True)

    # Push Draw 2 out a month — the kind of edit that happens all the time.
    await groups_service.update_group(pool, group["id"], {
        "schedule_items": [
            {**_draw("Draw 1", 1000, LAST_MONTH), "id": str(by_desc["Draw 1"]["id"])},
            {**_draw("Draw 2", 2000, NEXT_MONTH + timedelta(days=30)),
             "id": str(by_desc["Draw 2"]["id"])},
        ],
    })

    after = await _ids(pool, group["id"])
    assert after["Draw 1"]["state"] == "ready"
    assert after["Draw 1"]["released_at"] is not None
    assert after["Draw 1"]["id"] == by_desc["Draw 1"]["id"]


async def test_an_invoiced_draw_cannot_be_edited(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])
    await _mark_created(pool, run_id, draw["id"])

    with pytest.raises(groups_service.BillingConfigError, match="already been invoiced"):
        await groups_service.update_group(pool, group["id"], {
            "schedule_items": [
                {**_draw("Draw 1", 9999, TODAY), "id": str(draw["id"])},
            ],
        })


async def test_an_invoiced_draw_cannot_be_removed(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])
    await _mark_created(pool, run_id, draw["id"])

    with pytest.raises(groups_service.BillingConfigError, match="cannot be removed"):
        await groups_service.update_group(pool, group["id"], {"schedule_items": []})


async def test_a_pending_draw_can_be_removed(fake):
    pool = await get_pool()
    group = await _group(pool, [
        _draw("Draw 1", 1000, TODAY),
        _draw("Draw 2", 2000, NEXT_MONTH),
    ])
    by_desc = await _ids(pool, group["id"])

    await groups_service.update_group(pool, group["id"], {
        "schedule_items": [
            {**_draw("Draw 1", 1000, TODAY), "id": str(by_desc["Draw 1"]["id"])},
        ],
    })
    assert set(await _ids(pool, group["id"])) == {"Draw 1"}


async def _begin_execution(pool, draw_id, group_id):
    """Stand in for Phase 3's write path, which does not exist yet.

    It writes the ledger row `in_flight` immediately before the POST to Harvest,
    per the §8 protocol — the row is the record that a request may have landed.
    Nothing in the app can produce this state today, so tests that need it have
    to build it, and building it here keeps the shape honest.
    """
    run_id = await pool.fetchval(
        "INSERT INTO billing_runs (run_month, status, kind) "
        "VALUES ($1, 'executing', 'draw') RETURNING id",
        date.today().replace(day=1),
    )
    await pool.execute(
        """
        INSERT INTO billing_run_items (
            billing_run_id, billing_group_id, run_month, status,
            planned_amount, planned_payload, fixed_fee_schedule_item_id
        )
        VALUES ($1,$2,$3,'in_flight',0,'{}'::jsonb,$4)
        """,
        run_id, group_id, date.today().replace(day=1), draw_id,
    )
    return run_id


async def _mark_created(pool, run_id, draw_id):
    """Finish that simulated execution: the draft exists and the draw is
    consumed, both inside one transaction in the real thing."""
    await pool.execute(
        "UPDATE billing_run_items SET status = 'created', harvest_invoice_id = 999, "
        "harvest_invoice_number = '1042' WHERE billing_run_id = $1",
        run_id,
    )
    await pool.execute(
        "UPDATE fixed_fee_schedule_items SET invoiced_run_id = $2 WHERE id = $1",
        draw_id, run_id,
    )


# ── Validation ──────────────────────────────────────────────────────────────


async def test_a_draw_must_target_a_project_in_the_group(fake):
    pool = await get_pool()
    with pytest.raises(groups_service.BillingConfigError, match="must target a project"):
        await groups_service.create_group(pool, {
            "name": "Ridgeway — ERP only",
            "harvest_client_id": CLIENT,
            "billing_type": "fixed_fee_schedule",
            "projects": [{"harvest_project_id": ERP}],
            "schedule_items": [_draw("Wrong project", 100, TODAY,
                                     harvest_project_id=PORTAL)],
        })


async def test_an_invalid_fee_type_is_rejected_at_save(fake):
    pool = await get_pool()
    with pytest.raises(groups_service.BillingConfigError, match="does not exist"):
        await _group(pool, [_draw("Bad kind", 100, TODAY, kind="Nonsense")])


# ── The monthly run ─────────────────────────────────────────────────────────


async def test_the_monthly_run_skips_draw_groups_with_an_honest_reason(fake):
    pool = await get_pool()
    await _group(pool, [_draw("Draw 1", 1000, NEXT_MONTH)])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=TODAY.replace(day=1))
    )
    item = next(i for i in run["items"] if i["status"] == "skipped")
    assert "billed individually from the Draws tab" in item["skip_reason"]


async def test_an_overdue_draw_is_surfaced_on_the_monthly_run(fake):
    """The run bills nothing here, but a delivered milestone nobody invoices is
    exactly what this system exists to catch."""
    pool = await get_pool()
    await _group(pool, [_draw("Draw 2 — Design system", 30000, LAST_MONTH)])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=TODAY.replace(day=1))
    )
    item = next(i for i in run["items"] if i["status"] == "skipped")
    codes = {f["code"] for f in item["flags"]}
    assert "DRAW_OVERDUE" in codes
    assert "DRAWS_AWAITING_RELEASE" in codes


async def test_a_future_draw_is_not_overdue(fake):
    pool = await get_pool()
    await _group(pool, [_draw("Draw 1", 1000, NEXT_MONTH)])

    run = await planner.get_run(
        pool, await planner.plan_run(pool, settings, run_month=TODAY.replace(day=1))
    )
    item = next(i for i in run["items"] if i["status"] == "skipped")
    assert "DRAW_OVERDUE" not in {f["code"] for f in item["flags"]}


async def test_replanning_a_month_does_not_touch_a_draws_ledger_row(fake):
    """Draw rows hold their own index, not the month's. A monthly re-plan
    sweeping them up would abandon an invoice already on its way to Harvest."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 3 — UAT", 50000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 3 — UAT"]
    await draws.set_release(pool, draw["id"], released=True)
    draw_run = await _begin_execution(pool, draw["id"], group["id"])

    await planner.plan_run(pool, settings, run_month=TODAY.replace(day=1))

    still_live = await planner.get_run(pool, draw_run)
    assert still_live["items"][0]["status"] == "in_flight"


async def test_draw_runs_are_excluded_from_the_monthly_run_list(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    await _begin_execution(pool, draw["id"], group["id"])
    await planner.plan_run(pool, settings, run_month=TODAY.replace(day=1))

    monthly = await planner.list_runs(pool)
    assert [r["kind"] for r in monthly] == ["monthly"]
    assert len(await planner.list_runs(pool, kind=None)) == 2


# ── Queue ───────────────────────────────────────────────────────────────────


async def test_the_queue_filters_by_state(fake):
    pool = await get_pool()
    group = await _group(pool, [
        _draw("Draw 1", 1000, LAST_MONTH),
        _draw("Draw 2", 2000, NEXT_MONTH),
    ])
    by_desc = await _ids(pool, group["id"])
    await draws.set_release(pool, by_desc["Draw 1"]["id"], released=True)

    ready = await draws.list_draws(pool, state="ready")
    pending = await draws.list_draws(pool, state="pending")
    assert [d["description"] for d in ready] == ["Draw 1"]
    assert [d["description"] for d in pending] == ["Draw 2"]


async def test_the_queue_carries_client_and_project_context(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    row = (await draws.list_draws(pool, group_id=group["id"]))[0]
    assert row["harvest_client_name"] == "Ridgeway Industrial"
    assert row["harvest_project_name"] == "Ridgeway ERP"
    assert row["billing_group_name"] == "Ridgeway ERP — Implementation"


# ── In flight: the state between ready and invoiced ─────────────────────────
#
# A draw with a live ledger row is mid-write. Nothing in the app can produce
# this today — it arrives with Phase 3 — but the state is derived now so that a
# draw being billed can never be offered for billing again, and so a half-
# completed write is visible rather than silently gone from the queue.


async def test_a_live_ledger_row_takes_a_draw_out_of_the_billable_queue(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])

    after = (await _ids(pool, group["id"]))["Draw 1"]
    assert after["state"] == "in_flight"
    assert after["live_run_id"] == run_id
    assert after["invoiced_run_id"] is None
    assert await draws.list_draws(pool, state="ready") == []
    assert [d["description"] for d in await draws.list_draws(pool, state="in_flight")] \
        == ["Draw 1"]


async def test_a_failed_attempt_returns_the_draw_to_ready(fake):
    """`failed` is excluded from the partial index, so a draw whose create
    failed outright is billable again."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])

    await pool.execute(
        "UPDATE billing_run_items SET status = 'failed' WHERE billing_run_id = $1",
        run_id,
    )

    after = (await _ids(pool, group["id"]))["Draw 1"]
    assert after["state"] == "ready"
    assert after["live_run_id"] is None


async def test_an_invoiced_draw_keeps_a_link_to_its_invoice(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])
    await _mark_created(pool, run_id, draw["id"])

    after = (await _ids(pool, group["id"]))["Draw 1"]
    assert after["state"] == "invoiced"
    assert after["live_run_id"] == run_id
    assert after["harvest_invoice_number"] == "1042"


async def test_a_draw_being_billed_cannot_be_edited(fake):
    """Execution has begun against these exact values."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    await _begin_execution(pool, draw["id"], group["id"])

    with pytest.raises(groups_service.BillingConfigError, match="in flight"):
        await groups_service.update_group(pool, group["id"], {
            "schedule_items": [{**_draw("Draw 1", 9999, TODAY), "id": str(draw["id"])}],
        })


async def test_a_draw_being_billed_may_still_be_re_scheduled(fake):
    """Only the fields that reach the payload are locked — a date is a note."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    await _begin_execution(pool, draw["id"], group["id"])

    await groups_service.update_group(pool, group["id"], {
        "schedule_items": [
            {**_draw("Draw 1", 1000, NEXT_MONTH), "id": str(draw["id"])},
        ],
    })
    assert (await _ids(pool, group["id"]))["Draw 1"]["scheduled_date"] == NEXT_MONTH


async def test_a_draw_being_billed_cannot_be_removed(fake):
    """Without this the delete hits `on delete restrict` and 500s."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    await _begin_execution(pool, draw["id"], group["id"])

    with pytest.raises(groups_service.BillingConfigError, match="in flight"):
        await groups_service.update_group(pool, group["id"], {"schedule_items": []})


async def test_removing_a_draw_with_billing_history_is_refused_cleanly(fake):
    """A failed attempt leaves a ledger row that still references the draw, so
    the delete cannot succeed. It must be a 400 with an explanation, not a 500."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])
    await pool.execute(
        "UPDATE billing_run_items SET status = 'failed' WHERE billing_run_id = $1",
        run_id,
    )

    with pytest.raises(groups_service.BillingConfigError, match="billing history"):
        await groups_service.update_group(pool, group["id"], {"schedule_items": []})


async def test_the_group_page_sees_the_live_ledger_row(fake):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)
    run_id = await _begin_execution(pool, draw["id"], group["id"])

    fresh = await groups_service.get_group(pool, group["id"])
    assert fresh["schedule_items"][0]["live_run_id"] == run_id


# ── Router ──────────────────────────────────────────────────────────────────


async def test_draw_endpoints_require_auth(fake, unauthed_client):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]

    for path in ("/billing/draws", f"/billing/draws/{draw['id']}/preview"):
        res = await unauthed_client.get(path)
        assert res.status_code in (401, 403), f"GET {path} was not protected"

    res = await unauthed_client.post(
        f"/billing/draws/{draw['id']}/release", json={"released": True}
    )
    assert res.status_code in (401, 403)


async def test_release_then_preview_through_the_api(fake, client):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 3 — UAT", 50000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 3 — UAT"]

    res = await client.post(
        f"/billing/draws/{draw['id']}/release", json={"released": True}
    )
    assert res.status_code == 200
    assert res.json()["state"] == "ready"
    assert res.json()["released_by"]

    res = await client.get(f"/billing/draws/{draw['id']}/preview")
    assert res.status_code == 200
    body = res.json()
    assert body["billable"] is True
    assert body["amount"] == 50000.0
    assert body["planned_payload"]["line_items"][0]["unit_price"] == 50000.0


async def test_the_preview_writes_nothing(fake, client):
    """The whole point of a GET here: looking at an invoice leaves no trace and
    nothing to unwind."""
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]
    await draws.set_release(pool, draw["id"], released=True)

    for _ in range(3):
        assert (await client.get(f"/billing/draws/{draw['id']}/preview")).status_code == 200

    assert await pool.fetchval("SELECT count(*) FROM billing_runs") == 0
    assert await pool.fetchval("SELECT count(*) FROM billing_run_items") == 0
    assert (await _ids(pool, group["id"]))["Draw 1"]["state"] == "ready"


async def test_the_api_reports_an_unconfirmed_draw_as_not_billable(fake, client):
    pool = await get_pool()
    group = await _group(pool, [_draw("Draw 1", 1000, TODAY)])
    draw = (await _ids(pool, group["id"]))["Draw 1"]

    res = await client.get(f"/billing/draws/{draw['id']}/preview")
    assert res.status_code == 200
    assert res.json()["billable"] is False
    assert res.json()["state"] == "pending"
