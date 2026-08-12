"""The Harvest write, end to end (PRD §8).

Four outcomes, and the system must tell them apart:

    201      → created, draw consumed, run completed
    4xx      → failed, draw returns to ready, safe to retry
    timeout  → in_flight forever, draw locked, human resolves
    duplicate→ refused before the POST

The assertion that matters most in this file is not a status — it is
`len(fake.created_invoices)`. Harvest has no idempotency keys, so every extra
POST is a second real invoice.
"""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from app.config import settings
from app.db import get_pool
from app.integrations import harvest
from app.services.billing import draws, harvest_snapshot, inflight, settings_store
from app.services.billing import groups as groups_service
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735774
ERP = 14308912
# A second project, for the one test that needs two draw groups at once — a
# project belongs to at most one active group.
PORTAL = 14308913

TODAY = date.today()
LAST_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=15)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Ridgeway Industrial")
    f.add_project(ERP, "Ridgeway ERP", client_id=CLIENT, is_fixed_fee=True)
    f.add_project(PORTAL, "Ridgeway Portal", client_id=CLIENT, is_fixed_fee=True)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


async def _ready_draw(
    pool,
    *,
    amount: float = 37500.0,
    name: str = "Ridgeway ERP — Implementation",
    project_id: int = ERP,
    **group_over,
):
    """A group with one draw, delivery confirmed — the state the button acts on.

    `project_id` is a parameter because a project belongs to at most one active
    group, so a test wanting two groups needs two projects.
    """
    group = await groups_service.create_group(pool, {
        "name": name,
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "subject_template": "{client_name} — {draw_description}",
        "projects": [{"harvest_project_id": project_id}],
        "schedule_items": [{
            "harvest_project_id": project_id,
            "description": "Draw 1 — Signing",
            "amount": amount,
            "kind": "Service",
            "scheduled_date": LAST_MONTH,
        }],
        **group_over,
    })
    rows = await draws.list_draws(pool, group_id=group["id"])
    draw = rows[0]
    await draws.set_release(
        pool, draw["id"], released=True, actor="jacob@frogslayer.com"
    )
    return group, draw["id"]


async def _item(pool, draw_id):
    return await pool.fetchrow(
        "SELECT * FROM billing_run_items WHERE fixed_fee_schedule_item_id = $1",
        draw_id,
    )


async def _state(pool, draw_id):
    return (await draws.get_draw(pool, draw_id))["state"]


# ── 201 ─────────────────────────────────────────────────────────────────────


async def test_creates_the_draft_and_consumes_the_draw(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)

    result = await draws.invoice_draw(
        pool, settings, draw_id, actor="jacob@frogslayer.com"
    )

    assert len(fake.created_invoices) == 1
    assert result["harvest_invoice_id"] == fake.created_invoices[0]["invoice"]["id"]
    assert result["planned_amount"] == 37500.0
    assert result["actual_amount"] == 37500.0
    assert result["variance"] == 0.0

    item = await _item(pool, draw_id)
    assert item["status"] == "created"
    assert item["harvest_invoice_id"] == result["harvest_invoice_id"]
    assert item["harvest_invoice_number"] == result["harvest_invoice_number"]
    assert float(item["actual_amount"]) == 37500.0
    # A draw covers no service period — that is the whole point of billing it on
    # the day delivery is confirmed rather than on a month boundary.
    assert item["period_start"] is None
    assert item["period_end"] is None

    assert await _state(pool, draw_id) == "invoiced"
    run = await pool.fetchrow(
        "SELECT status, kind, completed_at FROM billing_runs WHERE id = $1",
        result["billing_run_id"],
    )
    assert run["status"] == "completed"
    assert run["kind"] == "draw"
    assert run["completed_at"] is not None


async def test_the_posted_body_is_the_previewed_body(fake):
    """The operator authorized what they saw. Recomputing must not change it."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)

    preview = await draws.preview_draw_invoice(pool, draw_id)
    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@frogslayer.com")

    assert fake.created_invoices[0]["payload"] == preview["planned_payload"]


# ── Notes: Harvest's own defaults never arrive ──────────────────────────────


async def _set_notes(pool, text: str):
    """The account default, where it actually lives — `billing_settings`, not env.

    Written through the service rather than with raw SQL so these tests would fail
    if the storage moved again.
    """
    await settings_store.update(
        pool, {"default_invoice_notes": text}, actor="jacob@f.com",
    )


async def test_default_notes_are_sent_when_the_group_has_none(fake):
    """Harvest's account-level default notes do not reach an API-created invoice.

    They apply only to invoices made in Harvest's UI, and no endpoint exposes them
    for reading (`GET /v2/company` has no such field). So unless we send notes, the
    invoice arrives blank — including the remit-to instructions a client needs to
    pay it. Found the hard way on the first live draw.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    await _set_notes(
        pool, "Remit to: Frogslayer LLC, acct 1234. Questions: ar@frogslayer.com",
    )

    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    body = fake.created_invoices[0]["payload"]
    assert body["notes"] == (
        "Remit to: Frogslayer LLC, acct 1234. Questions: ar@frogslayer.com"
    )


async def test_a_group_template_overrides_the_default(fake):
    """Override, not append — otherwise a client with bespoke wire instructions
    silently gets both sets."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(
        pool, notes_template="Per MSA §4, wire to the account on file.",
    )
    await _set_notes(pool, "Generic boilerplate.")

    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert fake.created_invoices[0]["payload"]["notes"] == (
        "Per MSA §4, wire to the account on file."
    )


async def test_default_notes_render_tokens(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    await _set_notes(pool, "Thank you, {client_name} — {draw_description}.")

    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert fake.created_invoices[0]["payload"]["notes"] == (
        "Thank you, Ridgeway Industrial — Draw 1 — Signing."
    )


async def test_no_notes_key_when_nothing_is_configured(fake):
    """An empty setting must not become an empty `notes` string on the invoice.

    The seeded row is already empty, so this is the out-of-the-box behaviour.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)

    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert "notes" not in fake.created_invoices[0]["payload"]


async def test_changing_the_setting_changes_the_next_invoice(fake):
    """The point of moving this to the database: no restart between these two."""
    pool = await get_pool()
    _group, first_id = await _ready_draw(pool)
    await _set_notes(pool, "Old remit-to details.")
    await draws.invoice_draw(pool, settings, first_id, actor="jacob@f.com")

    _group2, second_id = await _ready_draw(
        pool, amount=1000.0, name="Ridgeway — Second", project_id=PORTAL,
    )
    await _set_notes(pool, "New remit-to details.")
    await draws.invoice_draw(pool, settings, second_id, actor="jacob@f.com")

    assert fake.created_invoices[0]["payload"]["notes"] == "Old remit-to details."
    assert fake.created_invoices[1]["payload"]["notes"] == "New remit-to details."


# ── What the draw becomes, after it leaves the queue ────────────────────────


async def test_an_invoiced_draw_still_reports_what_it_produced(fake):
    """The disappearing-draw bug.

    Once billed, a draw drops out of the ready queue. If the row carried no
    invoice identity it would be indistinguishable from one that was never
    billed — which is exactly how it looked on the first live run.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    billed = await draws.list_draws(pool, state="invoiced")

    assert len(billed) == 1
    row = billed[0]
    assert row["state"] == "invoiced"
    assert row["harvest_invoice_number"] == result["harvest_invoice_number"]
    assert row["harvest_invoice_id"] == result["harvest_invoice_id"]
    assert float(row["invoiced_amount"]) == result["actual_amount"]
    assert row["invoice_issue_date"] == result["issue_date"]
    assert row["invoice_due_date"] == result["due_date"]
    assert row["invoiced_at"] is not None
    # And the run that made it, so the operator can open the record.
    assert row["live_run_id"] == result["billing_run_id"]
    assert row["invoiced_run_id"] == result["billing_run_id"]


# ── Dating: drafted + terms, not previewed + terms ──────────────────────────


async def test_the_invoice_is_dated_when_created_not_when_previewed(fake, monkeypatch):
    """Preview on the 10th, create on the 12th → issued the 12th, due the 22nd.

    The operator's example, exactly: net-10 custom terms, a preview looked at two
    days before the click. The due date has to follow the draft, because that is
    when the client's clock starts.

    Both dates move together and must: Harvest only accepts an explicit
    `due_date` for `custom` terms and derives it from `issue_date` otherwise, so
    an invoice reading "issued the 10th, net 10, due the 22nd" is visibly wrong to
    whoever receives it.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(
        pool, payment_term="custom", custom_net_days=10,
    )

    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 10))
    previewed = await draws.preview_draw_invoice(pool, draw_id)
    assert previewed["issue_date"] == date(2026, 8, 10)
    assert previewed["due_date"] == date(2026, 8, 20)

    # Two days pass. The operator clicks the button they were already looking at.
    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 12))
    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert result["issue_date"] == date(2026, 8, 12)
    assert result["due_date"] == date(2026, 8, 22)

    # And it is the *sent* body that carries them, not just the response.
    body = fake.created_invoices[0]["payload"]
    assert body["issue_date"] == "2026-08-12"
    assert body["due_date"] == "2026-08-22"
    assert body["payment_term"] == "custom"

    item = await _item(pool, draw_id)
    assert item["issue_date"] == date(2026, 8, 12)
    assert item["due_date"] == date(2026, 8, 22)


async def test_enum_terms_send_no_due_date_and_let_harvest_derive_it(fake, monkeypatch):
    """For `net 30` the issue date is the whole story.

    Harvest computes the due date from it, so anchoring the issue date to the
    draft day is what makes "drafted + terms" true here too — there is nothing
    else we could send.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)  # net 30

    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 12))
    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    body = fake.created_invoices[0]["payload"]
    assert body["issue_date"] == "2026-08-12"
    assert body["payment_term"] == "net 30"
    assert "due_date" not in body, (
        "Harvest ignores due_date unless the term is 'custom'; sending it invites "
        "our arithmetic to disagree with the invoice the client receives."
    )
    # Still reported, so the UI can show it — computed the same way Harvest will.
    assert result["due_date"] == date(2026, 9, 11)


async def test_an_explicit_issue_date_moves_both_dates(fake, monkeypatch):
    """Backdating is allowed, but never one date alone — that is the invariant."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(
        pool, payment_term="custom", custom_net_days=10,
    )

    monkeypatch.setattr(draws, "_today", lambda: date(2026, 8, 12))
    result = await draws.invoice_draw(
        pool, settings, draw_id, issue_date=date(2026, 7, 31), actor="jacob@f.com",
    )

    assert result["issue_date"] == date(2026, 7, 31)
    assert result["due_date"] == date(2026, 8, 10)
    body = fake.created_invoices[0]["payload"]
    assert body["issue_date"] == "2026-07-31"
    assert body["due_date"] == "2026-08-10"


async def test_variance_is_recorded_when_harvest_disagrees(fake):
    """Harvest is the authority on the amount; a difference is data, not an error."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool, amount=37500.0)
    fake.create_invoice_amount = 37000.0

    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert result["variance"] == -500.0
    item = await _item(pool, draw_id)
    assert float(item["variance"]) == -500.0


async def test_audit_trail_records_attempt_then_creation(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)

    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@frogslayer.com")

    rows = await pool.fetch(
        "SELECT event_type, actor FROM audit_log "
        "WHERE event_type LIKE 'billing.invoice%' ORDER BY id",
    )
    assert [r["event_type"] for r in rows] == [
        "billing.invoice.attempted",
        "billing.invoice.created",
    ]
    assert {r["actor"] for r in rows} == {"jacob@frogslayer.com"}


# ── The gate still holds ────────────────────────────────────────────────────


async def test_an_unreleased_draw_is_refused_before_any_post(fake):
    """Delivery confirmation is the entire billing trigger."""
    pool = await get_pool()
    group = await groups_service.create_group(pool, {
        "name": "Ridgeway ERP — Implementation",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": ERP}],
        "schedule_items": [{
            "harvest_project_id": ERP,
            "description": "Draw 1 — Signing",
            "amount": 1000,
            "kind": "Service",
            "scheduled_date": LAST_MONTH,
        }],
    })
    draw_id = (await draws.list_draws(pool, group_id=group["id"]))[0]["id"]

    with pytest.raises(draws.DrawError, match="not billable"):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert fake.created_invoices == []
    assert await _item(pool, draw_id) is None


async def test_an_invoiced_draw_cannot_be_billed_again(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    with pytest.raises(draws.DrawError):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert len(fake.created_invoices) == 1


async def test_a_stuck_draw_cannot_be_billed_again(fake):
    """Simulates the retry-after-a-timeout: the draw is locked, not billable.

    Caught by the derived-state check rather than the unique index — `in_flight`
    is not `ready`, so it never reaches the insert. The index is the backstop for
    a genuine race; see the next test.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))

    with pytest.raises(draws.DrawWriteUnknown):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert await _state(pool, draw_id) == "in_flight"

    with pytest.raises(draws.DrawError, match="not billable"):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    # One POST was attempted, and it was the one that timed out.
    assert len(fake.calls_to("create_invoice")) == 1
    assert fake.created_invoices == []


# Not tested here: the `UniqueViolationError` branch in `invoice_draw`, which
# catches two simultaneous clicks racing past the state check. It needs two real
# connections committing independently, and this suite runs every test on one
# pinned connection inside a transaction that is rolled back at the end (see
# `_SingleConnPool` in conftest). Under that harness a race cannot be staged, and
# every derivable single-threaded path reaches the `not billable` check first.
#
# The index itself is tested at the DB level instead —
# `test_billing_schema_constraints.py::test_a_draw_cannot_have_two_live_ledger_rows`.
# What remains unverified by any test is only the mapping from that violation to
# a `DrawError`, which is four lines and no branching.


# ── 4xx: a verdict ──────────────────────────────────────────────────────────


async def test_422_frees_the_draw_to_be_retried(fake):
    """Harvest looked at the payload and refused, so nothing was created."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    fake.fail_create_invoice(harvest.HarvestValidationError(
        "Harvest 422", status=422, path="/invoices",
        body={"message": "Project does not belong to client"},
    ))

    with pytest.raises(harvest.HarvestValidationError):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    item = await _item(pool, draw_id)
    assert item["status"] == "failed"
    assert "does not belong" in item["error_message"]
    assert item["harvest_invoice_id"] is None

    # Back to billable: the ledger row is terminal-but-unsuccessful, so it no
    # longer holds the index and no longer derives `in_flight`.
    assert await _state(pool, draw_id) == "ready"
    row = await pool.fetchrow(
        "SELECT invoiced_run_id FROM fixed_fee_schedule_items WHERE id = $1", draw_id
    )
    assert row["invoiced_run_id"] is None

    # And a retry genuinely works once the cause is fixed.
    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")
    assert result["harvest_invoice_id"] is not None
    assert await _state(pool, draw_id) == "invoiced"


async def test_rate_limit_past_the_cap_frees_the_draw(fake):
    """A 429 never reached invoice creation, so this one is safe to reset."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    fake.fail_create_invoice(harvest.HarvestRateLimited(
        "Harvest 429", status=429, path="/invoices", retry_after=15.0,
    ))

    with pytest.raises(harvest.HarvestRateLimited):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert (await _item(pool, draw_id))["status"] == "failed"
    assert await _state(pool, draw_id) == "ready"


# ── Unknown outcome: the poison pill ────────────────────────────────────────


@pytest.mark.parametrize("exc", [
    httpx.TimeoutException("timed out"),
    httpx.ConnectError("no route to host"),
    harvest.HarvestServerError("Harvest 503", status=503, path="/invoices"),
])
async def test_unknown_outcomes_stay_in_flight(fake, exc):
    """The invoice may exist. The system must not guess, either way.

    This is the case the whole protocol is built around: no retry, no rollback of
    the lock, and a status that says 'unknown' rather than 'failed'.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    fake.fail_create_invoice(exc)

    with pytest.raises(draws.DrawWriteUnknown) as raised:
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    item = await _item(pool, draw_id)
    assert item["status"] == "in_flight"
    assert item["error_message"] is None, (
        "An unknown outcome must not be written down as an error — that is the "
        "inference PRD §8 forbids."
    )
    assert raised.value.item_id == item["id"]

    # The run stays `executing`: it is not finished, and it did not fail.
    run = await pool.fetchrow(
        "SELECT status FROM billing_runs WHERE id = $1", item["billing_run_id"]
    )
    assert run["status"] == "executing"

    assert await _state(pool, draw_id) == "in_flight"

    audit_rows = await pool.fetch(
        "SELECT event_type, payload FROM audit_log "
        "WHERE event_type = 'billing.invoice.unknown'"
    )
    assert len(audit_rows) == 1
    assert "Check Harvest" in audit_rows[0]["payload"]["remedy"]


async def test_the_in_flight_row_is_written_before_the_post(fake):
    """Ordering: the lock exists by the time the request goes out.

    Scope, stated plainly — this proves *written before*, not *committed before*.
    Every test here runs on one pinned connection inside a transaction that is
    rolled back at the end (`_SingleConnPool` in conftest), so `pool.acquire()`
    below hands back the same connection and would see uncommitted rows too. A
    genuine commit-visibility check needs a second connection outside the test
    transaction, which this harness cannot provide — the test's own fixture data
    would be invisible to it.

    The commit boundary is therefore load-bearing and enforced by review, not by
    this test: `invoice_draw` closes transaction A before calling
    `create_invoice`. If someone wraps the POST in that transaction later, this
    test keeps passing. The docstring on `invoice_draw` says why not to.
    """
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)

    seen: dict[str, object] = {}

    original = fake.create_invoice

    async def _peek(cfg, payload):
        # A different connection from the one that wrote the row: it can only see
        # committed data.
        async with pool.acquire() as other:
            seen["status"] = await other.fetchval(
                "SELECT status FROM billing_run_items "
                "WHERE fixed_fee_schedule_item_id = $1",
                draw_id,
            )
            seen["attempted_audit"] = await other.fetchval(
                "SELECT count(*) FROM audit_log "
                "WHERE event_type = 'billing.invoice.attempted'"
            )
        return await original(cfg, payload)

    fake.create_invoice = _peek
    await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    assert seen["status"] == "in_flight", (
        "The lock was not committed before the POST. A crash during the request "
        "would leave an invoice in Harvest with no record on our side."
    )
    assert seen["attempted_audit"] == 1


# ── Resolution ──────────────────────────────────────────────────────────────


async def _stick_in_flight(pool, fake, draw_id):
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))
    with pytest.raises(draws.DrawWriteUnknown):
        await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")
    return await _item(pool, draw_id)


async def test_linking_reaches_the_same_state_as_a_clean_201(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    item = await _stick_in_flight(pool, fake, draw_id)

    await inflight.resolve_item(
        pool, item["billing_run_id"], item["id"],
        resolution="link", harvest_invoice_id=4242,
        harvest_invoice_number="INV-4242", actor="jacob@frogslayer.com",
    )

    resolved = await _item(pool, draw_id)
    assert resolved["status"] == "created"
    assert resolved["harvest_invoice_id"] == 4242
    assert await _state(pool, draw_id) == "invoiced"
    run = await pool.fetchrow(
        "SELECT status FROM billing_runs WHERE id = $1", item["billing_run_id"]
    )
    assert run["status"] == "completed"


async def test_linking_without_an_amount_leaves_variance_null(fake):
    """A variance of exactly zero reads as a verified match. We don't know that."""
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    item = await _stick_in_flight(pool, fake, draw_id)

    await inflight.resolve_item(
        pool, item["billing_run_id"], item["id"],
        resolution="link", harvest_invoice_id=4242, actor="jacob@f.com",
    )

    resolved = await _item(pool, draw_id)
    assert resolved["actual_amount"] is None
    assert resolved["variance"] is None


async def test_resolving_as_failed_returns_the_draw_to_ready(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    item = await _stick_in_flight(pool, fake, draw_id)

    await inflight.resolve_item(
        pool, item["billing_run_id"], item["id"],
        resolution="failed", actor="jacob@frogslayer.com",
    )

    assert (await _item(pool, draw_id))["status"] == "failed"
    assert await _state(pool, draw_id) == "ready"

    # And it can now be billed for real.
    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")
    assert result["harvest_invoice_id"] is not None


async def test_linking_requires_an_invoice_id(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    item = await _stick_in_flight(pool, fake, draw_id)

    with pytest.raises(inflight.InFlightError, match="requires the Harvest invoice id"):
        await inflight.resolve_item(
            pool, item["billing_run_id"], item["id"],
            resolution="link", actor="jacob@f.com",
        )

    assert (await _item(pool, draw_id))["status"] == "in_flight"


async def test_only_in_flight_rows_can_be_resolved(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    result = await draws.invoice_draw(pool, settings, draw_id, actor="jacob@f.com")

    with pytest.raises(inflight.InFlightError, match="not 'in_flight'"):
        await inflight.resolve_item(
            pool, result["billing_run_id"], result["billing_run_item_id"],
            resolution="failed", actor="jacob@f.com",
        )


async def test_unresolved_queue_lists_the_stuck_row(fake):
    pool = await get_pool()
    _group, draw_id = await _ready_draw(pool)
    await _stick_in_flight(pool, fake, draw_id)

    queue = await inflight.list_unresolved(pool)

    assert len(queue) == 1
    assert queue[0]["draw_description"] == "Draw 1 — Signing"
    assert queue[0]["harvest_client_name"] == "Ridgeway Industrial"
