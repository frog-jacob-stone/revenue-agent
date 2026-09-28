"""Revenue recognition models.

Amounts are `Decimal`, not `float`. The ledger reconciles to the cent and the
columns are `numeric(12,2)`; routing money through a binary float on the way out
would be the one place that stops being true.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from app.models.common import ORMBase

RevenueType = Literal[
    "fixed_fee", "time_and_materials", "msf", "hosting", "retainer"
]
RevenueRunStatus = Literal["draft", "recognized", "abandoned"]


class RevenueEntry(ORMBase):
    """One project's revenue for one month."""

    id: UUID
    period_month: date
    harvest_project_id: int
    # Snapshotted on the entry: projects get renamed, and a ledger row should
    # read the way it read when it was recognized.
    harvest_project_name: str
    # Joined live from the Harvest cache rather than snapshotted — unlike the
    # project name it is not part of what was recognized. Both null if the
    # project has since left the cache.
    client_id: int | None = None
    client_name: str | None = None
    revenue_type: RevenueType

    #: Revenue recognized in this period. The only stored monetary fact.
    recognized_amount: Decimal
    #: What the system computed, before any override. Equal to
    #: `recognized_amount` unless a human intervened.
    computed_amount: Decimal

    # Evidence of how the number was derived, not reporting fields.
    logged_hours: Decimal | None = None
    scheduled_hours: Decimal | None = None
    percent_complete: Decimal | None = None
    contracted_fees: Decimal | None = None
    invoiced_to_date: Decimal | None = None
    notes: str | None = None

    override_reason: str | None = None
    overridden_by: str | None = None
    overridden_at: datetime | None = None


class LedgerEntry(RevenueEntry):
    """A ledger row, carrying the project's cumulative total at that month.

    Summed at read time over the project's whole history — not just the rows a
    date filter returned, so a twelve-month view still shows the true figure
    rather than one that restarts at the range boundary.
    """

    cumulative_recognized: Decimal


class RevenueClient(ORMBase):
    """A client with activity in the window — one option in the client filter.

    Carries its totals and project count so the filter can show what picking it
    would be worth, rather than being a bare list of names.

    Both measures ride along because the Overview is read through a metric
    selector: the number beside each name is revenue, hours or a rate depending
    on what is being looked at, and re-fetching this list on every switch would
    make the options move under the cursor.
    """

    client_id: int
    client_name: str | None = None
    recognized_amount: Decimal
    logged_hours: Decimal | None = None
    project_count: int


class RevenueMonth(ORMBase):
    """One month of the trend.

    Revenue per billable hour is deliberately absent: blending it (total revenue
    / total hours) and averaging it across months give different answers, and
    only the former is right. The caller gets both numbers and does the division
    at whatever granularity it is actually showing.
    """

    period_month: date
    recognized_amount: Decimal
    logged_hours: Decimal | None = None
    entry_count: int


class RevenueRunSummary(ORMBase):
    """A run as the history table shows it. Every status, including drafts."""

    id: UUID
    period_month: date
    status: RevenueRunStatus
    created_at: datetime
    created_by: str
    finalized_at: datetime | None = None
    finalized_by: str | None = None
    abandoned_at: datetime | None = None
    abandoned_by: str | None = None
    entry_count: int
    #: For a draft, what it currently proposes rather than what was booked.
    total_recognized: Decimal


class RevenueProjectConfig(ORMBase):
    """One project's recognition setup, as the setup screen shows it.

    `revenue_type` null means unconfigured — the LEFT JOIN is from the project,
    because the useful question is what still needs setting up.
    """

    harvest_project_id: int
    harvest_project_name: str
    client_name: str | None = None
    revenue_type: RevenueType | None = None
    contracted_fees: Decimal | None = None
    notes: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class RevenueConfigRequest(ORMBase):
    """Setting a project's configuration. Create and update are one operation —
    "has this been configured before" is not a distinction the person filling
    in the form is making."""

    revenue_type: RevenueType
    contracted_fees: Decimal | None = None
    notes: str | None = None


class PlanRunRequest(ORMBase):
    """Any date in the month to recognize; the server takes the month."""

    period_month: date


class OverrideEntryRequest(ORMBase):
    """Replacing a computed figure with a human's.

    The reason is required and non-empty — it becomes the only record of why a
    booked figure is not what the arithmetic produced.
    """

    recognized_amount: Decimal
    override_reason: str


class RevenueRunDetail(ORMBase):
    """One run and every entry in it, whatever its status.

    This is what an operator reads before finalizing, so it is deliberately not
    filtered to recognized runs — under ADR-0004 seeing this exact payload is
    what makes the subsequent click an authorization.
    """

    id: UUID
    period_month: date
    status: RevenueRunStatus
    created_at: datetime
    created_by: str
    finalized_at: datetime | None = None
    finalized_by: str | None = None
    abandoned_at: datetime | None = None
    abandoned_by: str | None = None
    entries: list[RevenueEntry]
    total_recognized: Decimal
