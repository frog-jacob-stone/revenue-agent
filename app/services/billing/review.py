"""Per-group review of a planned run: approve, leave undecided, or reject.

The pre-flight screen is where a human decides which invoices are allowed to
exist. That decision is persisted here, on the ledger row itself, so closing
the tab does not throw it away — and so the record of who decided what
survives the run.

**Three outcomes, not two.** `planned` is undecided, `approved` will be sent,
and `skipped` with a `rejected_by` will not — see `set_item_rejection` for why
un-approving is not enough on its own.

Four rules the service layer owns, not the UI:

  - **Nothing is approved by default.** The planner writes `planned`; only an
    explicit human action moves a row to `approved`.
  - **An error-severity flag blocks approval until it is overridden**, and
    flags in `flags.NON_OVERRIDABLE` (today: `UNRESOLVED_IN_FLIGHT`) can never
    be overridden at all — overriding those risks a duplicate invoice, which is
    the failure the whole in-flight protocol exists to prevent.
  - **An undecided placeholder blocks approval, and cannot be overridden.**
    Not a flag: flags are frozen at plan time, and this one changes as the
    operator works, so it is derived live from the ledger row's own line items
    (`_unresolved_placeholders`). Nor is it in `flags.NON_OVERRIDABLE`, since
    there is no flag to name — `PLACEHOLDER_LINE_ITEMS` stays `info` and stays
    a record of what the plan contained. Deliberately no override path: the
    entire point of a placeholder is that it cannot be forgotten, and an
    override is a way to forget it with a click.
  - **Rejecting requires a reason; un-rejecting requires having rejected.**
    A rejection is the only record of why a planned client got no invoice, and
    a planner-skipped row (nothing to bill) is not something a click should be
    able to turn into one.

**Review outlives the first batch.** `execute` drafts the groups approved at
the moment of the click and leaves the rest planned, which puts the run back in
`awaiting_approval` — so these same four rules govern the second batch and the
third. `close_run` at the bottom of this module is how an operator says there
will not be another one.

Review state here is not the Unbreakable Rule #1 approval chain. Since
[ADR-0004](../../../docs/adr/0004-operator-initiated-writes.md) the monthly
write is operator-initiated and carries no `approvals` row at all — this
selection, plus the payload shown on the pre-flight, *is* the authorization.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.billing import flags, write_protocol

logger = logging.getLogger(__name__)

# Run states in which the plan is still under review.
_REVIEWABLE_RUN = ("planning", "awaiting_approval")
# Rejection is allowed for longer than approval is. A run that halted on an
# unknown outcome sits in `executing` with its remaining groups still
# `approved`, and the operator resolving that halt may well decide one of them
# should not go out after all. Approval is a narrower gesture and keeps the
# narrower gate: once execution has begun, what was approved is what was
# approved.
_REJECTABLE_RUN = ("planning", "awaiting_approval", "executing")
# Item states that can move between approved and unapproved.
_REVIEWABLE_ITEM = ("planned", "approved")
# Closing follows rejection rather than approval, and for the same reason: the
# operator settling a run that stopped mid-batch is exactly the person who may
# decide the groups it never reached are not going out at all. The in-flight
# row itself still has to be resolved first — see `close_run`.
_CLOSEABLE_RUN = ("awaiting_approval", "executing")


class ApprovalError(Exception):
    """The requested approval transition is not permitted."""


async def _blocking_flags(conn: Any, item_id: UUID) -> list[str]:
    """Error-severity flag codes on an item that forbid approval outright."""
    rows = await conn.fetch(
        "SELECT code FROM billing_run_flags "
        "WHERE billing_run_item_id = $1 AND severity = 'error'",
        item_id,
    )
    return [r["code"] for r in rows if r["code"] in flags.NON_OVERRIDABLE]


async def _has_error_flag(conn: Any, item_id: UUID) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM billing_run_flags "
        "WHERE billing_run_item_id = $1 AND severity = 'error' LIMIT 1",
        item_id,
    ))


async def _unresolved_placeholders(conn: Any, item_id: UUID) -> list[str]:
    """Descriptions of the placeholder lines still awaiting a decision.

    Derived from the ledger row rather than stored alongside it: the operator
    resolves placeholders one at a time and each resolution rewrites
    `estimated_line_items`, so reading from there is the one place this cannot
    fall out of step with what is on screen.
    """
    rows = await conn.fetch(
        """
        SELECT e->>'label' AS label
        FROM billing_run_items i,
             jsonb_array_elements(i.estimated_line_items) e
        WHERE i.id = $1 AND e->>'placeholder_state' = 'unresolved'
        """,
        item_id,
    )
    return [r["label"] for r in rows]


async def _load_item(conn: Any, run_id: UUID, item_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT i.id, i.status, i.error_override, i.billing_group_id, i.rejected_by,
               r.status AS run_status, g.name AS billing_group_name
        FROM billing_run_items i
        JOIN billing_runs r ON r.id = i.billing_run_id
        JOIN billing_groups g ON g.id = i.billing_group_id
        WHERE i.billing_run_id = $1 AND i.id = $2
        FOR UPDATE OF i
        """,
        run_id, item_id,
    )


async def set_item_approval(
    pool: asyncpg.Pool,
    run_id: UUID,
    item_id: UUID,
    *,
    approved: bool | None = None,
    override: bool | None = None,
    actor: str = "system",
) -> bool:
    """Approve, un-approve, and/or record an error override for one group.

    Both fields are optional so the two operator gestures — "I accept this
    error" and "I approve this invoice" — can be recorded independently.
    Returns False if the item does not belong to the run.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await _load_item(conn, run_id, item_id)
            if item is None:
                return False
            if item["run_status"] not in _REVIEWABLE_RUN:
                raise ApprovalError(
                    f"run is {item['run_status']}; approval can only change while "
                    "it is under review"
                )
            if item["status"] not in _REVIEWABLE_ITEM:
                raise ApprovalError(
                    f"this group is {item['status']}; only a planned or approved "
                    "group can change approval"
                )

            effective_override = item["error_override"]

            if override is not None and override != effective_override:
                if override:
                    blocking = await _blocking_flags(conn, item_id)
                    if blocking:
                        raise ApprovalError(
                            f"{', '.join(blocking)} cannot be overridden — resolve it "
                            "instead; approving risks a duplicate invoice"
                        )
                await conn.execute(
                    "UPDATE billing_run_items SET error_override = $2, updated_at = now() "
                    "WHERE id = $1",
                    item_id, override,
                )
                effective_override = override
                await audit.write_audit_event(
                    conn,
                    events.BILLING_ITEM_OVERRIDDEN,
                    actor=actor,
                    payload={
                        "billing_run_id": str(run_id),
                        "billing_run_item_id": str(item_id),
                        "billing_group": item["billing_group_name"],
                        "override": override,
                    },
                )

            if approved is None or approved == (item["status"] == "approved"):
                return True

            if approved:
                blocking = await _blocking_flags(conn, item_id)
                if blocking:
                    raise ApprovalError(
                        f"{', '.join(blocking)} blocks approval and is not "
                        "overridable — resolve it first"
                    )
                undecided = await _unresolved_placeholders(conn, item_id)
                if undecided:
                    n = len(undecided)
                    listed = ", ".join(f"“{d}”" for d in undecided)
                    raise ApprovalError(
                        f"{n} placeholder line item{'' if n == 1 else 's'} still "
                        f"need{'s' if n == 1 else ''} an amount, or an explicit "
                        f"omit for this month: {listed}. Not overridable — enter "
                        f"or omit each one, which takes a moment and is the "
                        f"reason the placeholder is there."
                    )
                if await _has_error_flag(conn, item_id) and not effective_override:
                    raise ApprovalError(
                        "this group carries an error-severity flag; record an "
                        "override before approving"
                    )
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'approved', approved_at = now(), approved_by = $2,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    item_id, actor,
                )
            else:
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'planned', approved_at = NULL, approved_by = NULL,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    item_id,
                )

            await audit.write_audit_event(
                conn,
                events.BILLING_ITEM_APPROVED if approved else events.BILLING_ITEM_UNAPPROVED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": item["billing_group_name"],
                    "error_override": effective_override,
                },
            )
    return True


async def set_item_rejection(
    pool: asyncpg.Pool,
    run_id: UUID,
    item_id: UUID,
    *,
    rejected: bool,
    reason: str | None = None,
    actor: str = "system",
) -> bool:
    """Decide against invoicing one group this run, or undo that decision.

    **Rejection is not un-approval.** Un-approving returns a group to
    *undecided*: the row stays live, it still counts against the month's one
    live row per group, and the pre-flight keeps offering it. Rejection says
    the invoice is not going out — most often because it already went out, by
    hand, days earlier, which is the one case where drafting again would
    produce a real duplicate at a real client.

    **It reuses `skipped` rather than adding a status.** `skipped` already
    means "this run will not bill this group", it is already excluded from
    `billing_run_items_one_live_per_month` (so a rejection releases the month's
    slot, which is right — the group is free to be planned again), and the
    pre-flight already has a Skipped section. `rejected_by` is what separates
    the operator's decision from the planner finding nothing to bill, and it is
    also the guard on undo: a planner-skipped row must not be un-skipped into
    an invoice nobody planned.

    **The reason is required.** This row is the entire record of why a client
    did not receive an invoice their run had planned. A blank one turns the
    Skipped list into a list of names a month later, which is when someone asks.

    Returns False if the item does not belong to the run.
    """
    if rejected and not (reason or "").strip():
        raise ApprovalError(
            "a reason is required to reject — it is the only record of why this "
            "client did not get an invoice"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await _load_item(conn, run_id, item_id)
            if item is None:
                return False
            if item["run_status"] not in _REJECTABLE_RUN:
                raise ApprovalError(
                    f"run is {item['run_status']}; rejection can only change while "
                    "it is under review or mid-execution"
                )

            if rejected:
                if item["status"] not in _REVIEWABLE_ITEM:
                    raise ApprovalError(
                        f"this group is {item['status']}; only a planned or approved "
                        "group can be rejected"
                    )
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'skipped', skip_reason = $2,
                        rejected_at = now(), rejected_by = $3,
                        approved_at = NULL, approved_by = NULL, updated_at = now()
                    WHERE id = $1
                    """,
                    item_id, (reason or "").strip(), actor,
                )
            else:
                if item["status"] != "skipped" or item["rejected_by"] is None:
                    raise ApprovalError(
                        "only a group an operator rejected can be un-rejected; a "
                        "group the planner skipped had nothing to bill, so re-plan "
                        "the run instead"
                    )
                try:
                    await conn.execute(
                        """
                        UPDATE billing_run_items
                        SET status = 'planned', skip_reason = NULL,
                            rejected_at = NULL, rejected_by = NULL, updated_at = now()
                        WHERE id = $1
                        """,
                        item_id,
                    )
                except asyncpg.UniqueViolationError as exc:
                    # `billing_run_items_one_live_per_month`. Rejecting released
                    # the month's slot and something has since taken it — a
                    # re-plan, most likely. Undoing here would mean two live
                    # rows for one group in one month, which is the double-bill
                    # the index exists to stop.
                    raise ApprovalError(
                        f"“{item['billing_group_name']}” already has a live row for "
                        "this month in another run — that row is the one to work "
                        "with, not this rejected one."
                    ) from exc

            await audit.write_audit_event(
                conn,
                events.BILLING_ITEM_REJECTED if rejected else events.BILLING_ITEM_UNREJECTED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": item["billing_group_name"],
                    "reason": (reason or "").strip() or None,
                },
            )
    return True


async def set_all_approvals(
    pool: asyncpg.Pool, run_id: UUID, *, approved: bool, actor: str = "system"
) -> int:
    """Bulk approve or clear.

    Approving in bulk touches only the groups that are already approvable — it
    never silently overrides an error flag. Clearing touches everything
    approved. Returns the number of rows changed.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            run_status = await conn.fetchval(
                "SELECT status FROM billing_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if run_status is None:
                return 0
            if run_status not in _REVIEWABLE_RUN:
                raise ApprovalError(
                    f"run is {run_status}; approval can only change while it is "
                    "under review"
                )

            if approved:
                rows = await conn.fetch(
                    """
                    UPDATE billing_run_items i
                    SET status = 'approved', approved_at = now(), approved_by = $2,
                        updated_at = now()
                    WHERE i.billing_run_id = $1
                      AND i.status = 'planned'
                      AND (
                        i.error_override
                        OR NOT EXISTS (
                            SELECT 1 FROM billing_run_flags f
                            WHERE f.billing_run_item_id = i.id AND f.severity = 'error'
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM billing_run_flags f
                        WHERE f.billing_run_item_id = i.id
                          AND f.code = ANY($3::text[])
                      )
                      -- Skipped rather than refused, matching how this treats
                      -- an un-overridden error flag: "Approve all" approves what
                      -- is approvable, and the rest keep saying why they aren't.
                      AND NOT EXISTS (
                        SELECT 1 FROM jsonb_array_elements(i.estimated_line_items) e
                        WHERE e->>'placeholder_state' = 'unresolved'
                      )
                    RETURNING i.id
                    """,
                    run_id, actor, list(flags.NON_OVERRIDABLE),
                )
            else:
                rows = await conn.fetch(
                    """
                    UPDATE billing_run_items
                    SET status = 'planned', approved_at = NULL, approved_by = NULL,
                        updated_at = now()
                    WHERE billing_run_id = $1 AND status = 'approved'
                    RETURNING id
                    """,
                    run_id,
                )

            if not rows:
                return 0
            await audit.write_audit_event(
                conn,
                events.BILLING_ITEM_APPROVED if approved else events.BILLING_ITEM_UNAPPROVED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "bulk": True,
                    "count": len(rows),
                    "billing_run_item_ids": [str(r["id"]) for r in rows],
                },
            )
    return len(rows)


DEFAULT_CLOSE_REASON = "Not billed on this run — the operator closed the run."


async def close_run(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Finish a partially-drafted run: nothing further goes out from it.

    Since drafting is a batch (`execute`), a run whose operator drafted some
    groups and not others sits in `awaiting_approval` with the rest still
    planned — which is right while they are still deciding, and wrong once they
    have decided. This is that decision, made once for the whole remainder
    rather than group by group.

    **The remainder is rejected, not left dangling.** Setting the run status
    alone would leave live `planned` rows holding the month's one-live-row-per-
    group slot, so those groups could not be planned into a later run — the
    exact thing an operator who just closed the run is likely to want next.
    They land in `skipped` with `rejected_by`, which is what `set_item_rejection`
    writes and what the pre-flight's Skipped section already renders.

    **A halted run must be resolved before it can be closed.** An `in_flight`
    row may be a real invoice, and a run that ends with one unaccounted for has
    not ended. Resolve it, then close what it stopped.

    **A run that has drafted nothing is not closed, it is abandoned.** Closing
    is the record of "these groups were considered and not billed" alongside
    invoices that did go out; `planner.abandon_run` is the record of a plan
    thrown away whole, and it is the right verb when there is nothing to stand
    beside.

    Returns the count skipped and the run's settled status.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                "SELECT status, kind FROM billing_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if run is None:
                return {}
            if run["kind"] != "monthly":
                raise ApprovalError(
                    "a draw run bills one milestone and finishes on its own; there "
                    "is no remainder to close"
                )
            if run["status"] not in _CLOSEABLE_RUN:
                raise ApprovalError(
                    f"run is {run['status']}; only a run between batches, or one "
                    "stopped mid-batch, can be closed"
                )

            counts = await conn.fetchrow(
                """
                SELECT count(*) FILTER (WHERE status = 'in_flight') AS in_flight,
                       count(*) FILTER (WHERE status IN ('created','failed')) AS attempted
                FROM billing_run_items WHERE billing_run_id = $1
                """,
                run_id,
            )
            if counts["in_flight"]:
                raise ApprovalError(
                    "an invoice from this run is in flight — nobody knows whether "
                    "Harvest created it. Resolve that row before closing the run."
                )
            if not counts["attempted"]:
                raise ApprovalError(
                    "this run has drafted nothing, so there is nothing for the "
                    "remainder to stand beside — abandon the run instead"
                )

            skipped = await conn.fetch(
                """
                UPDATE billing_run_items
                SET status = 'skipped', skip_reason = $2,
                    rejected_at = now(), rejected_by = $3,
                    approved_at = NULL, approved_by = NULL, updated_at = now()
                WHERE billing_run_id = $1 AND status IN ('planned', 'approved')
                RETURNING id
                """,
                run_id, (reason or "").strip() or DEFAULT_CLOSE_REASON, actor,
            )
            status = await write_protocol.settle_run_status(conn, run_id)
            await audit.write_audit_event(
                conn,
                events.BILLING_RUN_CLOSED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "status": status,
                    "skipped_count": len(skipped),
                    "reason": (reason or "").strip() or DEFAULT_CLOSE_REASON,
                    "billing_run_item_ids": [str(r["id"]) for r in skipped],
                },
            )
    return {"billing_run_id": run_id, "status": status, "skipped": len(skipped)}
