"""Placeholder resolution — the operator prices a line, or omits it for a month.

A placeholder line item (`recurring_line_items.is_placeholder`) has a stable
description, category, and project but an amount that is only knowable after the
fact: hosting pass-through, a tooling fee that is a percentage of it, a retainer
overage. This module is where that amount gets decided.

It used to be decided in the Harvest draft. That put the last step of an invoice
in a system this one cannot read, so nothing could notice when it was skipped —
the invoice went out short while `planned_amount` still read as correct, because
the placeholder had been deliberately excluded from it. Now the decision is
recorded here and `review.py` refuses to approve an invoice with any placeholder
still undecided.

**Omitting is a decision, not an absence.** A retainer overage is configured
precisely so that it comes up every month; most months there is no overage. So
`omitted` drops the line from this month's payload and leaves the template
alone — next month it is back, asking again. The line stays visible in the
pre-flight, struck through, because a placeholder that vanished when omitted
would stop being the reminder it exists to be.

Two properties worth stating, because both are load-bearing:

**The rebuild reads the ledger row, never live config.** `planned_payload` and
`estimated_line_items` were frozen when the run was planned, and the operator has
reviewed them. Re-running `recurring.resolve()` here would silently pull in any
config edited since — a fee changed, a line added — turning a resolution into an
unreviewed re-plan. So the payload is rebuilt from the row's own annotated
`estimated_line_items`, which `planner` writes complete enough to reconstruct
every line from (see `models.billing.EstimatedLineItem`).

**Resolving un-approves.** The payload changed, so an approval recorded against
the old one no longer describes what would be sent — and ADR-0004 condition 1
requires the operator to have seen the exact payload. Cheaper to make them click
approve again than to let the two drift apart.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.billing import recurring
from app.services.billing.dates import month_label

logger = logging.getLogger(__name__)

# Mirrors `review._REVIEWABLE_RUN` / `_REVIEWABLE_ITEM`: a resolution edits the
# plan, so it is allowed exactly while the plan is still under review.
_REVIEWABLE_RUN = ("planning", "awaiting_approval")
_REVIEWABLE_ITEM = ("planned", "approved")


class PlaceholderError(Exception):
    """The requested resolution is not permitted."""


class PlaceholderNotFound(Exception):
    """No such placeholder line on this planned invoice."""


async def _load_item(
    conn: asyncpg.Connection, run_id: UUID, item_id: UUID
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT i.id, i.status, i.run_month, i.period_start, i.planned_payload,
               i.estimated_line_items, i.billing_group_id,
               r.status AS run_status, g.name AS billing_group_name,
               g.billing_type
        FROM billing_run_items i
        JOIN billing_runs r ON r.id = i.billing_run_id
        JOIN billing_groups g ON g.id = i.billing_group_id
        WHERE i.billing_run_id = $1 AND i.id = $2
        FOR UPDATE OF i
        """,
        run_id, item_id,
    )


def _guard(item: asyncpg.Record) -> None:
    # Billing type first, deliberately. A T&M group is also usually `skipped`,
    # and "this group is skipped" would send the caller looking at run state
    # when the real answer is that placeholders do not exist here at all.
    if item["billing_type"] != "recurring_monthly":
        raise PlaceholderError(
            f"{item['billing_group_name']} is a {item['billing_type']} group; "
            "only recurring monthly groups carry placeholder line items"
        )
    if item["run_status"] not in _REVIEWABLE_RUN:
        raise PlaceholderError(
            f"run is {item['run_status']}; placeholder amounts can only change "
            "while it is under review"
        )
    if item["status"] not in _REVIEWABLE_ITEM:
        raise PlaceholderError(
            f"this group is {item['status']}; only a planned or approved group "
            "can have its placeholders changed"
        )


def _find_placeholder(
    lines: list[dict[str, Any]], line_item_id: UUID
) -> dict[str, Any]:
    """The entry this resolution applies to, or a 404.

    Checking against the row's own line items — rather than against config — is
    what stops a resolution being recorded for a line this plan never included,
    e.g. one whose effective window does not cover the period.
    """
    target = str(line_item_id)
    for line in lines:
        if line.get("recurring_line_item_id") == target and line.get("is_placeholder"):
            return line
    raise PlaceholderNotFound(
        f"No placeholder line item {line_item_id} on this planned invoice."
    )


def _rebuild(
    lines: list[dict[str, Any]], payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
    """Derive the payload lines and the total from the annotated display lines.

    Every other key on the payload — subject, dates, notes, purchase order — is
    left exactly as planned. Only the line items are ours to touch.
    """
    rebuilt = [
        {
            "project_id": line["harvest_project_id"],
            "kind": line["kind"],
            "description": line["label"],
            "quantity": line["quantity"],
            "unit_price": line["unit_price"],
        }
        for line in lines
        if line.get("placeholder_state") != "omitted"
    ]
    total = round(
        sum(
            float(line["amount"]) for line in lines
            if line.get("placeholder_state") != "omitted"
        ),
        2,
    )
    return rebuilt, {**payload, "line_items": rebuilt}, total


async def _apply(
    conn: asyncpg.Connection,
    item: asyncpg.Record,
    line: dict[str, Any],
    *,
    state: str | None,
    quantity: float,
    unit_price: float,
) -> tuple[float, list[dict[str, Any]]]:
    """Patch one display line, rebuild the payload, and write both back."""
    lines = [dict(entry) for entry in item["estimated_line_items"]]
    target = next(
        entry for entry in lines
        if entry.get("recurring_line_item_id") == line["recurring_line_item_id"]
    )
    target["quantity"] = quantity
    target["unit_price"] = unit_price
    target["amount"] = round(quantity * unit_price, 2)
    target["placeholder_state"] = state
    target["detail"] = recurring.line_detail(
        target.get("kind") or "", state, month_label(item["period_start"])
    )

    _, payload, total = _rebuild(lines, dict(item["planned_payload"]))

    await conn.execute(
        """
        UPDATE billing_run_items
        SET estimated_line_items = $2, planned_payload = $3, planned_amount = $4,
            updated_at = now()
        WHERE id = $1
        """,
        item["id"], lines, payload, Decimal(str(total)),
    )
    return total, lines


async def _unapprove_if_needed(
    conn: asyncpg.Connection, item: asyncpg.Record, *, actor: str
) -> bool:
    """An approval describes a payload. Change the payload, lose the approval."""
    if item["status"] != "approved":
        return False
    await conn.execute(
        """
        UPDATE billing_run_items
        SET status = 'planned', approved_at = NULL, approved_by = NULL,
            updated_at = now()
        WHERE id = $1
        """,
        item["id"],
    )
    await audit.write_audit_event(
        conn,
        events.BILLING_ITEM_UNAPPROVED,
        actor=actor,
        payload={
            "billing_run_item_id": str(item["id"]),
            "billing_group": item["billing_group_name"],
            "reason": "placeholder amount changed after approval",
        },
    )
    return True


async def set_resolution(
    pool: asyncpg.Pool,
    run_id: UUID,
    item_id: UUID,
    line_item_id: UUID,
    *,
    resolution: str,
    unit_price: float | None = None,
    quantity: float | None = None,
    note: str | None = None,
    actor: str = "system",
) -> bool:
    """Price a placeholder, or omit it for this run month.

    Returns False if the item does not belong to the run. Raises
    `PlaceholderError` when the plan is no longer editable, and
    `PlaceholderNotFound` when the line is not a placeholder on this invoice.
    """
    if resolution not in ("amount", "omitted"):
        raise PlaceholderError(
            f"'{resolution}' is not a resolution; expected 'amount' or 'omitted'"
        )
    if resolution == "amount" and unit_price is None:
        # A missing price must not land as a silent $0 — that is
        # indistinguishable from unresolved, and bills the client nothing.
        raise PlaceholderError("an amount resolution needs a unit price")

    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await _load_item(conn, run_id, item_id)
            if item is None:
                return False
            _guard(item)
            line = _find_placeholder(item["estimated_line_items"], line_item_id)

            await conn.execute(
                """
                INSERT INTO recurring_line_item_resolutions
                    (recurring_line_item_id, run_month, resolution, quantity,
                     unit_price, note, resolved_by)
                VALUES ($1,$2,$3::recurring_line_item_resolution,$4,$5,$6,$7)
                ON CONFLICT (recurring_line_item_id, run_month) DO UPDATE
                SET resolution = excluded.resolution,
                    quantity = excluded.quantity,
                    unit_price = excluded.unit_price,
                    note = excluded.note,
                    resolved_by = excluded.resolved_by,
                    resolved_at = now()
                """,
                line_item_id, item["run_month"], resolution,
                None if quantity is None else Decimal(str(quantity)),
                None if unit_price is None else Decimal(str(unit_price)),
                note, actor,
            )

            if resolution == "omitted":
                # Zeroed so the struck-through row reads as billing nothing,
                # rather than showing a price it will not charge.
                state, new_quantity, new_price = "omitted", float(line["quantity"]), 0.0
            else:
                state = "resolved"
                new_quantity = (
                    float(quantity) if quantity is not None
                    else float(line["quantity"])
                )
                new_price = float(unit_price)  # type: ignore[arg-type]

            total, _ = await _apply(
                conn, item, line,
                state=state, quantity=new_quantity, unit_price=new_price,
            )
            unapproved = await _unapprove_if_needed(conn, item, actor=actor)

            await audit.write_audit_event(
                conn,
                events.BILLING_PLACEHOLDER_RESOLVED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": item["billing_group_name"],
                    "recurring_line_item_id": str(line_item_id),
                    "description": line["label"],
                    "run_month": item["run_month"].isoformat(),
                    "resolution": resolution,
                    "quantity": new_quantity,
                    "unit_price": new_price,
                    "note": note,
                    "planned_amount": total,
                    "unapproved": unapproved,
                },
            )
    return True


async def clear_resolution(
    pool: asyncpg.Pool,
    run_id: UUID,
    item_id: UUID,
    line_item_id: UUID,
    *,
    actor: str = "system",
) -> bool:
    """Withdraw a decision, returning the line to undecided at $0.

    The line blocks approval again, which is the point: this is the retreat from
    a number entered by mistake, not a way to skip the question.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await _load_item(conn, run_id, item_id)
            if item is None:
                return False
            _guard(item)
            line = _find_placeholder(item["estimated_line_items"], line_item_id)

            await conn.execute(
                "DELETE FROM recurring_line_item_resolutions "
                "WHERE recurring_line_item_id = $1 AND run_month = $2",
                line_item_id, item["run_month"],
            )

            total, _ = await _apply(
                conn, item, line,
                state="unresolved",
                quantity=float(line["quantity"]),
                unit_price=0.0,
            )
            unapproved = await _unapprove_if_needed(conn, item, actor=actor)

            await audit.write_audit_event(
                conn,
                events.BILLING_PLACEHOLDER_CLEARED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": item["billing_group_name"],
                    "recurring_line_item_id": str(line_item_id),
                    "description": line["label"],
                    "run_month": item["run_month"].isoformat(),
                    "planned_amount": total,
                    "unapproved": unapproved,
                },
            )
    return True
