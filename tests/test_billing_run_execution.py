"""Monthly-run execution, end to end (PRD §8, §3.2).

The single-draw path is covered in `test_billing_draw_execution.py`; this is its
many-invoice counterpart, and the things that only a *sequence* of writes can
get wrong:

    201 on every group          → all created, run completed
    422 on one                  → that one failed, the rest still created
    timeout on one              → in_flight, and the run STOPS
    resolve, then click again   → resumes where it left off

The assertion that matters most here is not a status — it is
`len(fake.created_invoices)`. Harvest has no idempotency keys, so every extra
POST is a second real invoice at a real client.

The other load-bearing one is that the body reaching `create_invoice` still
carries `line_items_import`. That is what makes Harvest mark the underlying
time entries billed; a payload that computed its own `line_items` would bill a
client for hours Harvest still reported as uninvoiced, ready to be billed
again next month. `FakeHarvest.create_invoice` models that behaviour, so the
check is real rather than a string comparison.
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from app.config import settings
from app.db import get_pool
from app.integrations import harvest
from app.services.billing import (
    execute,
    harvest_snapshot,
    inflight,
    planner,
    review,
)
from app.services.billing import groups as groups_service
from tests.fakes.harvest import FakeHarvest

ACME = 5735774
NORTHWIND = 5735801

PLATFORM = 14307913
MOBILE = 14307914
LAB = 14307915
NW_DATA = 14308221

AUGUST = date(2026, 8, 1)
JULY_START, JULY_END = "2026-07-01", "2026-07-31"
ACTOR = "jacob@frogslayer.com"


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(ACME, "Acme Corp")
    f.add_client(NORTHWIND, "Northwind Industrial")
    f.add_project(PLATFORM, "Acme Platform", client_id=ACME)
    f.add_project(MOBILE, "Acme Mobile", client_id=ACME)
    f.add_project(LAB, "Acme Innovation Lab", client_id=ACME)
    f.add_project(NW_DATA, "Northwind Data Platform", client_id=NORTHWIND)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


async def _tm_group(pool, name, client_id, project_ids, **over):
    return await groups_service.create_group(pool, {
        "name": name,
        "harvest_client_id": client_id,
        "billing_type": "time_and_materials",
        "time_summary_type": "task",
        "projects": [{"harvest_project_id": p} for p in project_ids],
        **over,
    })


async def _planned_and_approved(pool, fake, *, groups: int = 3):
    """A run with N approved T&M groups — the state the button acts on."""
    specs = [
        ("A — Platform", ACME, PLATFORM, 10),
        ("B — Mobile", ACME, MOBILE, 20),
        ("C — Northwind", NORTHWIND, NW_DATA, 30),
    ][:groups]
    for name, client, project, hours in specs:
        fake.add_time(project, spent_date="2026-07-06", hours=hours, rate=185)
        await _tm_group(pool, name, client, [project])

    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    await review.set_all_approvals(pool, run_id, approved=True, actor=ACTOR)
    return run_id


async def _items(pool, run_id):
    rows = await pool.fetch(
        "SELECT i.*, g.name AS group_name FROM billing_run_items i "
        "JOIN billing_groups g ON g.id = i.billing_group_id "
        "WHERE i.billing_run_id = $1 ORDER BY lower(g.name)",
        run_id,
    )
    return [dict(r) for r in rows]


async def _run_status(pool, run_id):
    return await pool.fetchval("SELECT status FROM billing_runs WHERE id = $1", run_id)


async def _events(pool, event_type):
    return await pool.fetch(
        "SELECT * FROM audit_log WHERE event_type = $1 ORDER BY id", event_type,
    )


# ── The happy path ──────────────────────────────────────────────────────────


async def test_creates_a_draft_for_every_approved_group(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert len(fake.created_invoices) == 3
    assert result["created"] == 3
    assert result["failed"] == 0
    assert result["halted"] is False
    assert result["status"] == "completed"
    assert await _run_status(pool, run_id) == "completed"

    for item in await _items(pool, run_id):
        assert item["status"] == "created"
        assert item["harvest_invoice_id"] is not None
        assert item["harvest_invoice_number"].startswith("INV-")
        assert item["actual_amount"] is not None
        assert item["variance"] is not None

    created = await _events(pool, "billing.invoice.created")
    assert len(created) == 3
    assert all(e["actor"] == ACTOR for e in created)


async def test_the_body_still_imports_time_rather_than_listing_it(fake):
    """The regression test for the entire point of this pipeline.

    `line_items_import` is the only mechanism that sets `is_billed` on a
    Harvest time entry. If execution ever sends computed `line_items` instead,
    the client is billed and Harvest still shows the hours as uninvoiced.
    """
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    body = fake.created_invoices[0]["payload"]
    assert body["line_items_import"]["project_ids"] == [PLATFORM]
    assert body["line_items_import"]["time"] == {
        "summary_type": "task", "from": JULY_START, "to": JULY_END,
    }
    assert "line_items" not in body
    # And Harvest did what that mechanism promises.
    assert all(e["is_billed"] for e in fake.time_entries[PLATFORM])
    stamped = fake.time_entries[PLATFORM][0]["invoice"]
    assert stamped["id"] == fake.created_invoices[0]["invoice"]["id"]


async def test_the_invoice_is_issued_on_the_period_boundary_and_due_from_today(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    body = fake.created_invoices[0]["payload"]
    expected_due = max(date.today(), date(2026, 7, 31)) + timedelta(days=30)
    assert body["issue_date"] == "2026-07-31"
    assert body["payment_term"] == "custom"
    assert body["due_date"] == expected_due.isoformat()

    # The row records the dates actually sent, not the ones the plan guessed.
    item = (await _items(pool, run_id))[0]
    assert item["due_date"] == expected_due
    assert item["planned_payload"]["due_date"] == expected_due.isoformat()


async def test_recurring_groups_send_literal_line_items(fake):
    """A retainer imports no time, so it keeps its free-form body — and the
    post-write check must not run against it, or every hour on the project
    would read as a leftover, every month."""
    pool = await get_pool()
    group = await _tm_group(
        pool, "Acme — Hosting", ACME, [PLATFORM],
        billing_type="recurring_monthly",
        recurring_items=[{
            "harvest_project_id": PLATFORM,
            "description": "Managed hosting",
            "quantity": 1, "unit_price": 4200.0, "kind": "Service",
        }],
    )
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185)

    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    await review.set_all_approvals(pool, run_id, approved=True, actor=ACTOR)
    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    body = fake.created_invoices[0]["payload"]
    assert "line_items_import" not in body
    assert body["line_items"][0]["unit_price"] == 4200.0
    assert result["items"][0]["unbilled_hours_after"] is None

    item = next(i for i in await _items(pool, run_id)
                if i["billing_group_id"] == group["id"])
    assert item["unbilled_hours_after"] is None


# ── Post-write verification ─────────────────────────────────────────────────


async def test_a_clean_import_leaves_nothing_unbilled(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["items"][0]["unbilled_hours_after"] == 0
    assert result["items"][0]["unbilled_entries_after"] == 0
    assert (await _items(pool, run_id))[0]["unbilled_hours_after"] == 0


async def test_time_harvest_did_not_import_is_recorded_not_failed(fake):
    """The check Jacob asked for. An entry Harvest left behind is reported on
    the row and on the result — and the invoice is still `created`, because it
    is, and calling a real invoice a failure helps nobody."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    fake.add_time(PLATFORM, spent_date="2026-07-20", hours=4.5, rate=185,
                  importable=False)
    await _tm_group(pool, "Acme — Platform", ACME, [PLATFORM])
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    await review.set_all_approvals(pool, run_id, approved=True, actor=ACTOR)

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["status"] == "completed"
    assert result["items"][0]["status"] == "created"
    assert result["items"][0]["unbilled_hours_after"] == 4.5
    assert result["items"][0]["unbilled_entries_after"] == 1

    item = (await _items(pool, run_id))[0]
    assert item["status"] == "created"
    assert float(item["unbilled_hours_after"]) == 4.5


async def test_a_broken_check_leaves_nulls_and_does_not_fail_the_run(fake, monkeypatch):
    """The check runs after the invoice exists and is recorded. Nothing it can
    do may unwind that."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    async def boom(*a, **kw):
        raise httpx.ConnectError("Harvest went away")

    monkeypatch.setattr("app.services.billing.verify.harvest.list_time_entries", boom)
    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["status"] == "completed"
    assert result["created"] == 1
    assert result["items"][0]["unbilled_hours_after"] is None
    assert (await _items(pool, run_id))[0]["unbilled_hours_after"] is None


# ── 4xx: a verdict about one payload, and only that one ─────────────────────


async def test_one_rejected_payload_does_not_stop_the_others(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    # Groups execute alphabetically, so this refuses the second of three.
    original = fake.create_invoice
    calls = {"n": 0}

    async def failing_second(cfg, payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise harvest.HarvestValidationError(
                "422", status=422, path="/invoices",
                body={"message": "client_id is invalid"},
            )
        return await original(cfg, payload)

    fake.create_invoice = failing_second

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert calls["n"] == 3, "a 4xx on one group must not stop the next"
    assert result["created"] == 2
    assert result["failed"] == 1
    assert result["halted"] is False
    assert result["status"] == "failed"
    assert await _run_status(pool, run_id) == "failed"

    statuses = [i["status"] for i in await _items(pool, run_id)]
    assert statuses == ["created", "failed", "created"]
    failed = next(i for i in await _items(pool, run_id) if i["status"] == "failed")
    assert "client_id is invalid" in failed["error_message"]


async def test_a_rate_limit_past_the_cap_fails_that_item_only(fake):
    """A 429 never reached creation, so unlike the unknown outcomes it is safe
    to record as a failure and move on."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=2)
    original = fake.create_invoice
    calls = {"n": 0}

    async def rate_limited_first(cfg, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise harvest.HarvestRateLimited(
                "429", status=429, path="/invoices", retry_after=15.0,
            )
        return await original(cfg, payload)

    fake.create_invoice = rate_limited_first
    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["failed"] == 1
    assert result["created"] == 1
    assert result["halted"] is False
    assert len(fake.created_invoices) == 1


# ── The unknown outcome: the run stops ──────────────────────────────────────


async def _halt_on_second(fake):
    original = fake.create_invoice
    calls = {"n": 0}

    async def timing_out(cfg, payload):
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.TimeoutException("read timeout")
        return await original(cfg, payload)

    fake.create_invoice = timing_out
    return calls


async def test_an_unknown_outcome_halts_the_run(fake):
    """The whole reason the loop is not a `for` over `parallel`. An invoice may
    exist for group 2 and nobody knows; group 3 must not be attempted, because
    continuing turns one unresolvable row into several while Harvest is
    plainly unwell."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    calls = await _halt_on_second(fake)

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert calls["n"] == 2, "the third group must never have been POSTed"
    assert len(fake.created_invoices) == 1
    assert result["halted"] is True
    assert result["created"] == 1
    assert result["status"] == "executing"
    assert result["unknown_item"]["billing_run_id"] == str(run_id)
    assert "may exist in Harvest" in result["unknown_item"]["message"]

    items = await _items(pool, run_id)
    assert [i["status"] for i in items] == ["created", "in_flight", "approved"]
    # `executing` is the accurate answer while one invoice is unaccounted for.
    assert await _run_status(pool, run_id) == "executing"

    unknown = await _events(pool, "billing.invoice.unknown")
    assert len(unknown) == 1
    assert unknown[0]["payload"]["remedy"]


async def test_a_second_click_while_a_row_is_in_flight_is_refused(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    await _halt_on_second(fake)
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    with pytest.raises(execute.RunExecutionError, match="in flight"):
        await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert len(fake.created_invoices) == 1


async def test_resolving_the_halt_lets_the_run_resume(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    await _halt_on_second(fake)
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    stuck = next(i for i in await _items(pool, run_id) if i["status"] == "in_flight")
    resolved = await inflight.resolve_item(
        pool, run_id, stuck["id"], resolution="link",
        harvest_invoice_id=778899, harvest_invoice_number="INV-778899",
        actor=ACTOR,
    )
    # One group is still approved and un-attempted, so the run is not finished
    # — resolving a row must not declare the run complete over its head.
    assert resolved["run_status"] == "executing"

    # The fake's create_invoice was replaced with the halting wrapper, whose
    # counter is past 2, so the third group now succeeds.
    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["created"] == 1
    assert result["status"] == "completed"
    assert [i["status"] for i in await _items(pool, run_id)] == [
        "created", "created", "created",
    ]
    # Two real POSTs across both clicks; the halted one was never retried.
    assert len(fake.created_invoices) == 2


async def test_resolving_the_last_row_as_failed_finishes_the_run(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)
    fake.fail_create_invoice(httpx.TimeoutException("read timeout"))
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    stuck = (await _items(pool, run_id))[0]
    resolved = await inflight.resolve_item(
        pool, run_id, stuck["id"], resolution="failed", actor=ACTOR,
    )
    assert resolved["run_status"] == "failed"
    assert await _run_status(pool, run_id) == "failed"


# ── What is not billed ──────────────────────────────────────────────────────


async def test_unapproved_and_rejected_groups_are_never_posted(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)

    await review.set_item_approval(
        pool, run_id, items[0]["id"], approved=False, actor=ACTOR
    )
    await review.set_item_rejection(
        pool, run_id, items[1]["id"], rejected=True,
        reason="already invoiced by hand on the 1st", actor=ACTOR,
    )

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert len(fake.created_invoices) == 1
    assert result["attempted"] == 1
    assert [i["status"] for i in await _items(pool, run_id)] == [
        "planned", "skipped", "created",
    ]
    # The undecided group holds the run open; the rejected one does not.
    assert result["status"] == "awaiting_approval"
    assert result["remaining"] == 1


# ── Drafting a subset: a click is a batch, not the run ──────────────────────
#
# The operator approves two of three groups because the third is waiting on an
# answer from a PM. Drafting the two must not declare the month billed and lock
# the third out of the run it was planned in.


async def test_drafting_a_subset_leaves_the_rest_billable(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[2]["id"], approved=False, actor=ACTOR
    )

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["created"] == 2
    assert result["remaining"] == 1
    assert result["status"] == "awaiting_approval"
    assert await _run_status(pool, run_id) == "awaiting_approval"
    assert [i["status"] for i in await _items(pool, run_id)] == [
        "created", "created", "planned",
    ]


async def test_a_second_batch_drafts_only_the_newly_approved_group(fake):
    """The assertion that matters is `len(fake.created_invoices)`: the second
    click must not re-POST the two invoices the first click already created."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[2]["id"], approved=False, actor=ACTOR
    )
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    # Days later, the answer arrives and the third group is approved.
    await review.set_item_approval(
        pool, run_id, items[2]["id"], approved=True, actor=ACTOR
    )
    second = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert second["attempted"] == 1
    assert second["created"] == 1
    assert second["remaining"] == 0
    assert second["status"] == "completed"
    assert len(fake.created_invoices) == 3
    assert [i["status"] for i in await _items(pool, run_id)] == [
        "created", "created", "created",
    ]


async def test_a_group_left_over_from_a_failed_batch_still_holds_the_run_open(fake):
    """`failed` is not the last word while a group nobody has decided on can
    still be billed from this run."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[1]["id"], approved=False, actor=ACTOR
    )
    for _ in range(2):
        fake.fail_create_invoice(harvest.HarvestValidationError(
            "422", status=422, path="/invoices",
            body={"message": "client_id is invalid"},
        ))

    result = await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert result["failed"] == 2
    assert result["status"] == "awaiting_approval"
    assert await _run_status(pool, run_id) == "awaiting_approval"


# ── Closing what the batches left behind ────────────────────────────────────


async def test_closing_rejects_the_remainder_and_finishes_the_run(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[2]["id"], approved=False, actor=ACTOR
    )
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    closed = await review.close_run(
        pool, run_id, reason="waiting on September's SOW", actor=ACTOR,
    )

    assert closed["skipped"] == 1
    assert closed["status"] == "completed"
    assert await _run_status(pool, run_id) == "completed"

    left = (await _items(pool, run_id))[2]
    assert left["status"] == "skipped"
    assert left["rejected_by"] == ACTOR
    assert left["skip_reason"] == "waiting on September's SOW"

    event = (await _events(pool, "billing.run.closed"))[0]
    assert event["actor"] == ACTOR
    assert event["payload"]["skipped_count"] == 1


async def test_a_closed_run_cannot_be_drafted_from_again(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=2)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[1]["id"], approved=False, actor=ACTOR
    )
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)
    await review.close_run(pool, run_id, actor=ACTOR)

    with pytest.raises(execute.RunExecutionError, match="Run is completed"):
        await execute.execute_run(pool, settings, run_id, actor=ACTOR)
    assert len(fake.created_invoices) == 1


async def test_closing_is_refused_while_a_row_is_in_flight(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    await _halt_on_second(fake)
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    with pytest.raises(review.ApprovalError, match="in flight"):
        await review.close_run(pool, run_id, actor=ACTOR)


async def test_a_run_that_drafted_nothing_is_abandoned_not_closed(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    with pytest.raises(review.ApprovalError, match="abandon the run instead"):
        await review.close_run(pool, run_id, actor=ACTOR)


async def test_a_partially_drafted_run_cannot_be_abandoned(fake):
    """`abandoned` reads everywhere as "this produced nothing", and this run put
    an invoice in front of a client."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=2)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[1]["id"], approved=False, actor=ACTOR
    )
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    with pytest.raises(planner.RunStateError, match="close it instead"):
        await planner.abandon_run(pool, run_id, actor=ACTOR)
    assert await _run_status(pool, run_id) == "awaiting_approval"


async def test_replanning_the_month_settles_a_partially_drafted_run(fake):
    """Re-planning sweeps the leftovers, which is what closing does — so the
    old run settles rather than being relabelled as having produced nothing."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=2)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[1]["id"], approved=False, actor=ACTOR
    )
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    new_run = await planner.plan_run(pool, settings, run_month=AUGUST)

    assert new_run != run_id
    assert await _run_status(pool, run_id) == "completed"
    assert [i["status"] for i in await _items(pool, run_id)] == [
        "created", "abandoned",
    ]


async def test_a_run_with_nothing_approved_is_refused_before_any_post(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)
    await review.set_all_approvals(pool, run_id, approved=False, actor=ACTOR)

    with pytest.raises(execute.RunExecutionError, match="No approved groups"):
        await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert fake.created_invoices == []
    assert await _run_status(pool, run_id) == "awaiting_approval"


async def test_a_completed_run_cannot_be_executed_twice(fake):
    """The guard that stops a double-click from becoming double money — the
    run-level half of it. The item-level `FOR UPDATE` is the other half."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)
    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    with pytest.raises(execute.RunExecutionError, match="Run is completed"):
        await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    assert len(fake.created_invoices) == 1


async def test_a_draw_run_is_refused(fake):
    """A draw is billed from the Draws tab and never rides a monthly run."""
    pool = await get_pool()
    run_id = await pool.fetchval(
        "INSERT INTO billing_runs (run_month, status, kind) "
        "VALUES ($1, 'executing', 'draw') RETURNING id",
        AUGUST,
    )
    with pytest.raises(execute.RunExecutionError, match="Draws tab"):
        await execute.execute_run(pool, settings, run_id, actor=ACTOR)


async def test_an_unknown_run_is_refused(fake):
    pool = await get_pool()
    with pytest.raises(execute.RunNotFound, match="not found"):
        await execute.execute_run(
            pool, settings, "00000000-0000-0000-0000-000000000000", actor=ACTOR,
        )


# ── The audit trail ─────────────────────────────────────────────────────────


async def test_the_attempt_is_recorded_before_the_post(fake):
    """`ATTEMPTED` is committed before the POST so that an invoice created
    during an outage that never returned still has a record on our side."""
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)
    fake.fail_create_invoice(httpx.TimeoutException("read timeout"))

    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    attempted = await _events(pool, "billing.invoice.attempted")
    assert len(attempted) == 1
    assert attempted[0]["payload"]["billing_run_id"] == str(run_id)
    assert attempted[0]["actor"] == ACTOR
    # Nothing was created, and the row says so by staying in flight.
    assert await _events(pool, "billing.invoice.created") == []
    assert (await _items(pool, run_id))[0]["status"] == "in_flight"


async def test_the_run_brackets_are_recorded_with_their_counts(fake):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=2)

    await execute.execute_run(pool, settings, run_id, actor=ACTOR)

    started = await _events(pool, "billing.run.execution.started")
    finished = await _events(pool, "billing.run.executed")
    assert started[0]["payload"]["approved_count"] == 2
    assert finished[0]["payload"] == {
        "billing_run_id": str(run_id),
        "status": "completed",
        "attempted": 2,
        "created": 2,
        "failed": 0,
        "remaining": 0,
    }


# ── The HTTP contract ───────────────────────────────────────────────────────
#
# 200 even when invoices failed, and 200 even when the run halted. That second
# one departs from `POST /draws/{id}/invoice`, which answers 502 on an unknown
# outcome — a batch that halts has usually already created real invoices, and a
# 502 would throw away the only record of which.


async def test_api_creates_the_drafts_and_reports_per_group(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=2)

    res = await client.post(f"/billing/runs/{run_id}/execute")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "completed"
    assert body["created"] == 2
    assert body["halted"] is False
    assert body["unknown_item"] is None
    assert {i["billing_group_name"] for i in body["items"]} == {
        "A — Platform", "B — Mobile",
    }
    assert all(i["harvest_invoice_number"] for i in body["items"])


async def test_api_reports_a_halt_inside_a_200(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    await _halt_on_second(fake)

    res = await client.post(f"/billing/runs/{run_id}/execute")

    assert res.status_code == 200, "a 502 would discard the invoice that did get made"
    body = res.json()
    assert body["halted"] is True
    assert body["created"] == 1
    assert body["unknown_item"]["billing_run_id"] == str(run_id)
    assert body["unknown_item"]["remedy"]


async def test_api_refuses_an_unapproved_run_with_409(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)
    await review.set_all_approvals(pool, run_id, approved=False, actor=ACTOR)

    res = await client.post(f"/billing/runs/{run_id}/execute")

    assert res.status_code == 409
    assert "No approved groups" in res.json()["detail"]
    assert fake.created_invoices == []


async def test_api_404s_an_unknown_run(fake, client):
    """404, not 409. 409 means "this run exists and is not in a state to be
    executed" — a distinction the operator acts on differently."""
    res = await client.post(
        "/billing/runs/00000000-0000-0000-0000-000000000000/execute"
    )
    assert res.status_code == 404
    assert "not found" in res.json()["detail"]


async def test_api_records_the_authenticated_user_as_the_actor(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    await client.post(f"/billing/runs/{run_id}/execute")

    created = await _events(pool, "billing.invoice.created")
    assert len(created) == 1
    assert created[0]["actor"] and created[0]["actor"] != "system"


async def test_api_drafts_a_subset_and_reports_what_is_left(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[2]["id"], approved=False, actor=ACTOR
    )

    res = await client.post(f"/billing/runs/{run_id}/execute")

    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 2
    assert body["remaining"] == 1
    assert body["status"] == "awaiting_approval"


async def test_api_closes_the_run_and_returns_the_settled_detail(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake)
    items = await _items(pool, run_id)
    await review.set_item_approval(
        pool, run_id, items[2]["id"], approved=False, actor=ACTOR
    )
    await client.post(f"/billing/runs/{run_id}/execute")

    res = await client.post(
        f"/billing/runs/{run_id}/close", json={"reason": "next month"},
    )

    assert res.status_code == 200
    detail = res.json()
    assert detail["status"] == "completed"
    left = next(i for i in detail["items"] if i["status"] == "skipped")
    assert left["skip_reason"] == "next month"
    assert left["rejected_by"] and left["rejected_by"] != "system"


async def test_api_refuses_to_close_a_run_that_drafted_nothing(fake, client):
    pool = await get_pool()
    run_id = await _planned_and_approved(pool, fake, groups=1)

    res = await client.post(f"/billing/runs/{run_id}/close", json={})

    assert res.status_code == 409
    assert "abandon" in res.json()["detail"]


async def test_api_404s_a_close_on_an_unknown_run(fake, client):
    res = await client.post(
        "/billing/runs/00000000-0000-0000-0000-000000000000/close", json={},
    )
    assert res.status_code == 404
