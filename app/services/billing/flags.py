"""The flag catalog (PRD §7), table-driven so codes are cheap to add.

Design principle: **the system's job is to notice, the operator's job is to
decide.** Nothing here auto-blocks execution. Error-severity flags default a
group to unapproved; the operator can still override.

The one exception is `UNRESOLVED_IN_FLIGHT`. Overriding it risks creating the
duplicate invoice that the whole §8 protocol exists to prevent, so the UI
offers resolution rather than override. That code is not in the PRD's catalog —
§8 describes the blocking behaviour but never names it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ERROR = "error"
WARNING = "warning"
INFO = "info"

# Errors that must never be overridable, because overriding risks money moving
# twice rather than merely moving wrongly.
NON_OVERRIDABLE = frozenset({"UNRESOLVED_IN_FLIGHT"})


@dataclass(frozen=True)
class Flag:
    code: str
    severity: str
    message: str
    context: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "context": self.context,
        }


def flag(code: str, severity: str, message: str, **context: Any) -> Flag:
    return Flag(code=code, severity=severity, message=message, context=context)


# ── Catalog ─────────────────────────────────────────────────────────────────


def unresolved_in_flight(*, run_label: str, item_id: str, run_id: str) -> Flag:
    return flag(
        "UNRESOLVED_IN_FLIGHT", ERROR,
        f"An in-flight ledger row from the {run_label} run has never been "
        f"resolved. Check Harvest manually and resolve it before planning this "
        f"group again.",
        item_id=item_id, run_id=run_id,
    )


def already_invoiced_this_run(*, run_label: str, invoice_number: str | None) -> Flag:
    which = f" (invoice #{invoice_number})" if invoice_number else ""
    return flag(
        "ALREADY_INVOICED_THIS_RUN", ERROR,
        f"This group already has a created invoice{which} for {run_label}. "
        f"Re-planning it would double-bill the client.",
        invoice_number=invoice_number,
    )


def no_rate_resolved(*, entries: list[dict[str, Any]], hours: float) -> Flag:
    users = sorted({(e.get("user") or {}).get("name") or "unknown" for e in entries})
    return flag(
        "NO_RATE_RESOLVED", ERROR,
        f"{len(entries)} billable entr{'y' if len(entries) == 1 else 'ies'} "
        f"({hours:g} hrs) have no resolvable rate. The estimate excludes them.",
        entry_count=len(entries), hours=hours, users=users,
    )


def _describe_invoices(invoices: list[dict[str, Any]]) -> str:
    return ", ".join(
        f"#{i.get('number')} ({i.get('issue_date')}, {i.get('amount')})"
        for i in invoices
    )


def _invoice_context(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": i.get("id"), "number": i.get("number"),
         "issue_date": i.get("issue_date"), "amount": i.get("amount")}
        for i in invoices
    ]


def existing_harvest_invoice(*, invoices: list[dict[str, Any]]) -> Flag:
    """Scoped to the group's own projects, not to the client.

    A client with several engagements will have other invoices in the window
    and that is not news; only one billed against a project in *this* group is
    a possible duplicate. See `duplicate_guard`.
    """
    return flag(
        "EXISTING_HARVEST_INVOICE", WARNING,
        f"Harvest already has {len(invoices)} invoice(s) billed against this "
        f"billing group's projects in the period window that this system did "
        f"not create: {_describe_invoices(invoices)}.",
        invoices=_invoice_context(invoices),
    )


def unattributed_harvest_invoice(*, invoices: list[dict[str, Any]]) -> Flag:
    """Invoices for this client that carry no project on any line item.

    `info`, not `warning`: they cannot be shown to overlap this group, so
    treating them as duplicates would recreate the noise that scoping
    `EXISTING_HARVEST_INVOICE` to projects removed. But they cannot be shown
    *not* to overlap either — a hand-typed invoice is the likeliest duplicate
    there is — so they are named rather than dropped.
    """
    return flag(
        "UNATTRIBUTED_HARVEST_INVOICE", INFO,
        f"{len(invoices)} invoice(s) for this client in the period window carry "
        f"no project on any line, so they cannot be matched to a billing group: "
        f"{_describe_invoices(invoices)}. Check them by hand if this group looks "
        f"like it may already have been billed.",
        invoices=_invoice_context(invoices),
    )


def invalid_item_category(*, offenders: list[dict[str, Any]], valid: list[str]) -> Flag:
    listed = ", ".join(f"'{o['kind']}' on “{o['description']}”" for o in offenders)
    return flag(
        "INVALID_ITEM_CATEGORY", ERROR,
        f"{listed} — not an invoice item category in this Harvest account. "
        f"Valid categories: {', '.join(sorted(valid))}.",
        offenders=offenders, valid=sorted(valid),
    )


def placeholder_line_items(
    *,
    unresolved: list[dict[str, Any]],
    resolved: list[dict[str, Any]],
    omitted: list[dict[str, Any]],
) -> Flag:
    """A record of what this plan contained, not the thing that blocks approval.

    Deliberately `info`, and deliberately frozen. Making it `error` would reuse
    the existing gate machinery but hand over the `error_override` escape hatch,
    which is the escape a placeholder exists to close; and it would then need
    rewriting every time the operator resolves one, which would stop flags being
    a faithful record of the plan as it stood. The block is a live check in
    `review.py`, derived from the ledger row's own line items.
    """
    total = len(unresolved) + len(resolved) + len(omitted)
    listed = ", ".join(f"“{p['description']}”" for p in unresolved)
    if unresolved:
        n = len(unresolved)
        message = (
            f"{n} of {total} placeholder line item{'' if total == 1 else 's'} "
            f"still need{'s' if n == 1 else ''} an amount, or an explicit omit "
            f"for this month: {listed}. This invoice cannot be approved until "
            f"each one is decided."
        )
    else:
        parts = []
        if resolved:
            parts.append(f"{len(resolved)} priced")
        if omitted:
            parts.append(f"{len(omitted)} omitted for this month")
        message = (
            f"{total} placeholder line item{'' if total == 1 else 's'}, all "
            f"decided — {', '.join(parts)}."
        )
    return flag(
        "PLACEHOLDER_LINE_ITEMS", INFO, message,
        unresolved=unresolved, resolved=resolved, omitted=omitted,
    )


def line_item_off_group_project(*, offenders: list[dict[str, Any]]) -> Flag:
    listed = ", ".join(
        f"“{o['description']}” → project #{o['harvest_project_id']}" for o in offenders
    )
    return flag(
        "LINE_ITEM_OFF_GROUP_PROJECT", ERROR,
        f"{listed}. Every line item must target a project in this billing group, "
        f"or Harvest returns a 422.",
        offenders=offenders,
    )


def no_recurring_items(*, period_label: str) -> Flag:
    return flag(
        "NO_RECURRING_ITEMS", WARNING,
        f"Recurring group has no line items in effect for {period_label}. "
        f"Skipped rather than creating an empty invoice.",
        period_label=period_label,
    )


def no_uninvoiced_time(*, period_label: str) -> Flag:
    return flag(
        "NO_UNINVOICED_TIME", WARNING,
        f"No billable uninvoiced time for {period_label}. Skipped rather than "
        f"creating a zero invoice.",
        period_label=period_label,
    )


def unapproved_time(*, entries: list[dict[str, Any]], hours: float) -> Flag:
    return flag(
        "UNAPPROVED_TIME", WARNING,
        f"{len(entries)} time entr{'y' if len(entries) == 1 else 'ies'} "
        f"({hours:g} hrs) in the period are not yet approved in Harvest.",
        entry_count=len(entries), hours=hours,
    )


def straggler_time(*, hours: float, earliest: str | None, period_start: str) -> Flag:
    """Billable work from before this period that no invoice has ever picked up.

    There is no `LATE_TIME` counterpart. Time logged *after* the period is
    ordinary — billing August in September always finds September time — and a
    flag that fires on every arrears run every month is one nobody reads. Time
    from *before* the period is the asymmetric case: this invoice's import
    window will not reach it, so it rolls forward again, silently, exactly as
    it already has.
    """
    since = f" (earliest {earliest})" if earliest else ""
    return flag(
        "STRAGGLER_TIME", WARNING,
        f"{hours:g} hrs of billable time dated before {period_start}{since} has "
        f"never been invoiced. This invoice covers {period_start} onward, so it "
        f"will not pick that up — it rolls forward again unless you bill it by "
        f"hand.",
        hours=hours, earliest=earliest,
    )


def missing_po() -> Flag:
    return flag(
        "MISSING_PO", WARNING,
        "This group requires a purchase order but none is configured.",
    )


def amount_variance(*, planned: float, prior: float, pct: float, threshold: float) -> Flag:
    direction = "above" if pct > 0 else "below"
    return flag(
        "AMOUNT_VARIANCE", WARNING,
        f"Estimate is {abs(pct):.1f}% {direction} last month "
        f"(${prior:,.2f} → ${planned:,.2f}), past the {threshold:g}% threshold.",
        planned=planned, prior=prior, pct=round(pct, 2),
    )


def draws_overdue(*, draws: list[dict[str, Any]]) -> Flag:
    """Scheduled dates that have passed with delivery still unconfirmed.

    A warning rather than an error: nothing is wrong with the run, and the
    remedy is either to confirm delivery or to update the contract schedule.
    Both are outside this run.
    """
    first = draws[0]
    lead = (
        f"'{first['description']}' was scheduled for {first['scheduled_date']}"
        if len(draws) == 1
        else f"{len(draws)} draws are past their scheduled date, the earliest "
             f"'{first['description']}' ({first['scheduled_date']})"
    )
    return flag(
        "DRAW_OVERDUE", WARNING,
        f"{lead} and delivery is still unconfirmed. Confirm it on the Draws "
        f"tab, or update the schedule if the date moved.",
        draws=draws,
    )


def draws_awaiting_release(*, count: int, total: float) -> Flag:
    return flag(
        "DRAWS_AWAITING_RELEASE", INFO,
        f"{count} draw{'' if count == 1 else 's'} worth {total:,.2f} still "
        f"awaiting delivery confirmation. They bill individually from the Draws "
        f"tab, not from this run.",
        count=count, total=total,
    )


def draws_ready_to_bill(*, count: int, total: float) -> Flag:
    return flag(
        # Code left as-is: flag codes are stable identifiers that appear in
        # audit_log context. Only the human-readable message changed.
        "DRAWS_READY_TO_BILL", INFO,
        f"{count} draw{'' if count == 1 else 's'} worth {total:,.2f} "
        f"{'is' if count == 1 else 'are'} confirmed and waiting to be drafted on "
        f"the Draws tab.",
        count=count, total=total,
    )


def fixed_fee_time_noise(*, hours: float) -> Flag:
    return flag(
        "FIXED_FEE_TIME_NOISE", INFO,
        f"{hours:g} hrs of billable uninvoiced time tracked to a fixed-fee or "
        f"retainer project. Expected — Harvest never clears it.",
        hours=hours,
    )


def type_mismatch(*, billing_type: str, project_name: str, project_id: int,
                  is_fixed_fee: bool) -> Flag:
    actual = "fixed fee" if is_fixed_fee else "not fixed fee"
    return flag(
        "TYPE_MISMATCH", WARNING,
        f"Group is {billing_type}, but Harvest project {project_name} "
        f"(#{project_id}) is {actual}.",
        harvest_project_id=project_id,
    )


def counts(flags: list[Flag] | list[dict[str, Any]]) -> dict[str, int]:
    def severity_of(f: Any) -> str:
        return f["severity"] if isinstance(f, dict) else f.severity

    return {
        ERROR: sum(1 for f in flags if severity_of(f) == ERROR),
        WARNING: sum(1 for f in flags if severity_of(f) == WARNING),
        INFO: sum(1 for f in flags if severity_of(f) == INFO),
    }


def has_error(flags: list[Flag] | list[dict[str, Any]]) -> bool:
    return counts(flags)[ERROR] > 0
