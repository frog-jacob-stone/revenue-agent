"""Recurring monthly line items — retainers, hosting, management fees.

A `recurring_monthly` group carries a static set of line items that reappear
every month. Unlike T&M, Harvest generates nothing: we hand it the literal
lines. That means the group can span several projects on one invoice — hosting
against the hosting project, a service fee against a different one — which is
exactly what `line_items[].project_id` is for.

Two shapes matter operationally:

  - **Fixed lines** — a flat management fee. Amount known in advance, billed
    identically each month.
  - **Placeholder lines** — hosting pass-through, a tooling fee that is a
    percentage of it, a retainer overage. The description and category are
    stable but the amount is only knowable after the fact, so the operator
    decides it per month on the pre-flight: either an amount, or an explicit
    omit for that month. Until they do, the line plans at zero, contributes
    nothing to the total, and blocks approval of the invoice.

Why the decision lives here rather than in the Harvest draft (which is where it
used to): a reminder in a system this one cannot read is a reminder nothing
notices you skipped. The invoice goes out short while `planned_amount` still
reads as correct, because the placeholder was deliberately excluded from it.

Why omitting is a decision and not an absence: a retainer overage is configured
precisely so it comes up every month. Most months there is no overage, and "no
overage in August" is worth recording — it is the difference between checked and
forgotten.

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
    """Payload-shaped lines, ready for `line_items` on POST /v2/invoices.

    Omitted placeholders are absent — that is what omitting means.
    """

    estimated_line_items: list[dict[str, Any]] = field(default_factory=list)
    """Display-shaped lines for the pre-flight table.

    Carries every line including omitted ones, and enough per-line detail
    (`recurring_line_item_id`, `harvest_project_id`, `kind`) that the payload can
    be rebuilt from it later without re-reading config.
    """

    total: float = 0.0
    """Fixed lines plus resolved placeholders. Unresolved and omitted contribute
    nothing — so the total is a floor while anything is undecided, and exact once
    everything is."""

    unresolved_placeholders: list[dict[str, Any]] = field(default_factory=list)
    resolved_placeholders: list[dict[str, Any]] = field(default_factory=list)
    omitted_placeholders: list[dict[str, Any]] = field(default_factory=list)
    invalid_kinds: list[dict[str, Any]] = field(default_factory=list)
    off_group_projects: list[dict[str, Any]] = field(default_factory=list)

    @property
    def placeholders(self) -> list[dict[str, Any]]:
        """Every placeholder, in the order they appear on the invoice."""
        return sorted(
            self.unresolved_placeholders
            + self.resolved_placeholders
            + self.omitted_placeholders,
            key=lambda p: p["sort_order"],
        )


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


async def load_resolutions(
    conn: Any, billing_group_id: UUID, run_month: date
) -> dict[UUID, dict[str, Any]]:
    """The operator's placeholder decisions for this run month, by line id.

    Keyed on `run_month` rather than the service period: an arrears group's
    period is the *previous* month, but the decision belongs to the run the
    operator is looking at.
    """
    rows = await conn.fetch(
        """
        SELECT r.recurring_line_item_id, r.resolution, r.quantity, r.unit_price,
               r.note, r.resolved_by, r.resolved_at
        FROM recurring_line_item_resolutions r
        JOIN recurring_line_items l ON l.id = r.recurring_line_item_id
        WHERE l.billing_group_id = $1 AND r.run_month = $2
        """,
        billing_group_id, run_month,
    )
    return {r["recurring_line_item_id"]: dict(r) for r in rows}


async def resolve(
    conn: Any,
    *,
    billing_group_id: UUID,
    period: Period,
    run_month: date,
    client_name: str,
    group_project_ids: list[int],
    valid_kinds: set[str],
) -> RecurringResolution:
    """Turn configured line items into payload + display lines for one period."""
    res = RecurringResolution()
    items = await load_active_items(conn, billing_group_id, period.start)
    resolutions = await load_resolutions(conn, billing_group_id, run_month)

    for item in items:
        description = render_template(
            item["description"], client_name=client_name, period_label=period.label
        )
        kind = item["kind"]
        is_placeholder = item["is_placeholder"]
        # A resolution for a line that is no longer a placeholder is stale, not
        # an error: the flag can be turned off in config after the fact, and the
        # configured price is then the honest answer. The row stays, in case the
        # flag comes back on.
        resolution = resolutions.get(item["id"]) if is_placeholder else None

        quantity = float(item["quantity"])
        unit_price = 0.0 if is_placeholder else float(item["unit_price"])
        state: str | None = "unresolved" if is_placeholder else None

        if resolution is not None:
            if resolution["resolution"] == "omitted":
                state = "omitted"
            else:
                state = "resolved"
                unit_price = float(resolution["unit_price"])
                if resolution["quantity"] is not None:
                    quantity = float(resolution["quantity"])

        amount = round(quantity * unit_price, 2)

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

        # An omitted line is deliberately absent from the payload. It still
        # appears below, so the pre-flight can show it struck through — a
        # placeholder that vanished when omitted would stop being a reminder.
        if state != "omitted":
            res.line_items.append({
                "project_id": item["harvest_project_id"],
                "kind": kind,
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
            })

        res.estimated_line_items.append({
            "label": description,
            "detail": line_detail(kind, state, period.label),
            "quantity": quantity,
            "unit": "ea",
            "unit_price": unit_price,
            "amount": amount,
            "recurring_line_item_id": str(item["id"]),
            "harvest_project_id": item["harvest_project_id"],
            "kind": kind,
            "is_placeholder": is_placeholder,
            "placeholder_state": state,
        })

        if is_placeholder:
            entry = {
                "recurring_line_item_id": str(item["id"]),
                "description": description,
                "kind": kind,
                "sort_order": item["sort_order"],
                "quantity": quantity,
                "unit_price": unit_price,
                "amount": amount,
                "note": resolution["note"] if resolution else None,
            }
            if state == "resolved":
                res.resolved_placeholders.append(entry)
                res.total += amount
            elif state == "omitted":
                res.omitted_placeholders.append(entry)
            else:
                res.unresolved_placeholders.append(entry)
        else:
            res.total += amount

    res.total = round(res.total, 2)
    return res


def line_detail(kind: str, state: str | None, period_label: str) -> str:
    """The secondary line under a description in the pre-flight table.

    Public because `placeholders.py` renders the same string when it patches a
    resolved line in place — if the two drifted, resolving a placeholder and
    re-planning would produce visibly different rows for the same state.
    """
    if state == "unresolved":
        return f"{kind} · needs an amount"
    if state == "omitted":
        return f"{kind} · omitted for {period_label}"
    return kind


async def load_valid_kinds(conn: Any) -> set[str]:
    rows = await conn.fetch("SELECT name FROM harvest_invoice_item_categories")
    return {r["name"] for r in rows}
