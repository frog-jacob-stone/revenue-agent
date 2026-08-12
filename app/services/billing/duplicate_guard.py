"""Duplicate-invoice detection.

Two layers:

  1. **Ledger** — an unresolved `in_flight` row for this group. We don't know
     whether Harvest created that invoice, so planning is blocked until a human
     says. (The partial unique index enforces this at the DB level too; this
     produces the readable flag.)

  2. **Harvest** — an invoice already exists for this client in the period
     window.

Layer 2 needs care. `GET /v2/invoices` filters by **client**, not by billing
group, so a client with more than one group will legitimately have several
invoices in the window — one per group. Flagging on client alone would fire on
every multi-group client every month, and a warning that always fires is a
warning nobody reads.

So: cross-reference the ledger and flag only invoices this system did not
create. An invoice we recorded is expected. One we didn't — created by hand in
the Harvest UI, or by an earlier run whose ledger row was lost — is worth a
human look.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from app.config import Settings
from app.integrations import harvest

logger = logging.getLogger(__name__)


async def find_unresolved_in_flight(
    conn: Any, billing_group_id: UUID
) -> dict[str, Any] | None:
    """The poison pill: an in-flight row from any prior run for this group."""
    row = await conn.fetchrow(
        """
        SELECT i.id, i.billing_run_id, i.run_month, i.error_message, r.run_month AS run_label
        FROM billing_run_items i
        JOIN billing_runs r ON r.id = i.billing_run_id
        WHERE i.billing_group_id = $1 AND i.status = 'in_flight'
        ORDER BY i.created_at
        LIMIT 1
        """,
        billing_group_id,
    )
    return dict(row) if row else None


async def find_created_this_month(
    conn: Any, billing_group_id: UUID, run_month: date
) -> dict[str, Any] | None:
    """A successfully created invoice already exists for this group this month.

    Re-planning it would double-bill. Constraint C6 would reject the row
    anyway; finding it first turns a raw unique violation into a readable flag.
    """
    row = await conn.fetchrow(
        """
        SELECT id, harvest_invoice_id, harvest_invoice_number, actual_amount
        FROM billing_run_items
        WHERE billing_group_id = $1 AND run_month = $2 AND status = 'created'
        LIMIT 1
        """,
        billing_group_id, run_month,
    )
    return dict(row) if row else None


async def find_unrecognized_harvest_invoices(
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    harvest_client_id: int,
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    """Invoices in the window that this system has no ledger row for.

    Invoices we created are expected — including the ones belonging to a
    *different* billing group of the same client.
    """
    invoices = await harvest.list_invoices(
        cfg,
        client_id=harvest_client_id,
        from_=window_start.isoformat(),
        to=window_end.isoformat(),
    )
    if not invoices:
        return []

    known = {
        r["harvest_invoice_id"]
        for r in await pool.fetch(
            "SELECT harvest_invoice_id FROM billing_run_items "
            "WHERE harvest_invoice_id = ANY($1::bigint[])",
            [int(i["id"]) for i in invoices if i.get("id") is not None],
        )
    }
    unrecognized = [i for i in invoices if int(i.get("id", 0)) not in known]
    if unrecognized:
        logger.info(
            "duplicate guard: %d of %d invoices for client %s are not in the ledger",
            len(unrecognized), len(invoices), harvest_client_id,
        )
    return unrecognized
