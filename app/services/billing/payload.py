"""Invoice payload construction — the exact POST /v2/invoices body.

Persisted to `billing_run_items.planned_payload` at plan time and shown in the
pre-flight, so what the operator approves is literally what would be sent.

Two Harvest behaviours this file exists to get right:

  - Omitting `from`/`to` from the `time` object pulls **all** unbilled time
    regardless of date. Both are always present.
  - `due_date` is ignored unless `payment_term` is `"custom"`, so enum terms
    send the term alone and let Harvest compute the date.
"""
from __future__ import annotations

from datetime import date
from typing import Any


class PayloadNotSupported(NotImplementedError):
    """This billing type has no payload builder yet (PRD Phase 4)."""


def render_template(
    template: str,
    *,
    client_name: str,
    period_label: str = "",
    draw_description: str = "",
    draw_number: str = "",
    draw_count: str = "",
) -> str:
    """`{draw_description}`, `{draw_number}`, and `{draw_count}` exist because a
    draw invoice covers no period — the milestone identifies it, so
    `{period_label}` has nothing to say. `{draw_number}` is the draw's position
    in the contract schedule and `{draw_count}` its length, which together give
    the "Draw 2 of 5" a fixed-fee client recognises.

    A known token with nothing to say renders empty — `{draw_number}` on a
    time-and-materials group is blank, not literal. Anything not in this list is
    left untouched, so a misspelled token shows up in the pre-flight subject
    rather than silently vanishing.
    """
    return (
        template
        .replace("{client_name}", client_name or "")
        .replace("{period_label}", period_label or "")
        .replace("{draw_description}", draw_description or "")
        .replace("{draw_number}", draw_number or "")
        .replace("{draw_count}", draw_count or "")
    )


def resolve_notes(
    group_template: str | None,
    default_notes: str,
    **tokens: str,
) -> str | None:
    """The notes to put on the invoice, group template first, account default next.

    **Why a default exists at all.** Harvest's account-level "default invoice
    notes" apply only to invoices created through Harvest's own UI. The API
    neither applies them to a created invoice nor exposes them for reading — there
    is no field for them on `GET /v2/company`. So an API-created invoice arrives
    with the notes blank unless we send them, and the remit-to instructions the
    client needs to actually pay are exactly what lives in those notes.

    `default_notes` comes from the `default_invoice_notes` row in
    `billing_settings` (see `settings_store`), which makes it a second copy of
    something Harvest also stores. That is a real cost — the two can drift and
    nothing detects it — and it is unavoidable, because no endpoint reads the
    original. Keep them in step by hand when either changes.

    A group's `notes_template` overrides the default outright rather than
    appending to it. Appending would mean no group could ever *replace* the
    boilerplate, and a client with bespoke wire instructions would silently get
    both sets.
    """
    template = (group_template or "").strip() or default_notes
    return render_template(template, **tokens).strip() or None


def build_time_and_materials_payload(
    *,
    harvest_client_id: int,
    subject: str,
    issue_date: date,
    payment_term: str,
    due_date: date | None,
    project_ids: list[int],
    period_start: date,
    period_end: date,
    time_summary_type: str,
    include_expenses: bool,
    expense_summary_type: str | None,
    purchase_order: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """`line_items_import` body — Harvest generates the line items itself."""
    time_block = {
        "summary_type": time_summary_type,
        "from": period_start.isoformat(),
        "to": period_end.isoformat(),
    }
    line_items_import: dict[str, Any] = {
        "project_ids": project_ids,
        "time": time_block,
    }
    # Harvest rejects an empty expenses object — omit it entirely when off.
    if include_expenses:
        line_items_import["expenses"] = {
            "summary_type": expense_summary_type or "category",
            "from": period_start.isoformat(),
            "to": period_end.isoformat(),
        }

    payload: dict[str, Any] = {
        "client_id": harvest_client_id,
        "subject": subject,
        "issue_date": issue_date.isoformat(),
        "payment_term": payment_term,
        "line_items_import": line_items_import,
    }
    # Only meaningful for custom terms; sending it otherwise is ignored at best
    # and misleading in the pre-flight at worst.
    if payment_term == "custom" and due_date is not None:
        payload["due_date"] = due_date.isoformat()
    if purchase_order:
        payload["purchase_order"] = purchase_order
    if notes:
        payload["notes"] = notes
    return payload


def build_free_form_payload(
    *,
    harvest_client_id: int,
    subject: str,
    issue_date: date,
    payment_term: str,
    due_date: date | None,
    line_items: list[dict[str, Any]],
    purchase_order: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Literal `line_items` body — Harvest generates nothing.

    Used by `recurring_monthly` groups. Each line carries its own `project_id`,
    so one invoice can span several projects: hosting against the hosting
    project, a service fee against another.
    """
    payload: dict[str, Any] = {
        "client_id": harvest_client_id,
        "subject": subject,
        "issue_date": issue_date.isoformat(),
        "payment_term": payment_term,
        "line_items": line_items,
    }
    if payment_term == "custom" and due_date is not None:
        payload["due_date"] = due_date.isoformat()
    if purchase_order:
        payload["purchase_order"] = purchase_order
    if notes:
        payload["notes"] = notes
    return payload
