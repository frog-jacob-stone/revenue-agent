"""Invoices this system created, across every kind of run.

The ledger (`billing_run_items`) is already the record: one row per invoice, with
what was planned, what Harvest returned, and which run produced it. This module
reads it back as a flat list rather than making the caller walk runs — a person
asking "what have we drafted?" is not asking about runs, and the answer spans a
draw drafted on the 12th and a monthly run executed on the 1st.

Deliberately kind-agnostic. Draws are the only thing that can produce a `created`
row today, but nothing here knows that: when monthly execution ships its rows
appear in this list without a change, because `kind` is a column on the run rather
than a branch in the query.

Read-only. Nothing in this module writes, and it never calls Harvest — every
column was captured at creation time.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from app.services.billing.errors import BillingConfigError

# `created` is the only status that means an invoice exists in Harvest. `failed`
# and `in_flight` rows are attempts, and they belong to the run record and the
# in-flight queue respectively — surfacing them under "Drafted" would overstate
# what a client has been sent.
STATUSES = ("created", "failed", "in_flight")

KINDS = ("monthly", "draw")


class InvoiceQueryError(BillingConfigError):
    """The requested filter is not one this list supports."""


async def list_created_invoices(
    pool: asyncpg.Pool,
    *,
    kind: str | None = None,
    status: str = "created",
    group_id: UUID | None = None,
    since: date | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Invoices created, newest first.

    Ordered by when the row was written rather than by issue date: issue dates are
    backdated to period boundaries for monthly runs, so ordering by them would
    interleave a July invoice created in September among July's own work. "What
    did we bill recently" means recently *created*.
    """
    if kind is not None and kind not in KINDS:
        raise InvoiceQueryError(
            f"Unknown kind {kind!r}. Expected one of: {', '.join(KINDS)}."
        )
    if status not in STATUSES:
        raise InvoiceQueryError(
            f"Unknown status {status!r}. Expected one of: {', '.join(STATUSES)}."
        )

    conditions = ["bri.status = $1::billing_run_item_status"]
    params: list[Any] = [status]

    if kind is not None:
        params.append(kind)
        conditions.append(f"r.kind = ${len(params)}::billing_run_kind")
    if group_id is not None:
        params.append(group_id)
        conditions.append(f"bri.billing_group_id = ${len(params)}")
    if since is not None:
        params.append(since)
        conditions.append(f"bri.created_at >= ${len(params)}")

    params.append(limit)
    limit_param = f"${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT bri.id AS billing_run_item_id,
               bri.billing_run_id,
               bri.billing_group_id,
               bri.status,
               bri.harvest_invoice_id,
               bri.harvest_invoice_number,
               bri.planned_amount,
               bri.actual_amount,
               bri.variance,
               bri.issue_date,
               bri.due_date,
               bri.period_start,
               bri.period_end,
               bri.created_at,
               bri.error_message,
               r.kind,
               r.run_month,
               g.name AS billing_group_name,
               g.harvest_client_id,
               g.harvest_client_name,
               g.billing_type,
               -- Null for monthly rows. Present for a draw, and the only place
               -- the milestone's name survives once it has left the queue.
               d.description AS draw_description,
               d.sequence AS draw_sequence
        FROM billing_run_items bri
        JOIN billing_runs r ON r.id = bri.billing_run_id
        LEFT JOIN billing_groups g ON g.id = bri.billing_group_id
        LEFT JOIN fixed_fee_schedule_items d
               ON d.id = bri.fixed_fee_schedule_item_id
        WHERE {' AND '.join(conditions)}
        -- `harvest_invoice_id` breaks ties rather than leaving the order to the
        -- planner. Two rows can share `created_at` — the same transaction stamps
        -- one `now()` — and without a tiebreak the list reshuffles between
        -- identical queries. Harvest ids increase, so the higher one is the later
        -- invoice, which is the order the timestamp was expressing anyway.
        ORDER BY bri.created_at DESC, bri.harvest_invoice_id DESC NULLS LAST
        LIMIT {limit_param}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def created_invoice_totals(
    pool: asyncpg.Pool, *, since: date | None = None
) -> dict[str, Any]:
    """Counts and value of what has been created, split by kind.

    `total_amount` sums `actual_amount` and falls back to `planned_amount` for
    rows an operator linked by hand without recording an amount — those keep a
    null `actual_amount` on purpose (a zero variance would read as a verified
    match), and dropping them from the total would understate what was drafted.
    """
    row = await pool.fetchrow(
        """
        SELECT
            count(*)                                        AS count,
            count(*) FILTER (WHERE r.kind = 'draw')         AS draw_count,
            count(*) FILTER (WHERE r.kind = 'monthly')      AS monthly_count,
            coalesce(sum(coalesce(bri.actual_amount, bri.planned_amount)), 0)
                                                            AS total_amount,
            count(*) FILTER (WHERE bri.actual_amount IS NULL)
                                                            AS unverified_count
        FROM billing_run_items bri
        JOIN billing_runs r ON r.id = bri.billing_run_id
        WHERE bri.status = 'created'
          AND ($1::date IS NULL OR bri.created_at >= $1::date)
        """,
        since,
    )
    return {
        "count": row["count"],
        "draw_count": row["draw_count"],
        "monthly_count": row["monthly_count"],
        "total_amount": float(row["total_amount"] or 0),
        "unverified_count": row["unverified_count"],
    }
