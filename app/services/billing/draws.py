"""Fixed-fee draws — a contract's payment schedule, billed one at a time.

A `fixed_fee_schedule` group carries a schedule of draws: "30% on signing, 40%
at UAT, 30% at go-live". Two things about them shape every decision here.

**The schedule commits to dates, but dates never bill anything.** Delivery is
what earns a draw, and delivery slips. So `scheduled_date` drives the overdue
prompt and forecasting only; a draw becomes billable when a human confirms
delivery (`released_at`). When a date moves, the schedule is edited — nothing
bills itself in the meantime.

**Draws are billed between runs.** A milestone is accepted on the 12th and
invoiced on the 12th, so draws never ride the monthly run. Each one is billed
individually, for exactly its scheduled amount, as a `kind='draw'` billing run
holding a single ledger row.

**A draw's invoice is computed, never staged.** `preview_draw_invoice` builds
the exact POST body from the group config and the draw and writes nothing. A
ready draw is reviewed in place and created from there. There is deliberately no
step that persists a pending invoice ahead of the Harvest draft: it would invent
a state that is neither planned-in-a-run nor real, and then need a way to unwind
it. The ledger row is written by the execution path, immediately before the
POST, which is what the §8 in-flight protocol requires anyway.

Four states, derived rather than stored — there is no status column to drift
out of sync with the facts that define it:

    pending   released_at is null
    ready     released_at is not null, no live ledger row, not invoiced
    in_flight a live ledger row exists — execution has begun
    invoiced  invoiced_run_id is not null

`in_flight` is derived from `billing_run_items`, not from a column here, because
the ledger row *is* the fact: the partial unique index on
`fixed_fee_schedule_item_id` allows exactly one live row per draw, so a second
attempt is impossible whether or not this module notices. Until execution ships
nothing can produce it; it exists so that when execution does ship, a draw
mid-write can never be offered for billing again.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from app.config import Settings
from app.integrations import harvest
from app.orchestrator import events
from app.services import audit
from app.services.billing import (
    flags,
    recurring,
    settings_store,
)
from app.services.billing import (
    payload as payload_builder,
)
from app.services.billing.dates import resolve_due_date
from app.services.billing.errors import BillingConfigError

STATES = ("pending", "ready", "in_flight", "invoiced")

# The one live ledger row a draw may have, if any. The partial unique index
# `billing_run_items_one_live_per_draw` guarantees at most one, which is why a
# plain join is safe here rather than an aggregate.
#
# Carries the invoice identity too, because an `invoiced` draw leaves the billing
# queue and this row is then the only record of what it produced. A draw that
# vanishes with no invoice number to point at is indistinguishable from one that
# was never billed.
_LIVE_JOIN = """
    LEFT JOIN LATERAL (
        SELECT bri.id, bri.billing_run_id, bri.status,
               bri.harvest_invoice_number, bri.harvest_invoice_id,
               bri.issue_date, bri.due_date, bri.actual_amount, bri.created_at
        FROM billing_run_items bri
        WHERE bri.fixed_fee_schedule_item_id = d.id
          AND bri.status NOT IN ('failed', 'skipped', 'abandoned')
        LIMIT 1
    ) live ON TRUE
"""

# Derived in SQL so the queue can filter on it without loading every draw.
_STATE_SQL = """
    CASE
        WHEN d.invoiced_run_id IS NOT NULL THEN 'invoiced'
        WHEN live.id IS NOT NULL           THEN 'in_flight'
        WHEN d.released_at IS NOT NULL     THEN 'ready'
        ELSE 'pending'
    END
"""


class DrawError(BillingConfigError):
    """The requested draw operation is not permitted.

    Subclasses `BillingConfigError` so draw validation raised during group CRUD
    surfaces as a 400 alongside every other config error. The draw endpoints
    map it to 409, where it means a state conflict rather than bad input.
    """


# ── Read ────────────────────────────────────────────────────────────────────


async def list_draws(
    pool: asyncpg.Pool,
    *,
    group_id: UUID | None = None,
    state: str | None = None,
) -> list[dict[str, Any]]:
    """Draws with their group and project context, newest schedule last.

    Ordered by scheduled date so the queue reads as a timeline and the most
    overdue item is at the top of the pending list.
    """
    if state is not None and state not in STATES:
        raise DrawError(f"Unknown state {state!r}. Expected one of {', '.join(STATES)}.")

    conditions: list[str] = []
    params: list[Any] = []
    if group_id is not None:
        params.append(group_id)
        conditions.append(f"d.billing_group_id = ${len(params)}")
    if state is not None:
        params.append(state)
        conditions.append(f"{_STATE_SQL} = ${len(params)}")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = await pool.fetch(
        f"""
        SELECT d.id, d.billing_group_id, d.harvest_project_id, d.sequence,
               d.description, d.amount, d.kind, d.scheduled_date,
               d.released_at, d.released_by, d.invoiced_run_id,
               {_STATE_SQL} AS state,
               g.name AS billing_group_name,
               g.harvest_client_name,
               p.name AS harvest_project_name,
               live.billing_run_id AS live_run_id,
               live.harvest_invoice_number, live.harvest_invoice_id,
               live.issue_date AS invoice_issue_date,
               live.due_date AS invoice_due_date,
               live.actual_amount AS invoiced_amount,
               live.created_at AS invoiced_at
        FROM fixed_fee_schedule_items d
        JOIN billing_groups g ON g.id = d.billing_group_id
        LEFT JOIN harvest_projects p ON p.harvest_id = d.harvest_project_id
        {_LIVE_JOIN}
        {where}
        ORDER BY d.scheduled_date, g.name, d.sequence
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def get_draw(pool: asyncpg.Pool, draw_id: UUID) -> dict[str, Any] | None:
    rows = await pool.fetch(
        f"""
        SELECT d.*, {_STATE_SQL} AS state, g.name AS billing_group_name,
               g.harvest_client_name,
               live.billing_run_id AS live_run_id,
               live.harvest_invoice_number
        FROM fixed_fee_schedule_items d
        JOIN billing_groups g ON g.id = d.billing_group_id
        {_LIVE_JOIN}
        WHERE d.id = $1
        """,
        draw_id,
    )
    return dict(rows[0]) if rows else None


# ── Config ──────────────────────────────────────────────────────────────────


async def save_draws(
    conn: asyncpg.Connection, group_id: UUID, items: list[dict[str, Any]]
) -> None:
    """Upsert a group's schedule, preserving identity and history.

    Rows are matched on `id`, new rows are inserted, removed rows are deleted
    only while still pending, and an invoiced draw may not be modified or
    deleted at all. Its amount and description are on a real invoice.

    A draw needs this more strongly than a recurring line item does (which is
    upserted too, in `groups._save_recurring_items`): it carries `released_at`,
    `invoiced_run_id`, and a foreign key from `billing_run_items`, so replacing
    the set wholesale would erase billing history and orphan ledger rows. Since
    a slipped date means editing the schedule, that would happen routinely.
    Hence the locking below, which the recurring path has no need of.

    A draw with a live ledger row is guarded the same way for the same reason
    one step earlier: execution has begun against these exact values, so
    changing them here would leave the ledger describing an invoice nobody
    agreed to.
    """
    existing = {
        r["id"]: r for r in await conn.fetch(
            """
            SELECT d.id, d.released_at, d.invoiced_run_id, d.description,
                   d.amount, d.kind, d.harvest_project_id,
                   live.id IS NOT NULL AS in_flight
            FROM fixed_fee_schedule_items d
            LEFT JOIN LATERAL (
                SELECT bri.id FROM billing_run_items bri
                WHERE bri.fixed_fee_schedule_item_id = d.id
                  AND bri.status NOT IN ('failed', 'skipped', 'abandoned')
                LIMIT 1
            ) live ON TRUE
            WHERE d.billing_group_id = $1
            """,
            group_id,
        )
    }

    submitted_ids: set[UUID] = set()
    for order, it in enumerate(items, start=1):
        raw_id = it.get("id")
        draw_id = UUID(str(raw_id)) if raw_id else None

        if draw_id is not None and draw_id in existing:
            submitted_ids.add(draw_id)
            current = existing[draw_id]
            if current["invoiced_run_id"] is not None:
                # Silently ignoring the edit would be worse than refusing it:
                # the operator would believe the invoice changed.
                if _differs(current, it):
                    raise DrawError(
                        f"'{current['description']}' has already been invoiced and "
                        f"cannot be changed. Its amount and description are on a "
                        f"real invoice."
                    )
                continue
            if current["in_flight"] and _differs(current, it):
                raise DrawError(
                    f"'{current['description']}' has an invoice being created in "
                    f"Harvest from its current values and cannot be changed "
                    f"while that is in flight."
                )
            await conn.execute(
                """
                UPDATE fixed_fee_schedule_items
                SET harvest_project_id = $2, sequence = $3, description = $4,
                    amount = $5, kind = $6, scheduled_date = $7
                WHERE id = $1
                """,
                draw_id, int(it["harvest_project_id"]), order, it["description"],
                it.get("amount", 0), it.get("kind") or "Service",
                it["scheduled_date"],
            )
        else:
            await conn.execute(
                """
                INSERT INTO fixed_fee_schedule_items
                    (billing_group_id, harvest_project_id, sequence, description,
                     amount, kind, scheduled_date)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                """,
                group_id, int(it["harvest_project_id"]), order, it["description"],
                it.get("amount", 0), it.get("kind") or "Service",
                it["scheduled_date"],
            )

    dropped = [
        (draw_id, r) for draw_id, r in existing.items()
        if draw_id not in submitted_ids and r["invoiced_run_id"] is None
    ]
    still_live = [r["description"] for _, r in dropped if r["in_flight"]]
    if still_live:
        raise DrawError(
            "A draw with an invoice in flight cannot be removed from the "
            "schedule — resolve that first: "
            + ", ".join(f"'{d}'" for d in still_live)
        )

    removed = [draw_id for draw_id, _ in dropped]
    if removed:
        try:
            await conn.execute(
                "DELETE FROM fixed_fee_schedule_items WHERE id = ANY($1::uuid[])",
                removed,
            )
        except asyncpg.ForeignKeyViolationError:
            # A failed or abandoned attempt leaves a ledger row that still
            # points here (`on delete restrict`), so a draw that was ever billed
            # against can no longer be deleted. Say so, rather than 500.
            raise DrawError(
                "A draw that has been billed against cannot be deleted — the "
                "billing history references it. Set its amount to 0, or leave "
                "it in place."
            )

    kept_invoiced = [
        r["description"] for draw_id, r in existing.items()
        if draw_id not in submitted_ids and r["invoiced_run_id"] is not None
    ]
    if kept_invoiced:
        raise DrawError(
            "An invoiced draw cannot be removed from the schedule: "
            + ", ".join(f"'{d}'" for d in kept_invoiced)
        )


def _differs(current: asyncpg.Record, submitted: dict[str, Any]) -> bool:
    """Did the operator actually change a locked draw, or just resubmit it?

    Compares exactly the fields that reach an invoice payload — the rest
    (`scheduled_date`, `sequence`) can move freely, because a locked draw's date
    is a historical note rather than a commitment anyone still works against.
    """
    return (
        current["description"] != submitted.get("description")
        or float(current["amount"]) != float(submitted.get("amount") or 0)
        or current["kind"] != (submitted.get("kind") or "Service")
        or current["harvest_project_id"] != int(submitted["harvest_project_id"])
    )


async def validate_draws(
    conn: asyncpg.Connection, items: list[dict[str, Any]], project_ids: list[int]
) -> None:
    """Same two checks recurring line items get: the project must be in the
    group, and `kind` must be a category this Harvest account actually has."""
    if not items:
        return
    allowed = set(project_ids)
    stray = [
        f"'{it.get('description') or 'untitled'}' → project #{it['harvest_project_id']}"
        for it in items
        if int(it["harvest_project_id"]) not in allowed
    ]
    if stray:
        raise DrawError(
            "Every draw must target a project in this billing group. " + "; ".join(stray)
        )

    valid_kinds = await recurring.load_valid_kinds(conn)
    if valid_kinds:
        bad = sorted({
            it["kind"] for it in items if it.get("kind") and it["kind"] not in valid_kinds
        })
        if bad:
            raise DrawError(
                f"Invoice item category {bad} does not exist in this Harvest "
                f"account. Valid: {', '.join(sorted(valid_kinds))}."
            )

    missing_date = [
        it.get("description") or "untitled" for it in items if not it.get("scheduled_date")
    ]
    if missing_date:
        raise DrawError(
            "Every draw needs a scheduled date — it's what the contract commits "
            "to and what drives the overdue prompt. Missing on: "
            + ", ".join(f"'{d}'" for d in missing_date)
        )


# ── Release ─────────────────────────────────────────────────────────────────


async def set_release(
    pool: asyncpg.Pool,
    draw_id: UUID,
    *,
    released: bool,
    actor: str = "system",
) -> dict[str, Any] | None:
    """Confirm (or withdraw) delivery. Human-only.

    This is the entire billing trigger for a fixed-fee contract, which is why
    nothing in the system calls it — no scheduler, no planner, no agent.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT d.id, d.description, d.released_at, d.invoiced_run_id,
                       d.billing_group_id, g.name AS billing_group_name
                FROM fixed_fee_schedule_items d
                JOIN billing_groups g ON g.id = d.billing_group_id
                WHERE d.id = $1
                FOR UPDATE OF d
                """,
                draw_id,
            )
            if row is None:
                return None
            if row["invoiced_run_id"] is not None:
                raise DrawError(
                    f"'{row['description']}' has already been invoiced. Delivery "
                    f"confirmation cannot be changed."
                )
            if released == (row["released_at"] is not None):
                return await get_draw(pool, draw_id)

            if released:
                await conn.execute(
                    "UPDATE fixed_fee_schedule_items "
                    "SET released_at = now(), released_by = $2 WHERE id = $1",
                    draw_id, actor,
                )
            else:
                await _guard_no_live_ledger_row(conn, draw_id, row["description"])
                await conn.execute(
                    "UPDATE fixed_fee_schedule_items "
                    "SET released_at = NULL, released_by = NULL WHERE id = $1",
                    draw_id,
                )

            await audit.write_audit_event(
                conn,
                events.BILLING_DRAW_RELEASED if released
                else events.BILLING_DRAW_UNRELEASED,
                actor=actor,
                payload={
                    "fixed_fee_schedule_item_id": str(draw_id),
                    "billing_group_id": str(row["billing_group_id"]),
                    "billing_group": row["billing_group_name"],
                    "description": row["description"],
                },
            )
    return await get_draw(pool, draw_id)


async def _guard_no_live_ledger_row(
    conn: asyncpg.Connection, draw_id: UUID, description: str
) -> None:
    """Withdrawing delivery while a ledger row is live would leave that row
    attached to a draw the system now considers undelivered."""
    live = await conn.fetchval(
        "SELECT billing_run_id FROM billing_run_items "
        "WHERE fixed_fee_schedule_item_id = $1 "
        "  AND status NOT IN ('failed','skipped','abandoned') LIMIT 1",
        draw_id,
    )
    if live is not None:
        raise DrawError(
            f"'{description}' has an invoice in flight. Resolve that before "
            f"withdrawing delivery confirmation."
        )


# ── Billing ─────────────────────────────────────────────────────────────────


def _today() -> date:
    """Today, as a seam.

    Exists so a test can prove the one property that cannot be observed from a
    single call: that the invoice is dated when it is *created*, not when it was
    previewed. Without this the assertion would be "these two calls agree", which
    is what a bug here would also look like.
    """
    return date.today()


async def preview_draw_invoice(
    pool: asyncpg.Pool,
    draw_id: UUID,
    *,
    issue_date: date | None = None,
) -> dict[str, Any] | None:
    """The exact invoice this draw would produce, computed and thrown away.

    Nothing is written. A draw's invoice is a pure function of the group config
    and the draw itself, so persisting it before the Harvest draft exists would
    only invent a state — an invoice that is neither planned-in-a-run nor real —
    and with it the problem of unwinding that state. The ledger row is written
    by the execution path, immediately before the POST, as the in-flight
    protocol requires (PRD §8).

    Note what is *not* here: no `resolve_period`. Arrears/advance answers "which
    month's work does this cover?" and a draw covers no month — it's a contract
    event billed on the day it's confirmed. `period_start` / `period_end` stay
    null on the ledger row when one is eventually written.

    **Dates anchor to the day the draft is created, not to when it was previewed.**
    `issue_date` defaults to today on every call, so a preview looked at on the
    10th and created on the 12th is issued the 12th and — with net-10 terms — due
    the 22nd. `invoice_draw` relies on this: it recomputes rather than reusing
    whatever the operator last saw.

    Passing `issue_date` explicitly moves the issue date *and* the due date
    together; there is no way to shift one without the other. That is deliberate.
    Harvest only accepts a `due_date` for `custom` terms and derives it from
    `issue_date` for everything else, so an invoice reading "issued the 10th, net
    10, due the 22nd" is one the client can see is wrong.

    Takes no `cfg`: the only thing it needed one for was the default invoice
    notes, and those now come from `billing_settings` on the connection this
    function already holds.
    """
    issue = issue_date or _today()

    async with pool.acquire() as conn:
        draw = await conn.fetchrow(
            f"""
            SELECT d.*, {_STATE_SQL} AS state, g.id AS group_id,
                   g.name AS group_name, g.harvest_client_id,
                   g.harvest_client_name, g.payment_term, g.custom_net_days,
                   g.subject_template, g.notes_template, g.purchase_order,
                   g.is_active,
                   p.name AS harvest_project_name,
                   (SELECT count(*) FROM fixed_fee_schedule_items x
                     WHERE x.billing_group_id = d.billing_group_id)
                       AS schedule_length
            FROM fixed_fee_schedule_items d
            JOIN billing_groups g ON g.id = d.billing_group_id
            LEFT JOIN harvest_projects p ON p.harvest_id = d.harvest_project_id
            {_LIVE_JOIN}
            WHERE d.id = $1
            """,
            draw_id,
        )
        if draw is None:
            return None

        payment_term, due_date = resolve_due_date(
            issue, draw["payment_term"], draw["custom_net_days"]
        )

        # Everything that would stop this billing cleanly, surfaced rather than
        # raised: the operator is looking at the invoice, and a preview that
        # refuses to render explains nothing.
        preview_flags: list[flags.Flag] = []
        valid_kinds = await recurring.load_valid_kinds(conn)
        if valid_kinds and draw["kind"] not in valid_kinds:
            preview_flags.append(flags.invalid_item_category(
                offenders=[{"description": draw["description"], "kind": draw["kind"]}],
                valid=sorted(valid_kinds),
            ))

        in_group = await conn.fetchval(
            "SELECT 1 FROM billing_group_projects "
            "WHERE billing_group_id = $1 AND harvest_project_id = $2",
            draw["group_id"], draw["harvest_project_id"],
        )
        if not in_group:
            preview_flags.append(flags.line_item_off_group_project(offenders=[{
                "description": draw["description"],
                "harvest_project_id": draw["harvest_project_id"],
            }]))

        tokens = {
            "client_name": draw["harvest_client_name"] or "",
            "draw_description": draw["description"],
            # Position in the contract schedule, and its length — "Draw 2 of 5".
            # Both follow list order, so re-ordering or extending the schedule
            # re-numbers whatever has not billed yet.
            "draw_number": str(draw["sequence"]),
            "draw_count": str(draw["schedule_length"]),
        }
        subject = payload_builder.render_template(
            draw["subject_template"] or "{client_name} — {draw_description}",
            **tokens,
        )
        # Falls back to the account default from `billing_settings`. Harvest's own
        # default notes never reach an API-created invoice — see
        # `payload.resolve_notes`.
        notes = payload_builder.resolve_notes(
            draw["notes_template"],
            await settings_store.get_default_invoice_notes(conn),
            **tokens,
        )

        amount = float(draw["amount"])
        body = payload_builder.build_free_form_payload(
            harvest_client_id=draw["harvest_client_id"],
            subject=subject,
            issue_date=issue,
            payment_term=payment_term,
            due_date=due_date,
            line_items=[{
                "project_id": draw["harvest_project_id"],
                "kind": draw["kind"],
                "description": draw["description"],
                "quantity": 1,
                "unit_price": amount,
            }],
            purchase_order=draw["purchase_order"],
            notes=notes,
        )

    return {
        "draw_id": draw["id"],
        "billing_group_id": draw["group_id"],
        "billing_group_name": draw["group_name"],
        "harvest_client_name": draw["harvest_client_name"],
        "description": draw["description"],
        "state": draw["state"],
        "amount": amount,
        "issue_date": issue,
        "due_date": due_date,
        "payment_term": payment_term,
        "subject": subject,
        "notes": notes,
        "estimated_line_items": [{
            "label": draw["description"],
            "detail": draw["kind"],
            "quantity": 1,
            "unit": "ea",
            "unit_price": amount,
            "amount": amount,
            "project_name": draw["harvest_project_name"],
        }],
        "planned_payload": body,
        "flags": [f.as_dict() for f in preview_flags],
        # Only a released, un-billed draw may be created. The check is repeated
        # in the execution path — this one is so the button can explain itself.
        "billable": draw["state"] == "ready" and draw["is_active"],
    }


# ── Execution: the one Harvest write (PRD §8) ───────────────────────────────


class DrawWriteUnknown(Exception):
    """The POST did not return a verdict. The invoice may or may not exist.

    Distinct from `DrawError` (a refused request, nothing attempted) and from
    `HarvestValidationError` (a refusal from Harvest, nothing created). This is
    the ambiguous outcome the protocol exists to preserve: the ledger row is left
    `in_flight`, and only a human looking at Harvest can settle it.

    Raised rather than returned so it cannot be mistaken for a result and quietly
    logged as a failure by a caller that forgot to check a status field.
    """

    def __init__(self, message: str, *, item_id: UUID, run_id: UUID, cause: str) -> None:
        super().__init__(message)
        self.item_id = item_id
        self.run_id = run_id
        self.cause = cause


async def invoice_draw(
    pool: asyncpg.Pool,
    cfg: Settings,
    draw_id: UUID,
    *,
    issue_date: date | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Create the Harvest draft invoice for one released draw. Human-only.

    Operator-initiated per ADR-0004: the caller has already seen this exact
    payload via `preview_draw_invoice`, and their click is the authorization.
    There is no approval row. Nothing in the system calls this — no scheduler, no
    planner, no agent, and it is in no tool's `allowed_tools`.

    The order of writes is the entire safety property, and it is not the obvious
    one. The `in_flight` ledger row is written **and committed** before the POST,
    in its own transaction:

        transaction A: write in_flight, COMMIT   ← the lock, now durable
        POST /v2/invoices                        ← may time out, may 5xx
        transaction B: record the outcome

    Sharing one transaction across the POST would be the natural way to write
    this and it would be wrong: a process death mid-request would roll back the
    lock, leaving an invoice in Harvest that this system has no record of — and
    the next click would create a second one. Committing first means the worst
    case is a row we cannot interpret, which is recoverable by a human, instead
    of money we cannot see.

    Returns the created-invoice summary. Raises `DrawError` if the draw was never
    billable, `HarvestValidationError` (etc.) if Harvest refused, and
    `DrawWriteUnknown` if the outcome is genuinely unknown.
    """
    # Recomputed here rather than accepted from the caller. The preview is a pure
    # function, so recomputing costs nothing and removes any chance of the body
    # differing from the one the operator approved by clicking.
    preview = await preview_draw_invoice(pool, draw_id, issue_date=issue_date)
    if preview is None:
        raise DrawError("Draw not found.")
    if not preview["billable"]:
        raise DrawError(
            f"'{preview['description']}' is not billable (state: {preview['state']}). "
            "Only a released, un-invoiced draw in an active group can be invoiced."
        )
    blocking = [f for f in preview["flags"] if f["severity"] == "error"]
    if blocking:
        raise DrawError(
            f"'{preview['description']}' has unresolved errors: "
            + "; ".join(f["message"] for f in blocking)
        )

    body = preview["planned_payload"]
    amount = preview["amount"]

    # ── Transaction A: take the lock, durably ────────────────────────────────
    async with pool.acquire() as conn:
        async with conn.transaction():
            run_id = await conn.fetchval(
                "INSERT INTO billing_runs (run_month, status, kind) "
                "VALUES ($1, 'executing', 'draw') RETURNING id",
                # A draw covers no service period; the month it was billed in is
                # the only sensible stamp, and the ledger's uniqueness for draws
                # keys on the draw, not the month.
                preview["issue_date"].replace(day=1),
            )
            try:
                item_id = await conn.fetchval(
                    """
                    INSERT INTO billing_run_items (
                        billing_run_id, billing_group_id, fixed_fee_schedule_item_id,
                        run_month, status, planned_amount, planned_payload,
                        estimated_line_items, issue_date, due_date
                    )
                    VALUES ($1,$2,$3,$4,'in_flight'::billing_run_item_status,
                            $5,$6,$7,$8,$9)
                    RETURNING id
                    """,
                    run_id, preview["billing_group_id"], draw_id,
                    preview["issue_date"].replace(day=1),
                    amount, body, preview["estimated_line_items"],
                    preview["issue_date"], preview["due_date"],
                )
            except asyncpg.UniqueViolationError as exc:
                # `billing_run_items_one_live_per_draw`. Another attempt is
                # already in flight — possibly a double-click, possibly a stuck
                # row from an earlier outage. Either way this must not POST.
                raise DrawError(
                    f"'{preview['description']}' already has an invoice in flight. "
                    "Resolve that before billing it again."
                ) from exc

            await audit.write_audit_event(
                conn,
                events.BILLING_INVOICE_ATTEMPTED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "fixed_fee_schedule_item_id": str(draw_id),
                    "billing_group_id": str(preview["billing_group_id"]),
                    "description": preview["description"],
                    "planned_amount": amount,
                },
            )
    # Transaction A has committed here. The lock survives anything below.

    # ── The POST ─────────────────────────────────────────────────────────────
    try:
        invoice = await harvest.create_invoice(cfg, body)
    except harvest.HarvestRateLimited:
        # Past the retry cap inside `_post`. A 429 never reached creation, so
        # unlike the other failures below this one is genuinely safe to reset.
        await _record_failure(
            pool, item_id, run_id, draw_id, actor=actor,
            error="Harvest rate limit exceeded after retries. Nothing was created.",
        )
        raise
    except (harvest.HarvestValidationError, harvest.HarvestAuthError,
            harvest.HarvestNotFoundError) as exc:
        # A 4xx is a verdict: Harvest looked at the payload and refused. Nothing
        # was created, so the draw is safe to return to `ready`.
        await _record_failure(
            pool, item_id, run_id, draw_id, actor=actor, error=str(exc.body or exc),
        )
        raise
    except Exception as exc:
        # Timeout, connection error, 5xx, or anything unanticipated. The invoice
        # may exist. Write *nothing* to the item — it stays `in_flight` — and say
        # so in the audit trail.
        await _record_unknown(pool, item_id, run_id, draw_id, actor=actor, cause=repr(exc))
        raise DrawWriteUnknown(
            f"The POST for '{preview['description']}' did not return a verdict "
            f"({type(exc).__name__}). The invoice may exist in Harvest. This draw "
            "is locked until a human resolves it.",
            item_id=item_id, run_id=run_id, cause=repr(exc),
        ) from exc

    # ── Transaction B: success ───────────────────────────────────────────────
    actual = float(invoice.get("amount") or 0.0)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE billing_run_items
                SET status = 'created'::billing_run_item_status,
                    harvest_invoice_id = $2,
                    harvest_invoice_number = $3,
                    actual_amount = $4,
                    variance = $4 - planned_amount
                WHERE id = $1
                """,
                item_id, int(invoice["id"]), str(invoice.get("number") or ""), actual,
            )
            # Same transaction as the ledger update, per PRD 4.4 — a consumed
            # draw and the row that consumed it become true together.
            await conn.execute(
                "UPDATE fixed_fee_schedule_items SET invoiced_run_id = $2 WHERE id = $1",
                draw_id, run_id,
            )
            await conn.execute(
                "UPDATE billing_runs SET status = 'completed', completed_at = now() "
                "WHERE id = $1",
                run_id,
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_INVOICE_CREATED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "fixed_fee_schedule_item_id": str(draw_id),
                    "harvest_invoice_id": int(invoice["id"]),
                    "harvest_invoice_number": invoice.get("number"),
                    "planned_amount": amount,
                    "actual_amount": actual,
                    "issue_date": preview["issue_date"].isoformat(),
                    "due_date": (
                        preview["due_date"].isoformat() if preview["due_date"] else None
                    ),
                    "payment_term": preview["payment_term"],
                },
            )

    return {
        "draw_id": draw_id,
        "billing_run_id": run_id,
        "billing_run_item_id": item_id,
        "harvest_invoice_id": int(invoice["id"]),
        "harvest_invoice_number": invoice.get("number"),
        "planned_amount": amount,
        "actual_amount": actual,
        "variance": round(actual - amount, 2),
        # The dates as created, which is why they are returned rather than left
        # for the caller to infer from its preview: a preview opened yesterday
        # shows yesterday's dates, and these are the ones on the invoice.
        "issue_date": preview["issue_date"],
        "due_date": preview["due_date"],
        "payment_term": preview["payment_term"],
    }


async def _record_failure(
    pool: asyncpg.Pool,
    item_id: UUID,
    run_id: UUID,
    draw_id: UUID,
    *,
    actor: str,
    error: str,
) -> None:
    """Harvest refused. Release the lock so the draw can be fixed and retried."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE billing_run_items "
                "SET status = 'failed'::billing_run_item_status, error_message = $2 "
                "WHERE id = $1",
                item_id, error[:2000],
            )
            await conn.execute(
                "UPDATE billing_runs SET status = 'failed' WHERE id = $1", run_id
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_INVOICE_FAILED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "fixed_fee_schedule_item_id": str(draw_id),
                    "error": error[:2000],
                },
            )


async def _record_unknown(
    pool: asyncpg.Pool,
    item_id: UUID,
    run_id: UUID,
    draw_id: UUID,
    *,
    actor: str,
    cause: str,
) -> None:
    """Record that we don't know. Deliberately touches neither the item's status
    nor the run's — `in_flight` and `executing` are the accurate answers."""
    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.BILLING_INVOICE_UNKNOWN,
            actor=actor,
            payload={
                "billing_run_id": str(run_id),
                "billing_run_item_id": str(item_id),
                "fixed_fee_schedule_item_id": str(draw_id),
                "cause": cause[:2000],
                "remedy": (
                    "Check Harvest for an invoice matching this client and amount, "
                    "then resolve the in-flight row (link it, or mark it failed)."
                ),
            },
        )


# ── Group-level summary, for the monthly run ────────────────────────────────


async def group_flags(
    conn: asyncpg.Connection, group_id: UUID, *, as_of: date | None = None
) -> list[flags.Flag]:
    """What a monthly run should say about a draw-billed group.

    The run never bills these, but staying silent would let a delivered
    milestone sit unbilled indefinitely. Overdue is a day comparison — the
    schedule commits to a date, not a month.
    """
    today = as_of or date.today()
    rows = await conn.fetch(
        """
        SELECT id, description, amount, scheduled_date, released_at
        FROM fixed_fee_schedule_items
        WHERE billing_group_id = $1 AND invoiced_run_id IS NULL
        ORDER BY scheduled_date, sequence
        """,
        group_id,
    )
    pending = [r for r in rows if r["released_at"] is None]
    ready = [r for r in rows if r["released_at"] is not None]

    out: list[flags.Flag] = []
    overdue = [r for r in pending if r["scheduled_date"] < today]
    if overdue:
        out.append(flags.draws_overdue(draws=[
            {
                "id": str(r["id"]),
                "description": r["description"],
                "scheduled_date": r["scheduled_date"].isoformat(),
                "amount": float(r["amount"]),
            }
            for r in overdue
        ]))
    if pending:
        out.append(flags.draws_awaiting_release(
            count=len(pending), total=round(sum(float(r["amount"]) for r in pending), 2)
        ))
    if ready:
        out.append(flags.draws_ready_to_bill(
            count=len(ready), total=round(sum(float(r["amount"]) for r in ready), 2)
        ))
    return out
