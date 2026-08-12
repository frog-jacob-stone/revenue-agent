"""Rate resolution and hour normalization.

Shared by the T&M estimator and by config reconciliation, which needs to price
uninvoiced time on unmapped projects to say how much revenue is at risk.

The ladder (PRD §4.3), in order:

    1. the time entry's own `billable_rate`
    2. the project's `hourly_rate`
    3. the task assignment's `hourly_rate` for that project + task

An unresolved rate is an error, never a silent zero — quietly pricing work at
nothing is precisely how revenue goes missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import asyncpg


@dataclass
class RateContext:
    """Cached rate inputs for a set of projects, loaded once per run."""

    project_hourly: dict[int, float] = field(default_factory=dict)
    # (project_id, task_id) -> hourly rate
    task_hourly: dict[tuple[int, int], float] = field(default_factory=dict)


async def load_rate_context(
    pool: asyncpg.Pool, project_ids: list[int]
) -> RateContext:
    if not project_ids:
        return RateContext()

    ctx = RateContext()
    for row in await pool.fetch(
        "SELECT harvest_id, hourly_rate FROM harvest_projects "
        "WHERE harvest_id = ANY($1::bigint[]) AND hourly_rate IS NOT NULL",
        project_ids,
    ):
        ctx.project_hourly[row["harvest_id"]] = float(row["hourly_rate"])

    for row in await pool.fetch(
        "SELECT harvest_project_id, task_id, hourly_rate FROM harvest_task_assignments "
        "WHERE harvest_project_id = ANY($1::bigint[]) AND hourly_rate IS NOT NULL",
        project_ids,
    ):
        ctx.task_hourly[(row["harvest_project_id"], row["task_id"])] = float(
            row["hourly_rate"]
        )
    return ctx


def is_uninvoiced_billable(entry: dict[str, Any]) -> bool:
    """Whether an entry belongs on the next invoice.

    Filtered client-side rather than via a query parameter: v2's support for
    `is_billed` as a filter is unverified (PRD §4.3), and guessing wrong here
    would silently drop or double-count revenue.
    """
    return bool(entry.get("billable")) and not bool(entry.get("is_billed"))


# ---------------------------------------------------------------------------
# Time rounding
#
# MUST MATCH Harvest → Settings → Time. This is a mirror of a setting that lives
# in Harvest, and nothing reconciles the two: if someone enables rounding there
# and this stays False, every T&M invoice is quietly computed off unrounded hours
# and no flag fires. Check Harvest before changing it, not the other way round.
#
# Deliberately a constant rather than an env var. It has been False since the
# system went live and is not expected to change; as config it was one more
# undocumented value to keep in sync across environments, and getting it wrong
# per-environment is worse than needing a deploy to change it.
# ---------------------------------------------------------------------------
USE_ROUNDED_HOURS = False


def effective_hours(entry: dict[str, Any]) -> float:
    """Hours to bill, honoring the account's time-rounding preference.

    Reads `USE_ROUNDED_HOURS` at call time (not as a default argument) so tests
    can patch the module attribute to exercise both branches.
    """
    key = "rounded_hours" if USE_ROUNDED_HOURS else "hours"
    value = entry.get(key)
    if value is None:
        value = entry.get("hours") or 0
    return float(value)


def resolve_rate(entry: dict[str, Any], ctx: RateContext) -> float | None:
    """Walk the rate ladder. None means no rate resolved — an error condition."""
    rate = entry.get("billable_rate")
    if rate is not None:
        return float(rate)

    project_id = int((entry.get("project") or {}).get("id") or 0)
    if project_id in ctx.project_hourly:
        return ctx.project_hourly[project_id]

    task_id = int((entry.get("task") or {}).get("id") or 0)
    return ctx.task_hourly.get((project_id, task_id))


def price_entries(
    entries: list[dict[str, Any]], ctx: RateContext
) -> tuple[float, float, list[dict[str, Any]]]:
    """Price a set of entries.

    Returns `(total_amount, total_hours, unresolved)` where `unresolved` holds
    the entries whose rate could not be determined. Those contribute hours but
    no amount, and the caller is expected to raise NO_RATE_RESOLVED rather than
    presenting the understated total as if it were complete.
    """
    total = 0.0
    hours = 0.0
    unresolved: list[dict[str, Any]] = []
    for entry in entries:
        h = effective_hours(entry)
        hours += h
        rate = resolve_rate(entry, ctx)
        if rate is None:
            unresolved.append(entry)
            continue
        total += h * rate
    return round(total, 2), round(hours, 2), unresolved
