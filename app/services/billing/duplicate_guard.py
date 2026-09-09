"""Duplicate-invoice detection.

Two layers:

  1. **Ledger** — an unresolved `in_flight` row for this group. We don't know
     whether Harvest created that invoice, so planning is blocked until a human
     says. (The partial unique index enforces this at the DB level too; this
     produces the readable flag.)

  2. **Harvest** — an invoice already exists for this group's projects in the
     period window.

Layer 2 needs care, and got it wrong the first time. `GET /v2/invoices` filters
by **client**, not by billing group, so a client with more than one engagement
will legitimately have several invoices in the window. The original version
cross-referenced the ledger and flagged whatever this system had not created —
which still fired on every hand-made invoice for a *different* project of the
same client, monthly, on exactly the clients most likely to have one. A warning
that always fires is a warning nobody reads.

Two filters now, in order:

  1. **Drop invoices we created.** A ledger row means it was expected,
     including one belonging to another group of the same client.
  2. **Keep only invoices that touch this group's projects.** Every invoice
     line item carries a `project`, whether Harvest generated it from
     `line_items_import` or a human typed it in the UI against a project. An
     invoice whose lines all point elsewhere is another engagement's business.

An invoice with no project on any line is the residue: hand-typed, attributable
to nobody. It is returned separately rather than dropped, because a
hand-created duplicate is precisely what this guard exists to catch, and the
caller flags it at `info` — visible, blocking nothing.
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


def invoice_project_ids(invoice: dict[str, Any]) -> set[int]:
    """Every project referenced by an invoice's line items.

    Empty means the invoice carries no project attribution at all — its lines
    were typed by hand with no project attached, so it cannot be assigned to a
    billing group or ruled out of one.
    """
    found: set[int] = set()
    for line in invoice.get("line_items") or []:
        project = line.get("project") or {}
        pid = project.get("id")
        if pid is not None:
            found.add(int(pid))
    return found


async def find_unrecognized_harvest_invoices(
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    harvest_client_id: int,
    project_ids: list[int],
    window_start: date,
    window_end: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(on_this_group, unattributable)` — invoices in the window with no ledger row.

    The first list touches at least one of `project_ids` and is the real
    duplicate signal. The second carries no project on any line and so cannot
    be placed. Invoices belonging wholly to another engagement of the same
    client appear in neither; see the module docstring.
    """
    invoices = await harvest.list_invoices(
        cfg,
        client_id=harvest_client_id,
        from_=window_start.isoformat(),
        to=window_end.isoformat(),
    )
    if not invoices:
        return [], []

    known = {
        r["harvest_invoice_id"]
        for r in await pool.fetch(
            "SELECT harvest_invoice_id FROM billing_run_items "
            "WHERE harvest_invoice_id = ANY($1::bigint[])",
            [int(i["id"]) for i in invoices if i.get("id") is not None],
        )
    }
    unrecognized = [i for i in invoices if int(i.get("id", 0)) not in known]

    ours = set(project_ids)
    on_this_group: list[dict[str, Any]] = []
    unattributable: list[dict[str, Any]] = []
    for invoice in unrecognized:
        touched = invoice_project_ids(invoice)
        if not touched:
            unattributable.append(invoice)
        elif touched & ours:
            on_this_group.append(invoice)

    if unrecognized:
        logger.info(
            "duplicate guard: %d of %d invoices for client %s are not in the ledger; "
            "%d touch this group's projects, %d carry no project",
            len(unrecognized), len(invoices), harvest_client_id,
            len(on_this_group), len(unattributable),
        )
    return on_this_group, unattributable
