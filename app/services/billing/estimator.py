"""T&M estimation from uninvoiced time and expenses.

This estimate is computed independently of Harvest's own invoice generation, so
it will not always match to the penny — time rounding, rate resolution order,
and mid-period rate changes all diverge. That is expected and acceptable.

The number is a **sanity check, not a contract**. After execution, the created
invoice's amount is compared against it and the variance recorded. Over a few
months that either converges to zero or teaches you exactly where the estimator
is wrong.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import asyncpg

from app.config import Settings
from app.integrations import harvest
from app.services.billing import rates
from app.services.billing.dates import Period

logger = logging.getLogger(__name__)

# How far back to look for uninvoiced billable time predating the service period
# (the STRAGGLER_TIME flag), per configured group. Wide on purpose: straggler time
# rolls silently forward, so a year catches work that slipped through several
# cycles. Contrast `reconcile.UNMAPPED_LOOKBACK_DAYS`, which is narrower because
# it sweeps the whole account.
#
# A constant rather than a setting: it was never set in any environment.
STRAGGLER_LOOKBACK_DAYS = 365


@dataclass
class Estimate:
    line_items: list[dict[str, Any]] = field(default_factory=list)
    total: float = 0.0
    hours: float = 0.0
    unresolved_rate_entries: list[dict[str, Any]] = field(default_factory=list)
    unapproved_entries: list[dict[str, Any]] = field(default_factory=list)
    straggler_hours: float = 0.0
    straggler_earliest: date | None = None
    late_hours: float = 0.0
    has_any_time: bool = False


def _summary_key(entry: dict[str, Any], summary_type: str) -> tuple[str, str]:
    """(label, source) for one entry under the configured summary type."""
    project = (entry.get("project") or {}).get("name") or "Project"
    if summary_type == "task":
        return (entry.get("task") or {}).get("name") or "Work", project
    if summary_type == "people":
        return (entry.get("user") or {}).get("name") or "Team member", project
    if summary_type == "detailed":
        task = (entry.get("task") or {}).get("name") or "Work"
        return f"{task} — {entry.get('spent_date', '')}", project
    return project, "Project summary"


def _expense_key(expense: dict[str, Any], summary_type: str) -> tuple[str, str]:
    project = (expense.get("project") or {}).get("name") or "Project"
    category = (expense.get("expense_category") or {}).get("name") or "Expense"
    if summary_type == "category":
        return category, "Expenses — category summary"
    if summary_type == "people":
        return (expense.get("user") or {}).get("name") or "Team member", "Expenses"
    if summary_type == "detailed":
        return f"{category} — {expense.get('spent_date', '')}", project
    return project, "Expenses — project summary"


async def estimate_group(
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    project_ids: list[int],
    period: Period,
    time_summary_type: str,
    include_expenses: bool,
    expense_summary_type: str | None,
) -> Estimate:
    """Estimate one billing group's invoice from its projects' uninvoiced work."""
    est = Estimate()
    if not project_ids:
        return est

    rate_ctx = await rates.load_rate_context(pool, project_ids)

    from_ = period.start.isoformat()
    to = period.end.isoformat()

    # (label, detail) -> accumulated hours and amount
    buckets: dict[tuple[str, str], dict[str, float]] = {}

    for project_id in project_ids:
        entries = await harvest.list_time_entries(
            cfg, project_id=project_id, from_=from_, to=to
        )
        if entries:
            est.has_any_time = True

        billable = [e for e in entries if rates.is_uninvoiced_billable(e)]
        for entry in billable:
            if entry.get("approval_status") not in (None, "approved"):
                est.unapproved_entries.append(entry)

            hours = rates.effective_hours(entry)
            rate = rates.resolve_rate(entry, rate_ctx)
            if rate is None:
                est.unresolved_rate_entries.append(entry)
                continue

            key = _summary_key(entry, time_summary_type)
            bucket = buckets.setdefault(key, {"hours": 0.0, "amount": 0.0, "rate": rate})
            bucket["hours"] += hours
            bucket["amount"] += hours * rate

        await _collect_straggler_and_late(
            cfg, est, project_id=project_id, period=period
        )

    for (label, detail), b in buckets.items():
        hours = round(b["hours"], 2)
        amount = round(b["amount"], 2)
        est.line_items.append({
            "label": label,
            "detail": detail,
            "quantity": hours,
            "unit": "hrs",
            # Blended, since a bucket can span entries at different rates.
            "unit_price": round(amount / hours, 2) if hours else 0.0,
            "amount": amount,
        })
        est.hours += hours
        est.total += amount

    if include_expenses:
        await _add_expenses(
            cfg, est,
            project_ids=project_ids, from_=from_, to=to,
            summary_type=expense_summary_type or "category",
        )

    est.total = round(est.total, 2)
    est.hours = round(est.hours, 2)
    est.line_items.sort(key=lambda li: -li["amount"])
    return est


async def _add_expenses(
    cfg: Settings,
    est: Estimate,
    *,
    project_ids: list[int],
    from_: str,
    to: str,
    summary_type: str,
) -> None:
    buckets: dict[tuple[str, str], float] = {}
    for project_id in project_ids:
        for expense in await harvest.list_expenses(
            cfg, project_id=project_id, from_=from_, to=to
        ):
            if not rates.is_uninvoiced_billable(expense):
                continue
            key = _expense_key(expense, summary_type)
            buckets[key] = buckets.get(key, 0.0) + float(expense.get("total_cost") or 0)

    for (label, detail), amount in buckets.items():
        amount = round(amount, 2)
        est.line_items.append({
            "label": label,
            "detail": detail,
            "quantity": 1,
            "unit": "ea",
            "unit_price": amount,
            "amount": amount,
        })
        est.total += amount


async def _collect_straggler_and_late(
    cfg: Settings,
    est: Estimate,
    *,
    project_id: int,
    period: Period,
) -> None:
    """Uninvoiced billable time sitting outside the service period.

    Stragglers (before the period) will not be captured by the bounded
    from/to import and roll silently forward. Late time (after the period on an
    arrears group) is next month's problem but worth surfacing now.
    """
    lookback_start = period.start - timedelta(days=STRAGGLER_LOOKBACK_DAYS)
    before = await harvest.list_time_entries(
        cfg,
        project_id=project_id,
        from_=lookback_start.isoformat(),
        to=(period.start - timedelta(days=1)).isoformat(),
    )
    for entry in before:
        if not rates.is_uninvoiced_billable(entry):
            continue
        est.straggler_hours += rates.effective_hours(entry)
        spent = entry.get("spent_date")
        if spent:
            spent_date = date.fromisoformat(spent)
            if est.straggler_earliest is None or spent_date < est.straggler_earliest:
                est.straggler_earliest = spent_date

    today = date.today()
    if period.end < today:
        after = await harvest.list_time_entries(
            cfg,
            project_id=project_id,
            from_=(period.end + timedelta(days=1)).isoformat(),
            to=today.isoformat(),
        )
        for entry in after:
            if rates.is_uninvoiced_billable(entry):
                est.late_hours += rates.effective_hours(entry)

    est.straggler_hours = round(est.straggler_hours, 2)
    est.late_hours = round(est.late_hours, 2)
