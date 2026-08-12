"""Recurring monthly line items — retainers, hosting, management fees.

A `recurring_monthly` group carries a static set of line items that reappear
every month. Unlike T&M, Harvest generates nothing: we hand it the literal
lines. That means the group can span several projects on one invoice — hosting
against the hosting project, a service fee against a different one — which is
exactly what `line_items[].project_id` is for.

Two shapes matter operationally:

  - **Fixed lines** — a flat management fee. Amount known in advance, billed
    identically each month.
  - **Placeholder lines** — hosting pass-through, or a tooling fee that is a
    percentage of it. The description and category are stable but the amount is
    only knowable after the fact, so the line is created at zero and completed
    by hand in the Harvest draft before sending.

Effective dating lets a fee change without erasing history: supersede the old
row with an `effective_to` rather than editing it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

from app.services.billing.dates import Period
from app.services.billing.payload import render_template

logger = logging.getLogger(__name__)


@dataclass
class RecurringResolution:
    line_items: list[dict[str, Any]] = field(default_factory=list)
    """Payload-shaped lines, ready for `line_items` on POST /v2/invoices."""

    estimated_line_items: list[dict[str, Any]] = field(default_factory=list)
    """Display-shaped lines for the pre-flight table."""

    total: float = 0.0
    """Sum of non-placeholder lines. Placeholders contribute nothing."""

    placeholders: list[dict[str, Any]] = field(default_factory=list)
    invalid_kinds: list[dict[str, Any]] = field(default_factory=list)
    off_group_projects: list[dict[str, Any]] = field(default_factory=list)


async def load_active_items(
    conn: Any, billing_group_id: UUID, as_of: date
) -> list[dict[str, Any]]:
    """Line items in force for the service period.

    Comparison is **month-granular**: any day within a month means that whole
    month. Billing is monthly, and the UI presents these as "first / last month
    billed", so a mid-month `effective_from` must not silently skip the month it
    names. Null bounds are open-ended; superseded rows stay in the table and
    simply stop matching.
    """
    rows = await conn.fetch(
        """
        SELECT id, harvest_project_id, description, quantity, unit_price, kind,
               is_placeholder, sort_order, effective_from, effective_to
        FROM recurring_line_items
        WHERE billing_group_id = $1
          AND (effective_from IS NULL
               OR date_trunc('month', effective_from) <= date_trunc('month', $2::date))
          AND (effective_to IS NULL
               OR date_trunc('month', effective_to) >= date_trunc('month', $2::date))
        ORDER BY sort_order, id
        """,
        billing_group_id, as_of,
    )
    return [dict(r) for r in rows]


async def resolve(
    conn: Any,
    *,
    billing_group_id: UUID,
    period: Period,
    client_name: str,
    group_project_ids: list[int],
    valid_kinds: set[str],
) -> RecurringResolution:
    """Turn configured line items into payload + display lines for one period."""
    res = RecurringResolution()
    items = await load_active_items(conn, billing_group_id, period.start)

    for item in items:
        description = render_template(
            item["description"], client_name=client_name, period_label=period.label
        )
        quantity = float(item["quantity"])
        unit_price = 0.0 if item["is_placeholder"] else float(item["unit_price"])
        amount = round(quantity * unit_price, 2)
        kind = item["kind"]

        if kind not in valid_kinds:
            res.invalid_kinds.append({"description": description, "kind": kind})

        # Harvest 422s if a line's project doesn't belong to the invoice client.
        # The group's own projects are already client-validated at config time,
        # so checking membership here is enough.
        if item["harvest_project_id"] not in group_project_ids:
            res.off_group_projects.append({
                "description": description,
                "harvest_project_id": item["harvest_project_id"],
            })

        res.line_items.append({
            "project_id": item["harvest_project_id"],
            "kind": kind,
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
        })

        res.estimated_line_items.append({
            "label": description,
            "detail": (
                f"{kind} · amount entered in Harvest"
                if item["is_placeholder"] else kind
            ),
            "quantity": quantity,
            "unit": "ea",
            "unit_price": unit_price,
            "amount": amount,
        })

        if item["is_placeholder"]:
            res.placeholders.append({"description": description, "kind": kind})
        else:
            res.total += amount

    res.total = round(res.total, 2)
    return res


async def load_valid_kinds(conn: Any) -> set[str]:
    rows = await conn.fetch("SELECT name FROM harvest_invoice_item_categories")
    return {r["name"] for r in rows}
