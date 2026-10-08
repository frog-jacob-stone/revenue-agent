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

from pydantic import field_validator

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
    #: Whether the project is still active in Harvest, read live like
    #: `client_name` rather than snapshotted. An archived project only earns an
    #: entry while it has revenue left to recognize, and recognizing the
    #: remainder of a contract assumes the work *completed* — a judgement the
    #: system cannot make, since a cancelled project and a finished one look
    #: identical once their Forecast bookings end. Surfaced so the review screen
    #: can say so. Null if the project has left the snapshot.
    project_is_active: bool | None = None
    revenue_type: RevenueType

    #: Revenue recognized in this period. The only stored monetary fact.
    recognized_amount: Decimal
    #: What the system computed, before any override. Equal to
    #: `recognized_amount` unless a human intervened.
    computed_amount: Decimal

    # Evidence of how the number was derived, not reporting fields.
    logged_hours: Decimal | None = None
    #: Hours from project inception through this period — `logged_hours` plus
    #: every recognized month before it. Summed at read time, like
    #: `LedgerEntry.cumulative_recognized`, rather than stored.
    #:
    #: Here because `percent_complete` is a fraction of *total* effort while
    #: `logged_hours` is the period's own, so the two numbers on a row could not
    #: be reconciled with each other: 25 hours against 100% complete reads as an
    #: error until you know the project has 1,816 hours behind it. Only the run
    #: detail populates it — the one screen where a figure is checked before it
    #: is booked.
    cumulative_hours: Decimal | None = None
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

    @field_validator("recognized_amount", mode="before")
    @classmethod
    def _money_as_typed(cls, value: object) -> object:
        """Accept an amount the way a person writes one.

        The field is reached by typing a figure into a box on the review screen,
        and the natural way to write a retainer is `$12,500.00`. Bare
        `Decimal("$12,500.00")` raises, so the first real override attempted on
        this screen came back a 422 — for a *formatting* difference, on the one
        entry type that cannot be computed and therefore always has to be typed.

        `$`, commas and surrounding space are stripped; everything else still
        fails, so a genuine typo is still refused rather than coerced into a
        number nobody meant. Done here rather than in the browser so it holds
        for every caller, and so there is one definition of what a figure may
        look like instead of two that can drift.
        """
        if not isinstance(value, str):
            return value
        cleaned = value.strip().replace("$", "").replace(",", "").strip()
        if not cleaned:
            raise ValueError(
                "Enter an amount. A retainer that earned nothing this month is "
                "an explicit 0, which is a decision; an empty box is not."
            )
        return cleaned

    @field_validator("override_reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str) -> str:
        """Checked at the boundary as well as in the service.

        `revenue_run.override_entry` raises `RevenueRunError` for a blank
        reason, which the router maps to **404** — the status for a run that
        does not exist. Validating here returns 422 with the reason, and leaves
        that 404 meaning only what it says.
        """
        if not value.strip():
            raise ValueError(
                "An override needs a reason — it is the only record of why "
                "this figure is not what was computed."
            )
        return value.strip()


class DeletedRun(ORMBase):
    """What was removed. Returned instead of a bare 204 so the screen can say
    which month and how many entries went, rather than a row disappearing with
    no account of what was in it."""

    id: UUID
    period_month: date
    entry_count: int


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
