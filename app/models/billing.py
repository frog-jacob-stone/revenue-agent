from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.models.common import ORMBase

BillingType = Literal[
    "time_and_materials", "fixed_fee_schedule", "recurring_monthly", "manual"
]
BillingTiming = Literal["arrears", "advance"]
PaymentTerm = Literal[
    "upon receipt", "net 15", "net 30", "net 45", "net 60", "custom"
]
# Harvest's two summary vocabularies differ — time has `task`, expenses have
# `category`. Kept distinct so an invalid combination fails at the edge.
TimeSummaryType = Literal["project", "task", "people", "detailed"]
ExpenseSummaryType = Literal["project", "category", "people", "detailed"]
FlagSeverity = Literal["error", "warning", "info"]


# ── Billing groups ──────────────────────────────────────────────────────────


class GroupProject(ORMBase):
    harvest_project_id: int
    harvest_project_name: str | None = None
    sort_order: int = 0


class ScheduleItem(ORMBase):
    id: UUID
    harvest_project_id: int
    sequence: int
    description: str
    amount: float
    kind: str = "Service"
    scheduled_date: date
    released_at: datetime | None = None
    released_by: str | None = None
    invoiced_run_id: UUID | None = None
    live_run_id: UUID | None = None


class ScheduleItemInput(ORMBase):
    """A draw as submitted by the group form. `id` is null for a new row —
    existing rows keep their id so release state and billing history survive
    an edit."""

    id: UUID | None = None
    harvest_project_id: int
    sequence: int = 0
    description: str
    amount: float = 0
    kind: str = "Service"
    scheduled_date: date


class DrawResponse(ORMBase):
    """A draw with the context the queue needs. `state` is derived from
    `released_at`, `invoiced_run_id`, and whether a live ledger row exists —
    never stored. The invoice itself is not here: it is computed on demand by
    `DrawPreview`, because nothing about it is worth persisting before the
    Harvest draft exists."""

    id: UUID
    billing_group_id: UUID
    billing_group_name: str | None = None
    harvest_client_name: str | None = None
    harvest_project_id: int
    harvest_project_name: str | None = None
    sequence: int
    description: str
    amount: float
    kind: str
    scheduled_date: date
    state: str
    released_at: datetime | None = None
    released_by: str | None = None
    invoiced_run_id: UUID | None = None
    #: The run holding this draw's live ledger row, once execution has written
    #: one. Also set after creation, so the queue can reach the invoice it made.
    live_run_id: UUID | None = None
    #: What this draw produced. Populated once a Harvest draft exists — an
    #: `invoiced` draw leaves the billing queue, and these are the only record of
    #: what it became.
    harvest_invoice_number: str | None = None
    harvest_invoice_id: int | None = None
    invoice_issue_date: date | None = None
    invoice_due_date: date | None = None
    invoiced_amount: float | None = None
    invoiced_at: datetime | None = None


class DrawReleaseRequest(ORMBase):
    """`released=true` confirms delivery — the only thing that makes a draw
    billable."""

    released: bool


class DrawPreview(ORMBase):
    """The exact invoice a draw would produce, computed on read.

    Nothing behind this is persisted — it is recomputed on every request, so it
    always reflects the group config as it stands right now rather than as it
    stood when someone last looked. A draw covers no period, so there is no
    `period_start` / `period_end` here and no timing to resolve.
    """

    draw_id: UUID
    billing_group_id: UUID
    billing_group_name: str | None = None
    harvest_client_name: str | None = None
    description: str
    state: str
    amount: float
    issue_date: date
    due_date: date | None = None
    payment_term: str | None = None
    subject: str
    notes: str | None = None
    estimated_line_items: list[dict[str, Any]] = Field(default_factory=list)
    planned_payload: dict[str, Any] = Field(default_factory=dict)
    flags: list[dict[str, Any]] = Field(default_factory=list)
    #: False when the draw is not `ready`, or its group is inactive. The
    #: execution path checks this again — this copy is so the button can say why.
    billable: bool = False


class DrawInvoiceRequest(ORMBase):
    """Everything else is derived server-side.

    Notably absent: the payload. The client just looked at it via `DrawPreview`,
    but accepting it back would let a caller post a body the operator never saw.
    `invoice_draw` recomputes it — the preview is a pure function, so recomputing
    is free and the authorization stays honest.

    `issue_date` defaults to **the day of the request**, which is what makes the
    due date "drafted + terms" rather than "previewed + terms". Supplying it moves
    the issue and due dates together; neither can be shifted alone.
    """

    issue_date: date | None = None


class DrawInvoiceResponse(ORMBase):
    """What Harvest created, plus the ledger row that records it."""

    draw_id: UUID
    billing_run_id: UUID
    billing_run_item_id: UUID
    harvest_invoice_id: int
    harvest_invoice_number: str | None = None
    planned_amount: float
    #: The amount **at creation**. Drafts are freely edited in Harvest before
    #: sending, so this is not necessarily what the client was billed (PRD §10).
    actual_amount: float
    variance: float
    #: The dates actually used, which may be later than any preview the operator
    #: looked at — both anchor to the moment of creation.
    issue_date: date
    due_date: date | None = None
    payment_term: str | None = None


class BillingSettings(ORMBase):
    """Account-level billing config a human edits. Deploy config stays in env."""

    default_invoice_notes: str = ""


class BillingSettingsUpdate(ORMBase):
    """Only the fields present are written, so the form can PATCH one field.

    `None` means "leave alone"; an empty string means "deliberately blank", which
    for notes is a real choice — send none.
    """

    default_invoice_notes: str | None = None


class CreatedInvoice(ORMBase):
    """One invoice this system created, from either kind of run.

    Everything here was captured at creation time; nothing is re-read from
    Harvest. `actual_amount` is what Harvest returned **then** — a draft edited
    afterwards (retainer overages, added lines) will not match what the client
    was sent (PRD §10).
    """

    billing_run_item_id: UUID
    billing_run_id: UUID
    billing_group_id: UUID | None = None
    billing_group_name: str | None = None
    harvest_client_id: int | None = None
    harvest_client_name: str | None = None
    billing_type: str | None = None
    status: str
    #: `draw` | `monthly`. Draws are the only kind that can produce one today;
    #: monthly rows appear here unchanged once its execution ships.
    kind: str
    run_month: date
    harvest_invoice_id: int | None = None
    harvest_invoice_number: str | None = None
    planned_amount: float
    actual_amount: float | None = None
    variance: float | None = None
    issue_date: date | None = None
    due_date: date | None = None
    #: Null for a draw — a draw is a contract event and covers no service period.
    period_start: date | None = None
    period_end: date | None = None
    #: Set for a draw only; the milestone's name once it has left the queue.
    draw_description: str | None = None
    draw_sequence: int | None = None
    error_message: str | None = None
    created_at: datetime


class CreatedInvoiceTotals(ORMBase):
    count: int = 0
    draw_count: int = 0
    monthly_count: int = 0
    total_amount: float = 0.0
    #: Rows linked by hand with no amount recorded, so their contribution to
    #: `total_amount` is the planned figure rather than a confirmed one.
    unverified_count: int = 0


class InFlightItem(ORMBase):
    """One unresolved in-flight row — a Harvest write whose outcome is unknown."""

    billing_run_item_id: UUID
    billing_run_id: UUID
    billing_group_id: UUID
    billing_group_name: str | None = None
    harvest_client_name: str | None = None
    fixed_fee_schedule_item_id: UUID | None = None
    draw_description: str | None = None
    planned_amount: float
    issue_date: date | None = None
    created_at: datetime


class ResolveInFlightRequest(ORMBase):
    """A human's statement about what actually happened in Harvest.

    `link` requires `harvest_invoice_id`. `actual_amount` is optional and stays
    null when omitted, along with `variance` — a variance of exactly zero would
    read as a verified match rather than an unknown.
    """

    resolution: Literal["link", "failed"]
    harvest_invoice_id: int | None = None
    harvest_invoice_number: str | None = None
    actual_amount: float | None = None


class ResolveInFlightResponse(ORMBase):
    billing_run_id: UUID
    billing_run_item_id: UUID
    resolution: str
    status: str
    fixed_fee_schedule_item_id: UUID | None = None
    harvest_invoice_id: int | None = None


class RecurringItem(ORMBase):
    id: UUID
    harvest_project_id: int
    description: str
    quantity: float
    unit_price: float
    # Harvest invoice item category name — "Service", "Billable Expense",
    # "Discount", etc. Validated against the account's own categories.
    kind: str = "Service"
    # Amount is decided per month on the pre-flight, not here. Plans at $0
    # until then, and blocks approval of the invoice.
    is_placeholder: bool = False
    sort_order: int = 0
    effective_from: date | None = None
    effective_to: date | None = None


class RecurringItemInput(ORMBase):
    """One recurring line item as submitted by the group form.

    `id` is present when the form is editing a line that already exists, absent
    for a new one. Round-tripping it is what keeps the row's identity stable
    across a save — placeholder resolutions hang off that id, so a save that
    re-minted it would discard the amounts already entered for this month.
    """

    id: UUID | None = None
    harvest_project_id: int
    description: str
    quantity: float = 1
    unit_price: float = 0
    kind: str = "Service"
    is_placeholder: bool = False
    sort_order: int = 0
    effective_from: date | None = None
    effective_to: date | None = None


class BillingGroupCreate(ORMBase):
    name: str
    harvest_client_id: int
    harvest_client_name: str | None = None
    billing_type: BillingType
    billing_timing: BillingTiming = "arrears"
    payment_term: PaymentTerm = "net 30"
    custom_net_days: int | None = None
    time_summary_type: TimeSummaryType | None = None
    include_expenses: bool = False
    expense_summary_type: ExpenseSummaryType | None = None
    attach_receipts: bool = False
    subject_template: str = "{client_name} — {period_label}"
    notes_template: str | None = None
    purchase_order: str | None = None
    requires_purchase_order: bool = False
    currency: str | None = None
    projects: list[GroupProject] = Field(default_factory=list)
    recurring_items: list[RecurringItemInput] = Field(default_factory=list)
    schedule_items: list[ScheduleItemInput] = Field(default_factory=list)


class BillingGroupUpdate(ORMBase):
    """Every field optional — only what's present is written."""

    name: str | None = None
    harvest_client_id: int | None = None
    harvest_client_name: str | None = None
    billing_type: BillingType | None = None
    billing_timing: BillingTiming | None = None
    payment_term: PaymentTerm | None = None
    custom_net_days: int | None = None
    time_summary_type: TimeSummaryType | None = None
    include_expenses: bool | None = None
    expense_summary_type: ExpenseSummaryType | None = None
    attach_receipts: bool | None = None
    subject_template: str | None = None
    notes_template: str | None = None
    purchase_order: str | None = None
    requires_purchase_order: bool | None = None
    currency: str | None = None
    projects: list[GroupProject] | None = None
    recurring_items: list[RecurringItemInput] | None = None
    schedule_items: list[ScheduleItemInput] | None = None


class BillingGroupResponse(ORMBase):
    id: UUID
    name: str
    harvest_client_id: int
    harvest_client_name: str | None = None
    billing_type: BillingType
    billing_timing: BillingTiming
    payment_term: PaymentTerm
    custom_net_days: int | None = None
    time_summary_type: TimeSummaryType | None = None
    include_expenses: bool
    expense_summary_type: ExpenseSummaryType | None = None
    attach_receipts: bool
    subject_template: str
    notes_template: str | None = None
    purchase_order: str | None = None
    requires_purchase_order: bool
    currency: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    projects: list[GroupProject] = Field(default_factory=list)
    schedule_items: list[ScheduleItem] = Field(default_factory=list)
    recurring_items: list[RecurringItem] = Field(default_factory=list)


# ── Harvest catalog (for building group config) ─────────────────────────────


class InvoiceItemCategory(ORMBase):
    """A valid `kind` for free-form line items, per the Harvest account."""

    harvest_id: int
    name: str


class HarvestClientOption(ORMBase):
    harvest_id: int
    name: str
    currency: str | None = None
    is_active: bool = True
    billable_project_count: int = 0


class HarvestProjectOption(ORMBase):
    harvest_id: int
    name: str
    client_id: int
    client_name: str | None = None
    client_currency: str | None = None
    is_active: bool = True
    is_fixed_fee: bool = False
    hourly_rate: float | None = None
    # Null when the project is free to map. Set when another active group
    # already claims it — shown in the picker rather than hidden.
    billing_group_id: UUID | None = None
    billing_group_name: str | None = None


# ── Health ──────────────────────────────────────────────────────────────────


class UnmappedProject(ORMBase):
    harvest_project_id: int
    harvest_project_name: str
    harvest_client_name: str | None = None
    uninvoiced_hours: float = 0.0
    estimated_value: float = 0.0
    is_active: bool = True


class Flag(ORMBase):
    code: str
    severity: FlagSeverity
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class SnapshotInfo(ORMBase):
    clients: int
    projects: int
    invoice_item_categories: list[str] = Field(default_factory=list)
    fetched_at: datetime | None = None
    #: Account web address for linking out to an invoice. Empty when unconfigured
    #: — no Harvest endpoint exposes it, so the UI omits the link rather than
    #: guessing a subdomain.
    harvest_base_uri: str = ""


class HealthResponse(ORMBase):
    unmapped_projects: list[UnmappedProject] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    snapshot: SnapshotInfo
    counts: dict[str, int] = Field(default_factory=dict)


class SnapshotRefreshResponse(ORMBase):
    clients: int
    projects: int
    invoice_item_categories: int
    task_assignments: int


# ── Runs ────────────────────────────────────────────────────────────────────


PlaceholderState = Literal["unresolved", "resolved", "omitted"]


class EstimatedLineItem(ORMBase):
    """One row of the pre-flight table.

    For a `recurring_monthly` group this is more than display: the five fields
    below make the entry a complete description of a line, so
    `planned_payload["line_items"]` can be rebuilt from the array without
    re-reading config. That is what lets a placeholder be resolved against the
    plan the operator actually reviewed rather than against config as it stands
    now. `label` is already the *rendered* description, which is verbatim what
    Harvest receives.

    All five are null for T&M and draw entries — T&M lines are aggregated from
    time entries and have no config row behind them, and a draw's single line is
    rebuilt from the schedule item instead.
    """

    label: str
    detail: str | None = None
    quantity: float
    unit: str
    unit_price: float
    amount: float
    project_name: str | None = None

    recurring_line_item_id: UUID | None = None
    harvest_project_id: int | None = None
    #: Harvest invoice item category name.
    kind: str | None = None
    is_placeholder: bool = False
    #: `unresolved` blocks approval; `omitted` drops the line from the payload
    #: while keeping it on screen. Null when the line is not a placeholder.
    placeholder_state: PlaceholderState | None = None


class RunItemResponse(ORMBase):
    id: UUID
    billing_group_id: UUID
    billing_group_name: str | None = None
    harvest_client_name: str | None = None
    billing_type: BillingType | None = None
    billing_timing: BillingTiming | None = None
    status: str
    run_month: date
    period_start: date | None = None
    period_end: date | None = None
    issue_date: date | None = None
    due_date: date | None = None
    planned_amount: float
    prior_amount: float | None = None
    actual_amount: float | None = None
    variance: float | None = None
    harvest_invoice_id: int | None = None
    harvest_invoice_number: str | None = None
    error_message: str | None = None
    skip_reason: str | None = None
    # Review state. `status == 'approved'` is the source of truth for approval;
    # `error_override` is sticky and survives un-approving.
    approved_at: datetime | None = None
    approved_by: str | None = None
    error_override: bool = False
    estimated_line_items: list[EstimatedLineItem] = Field(default_factory=list)
    planned_payload: dict[str, Any] = Field(default_factory=dict)
    flags: list[Flag] = Field(default_factory=list)


class BillingRunSummary(ORMBase):
    id: UUID
    run_month: date
    label: str
    status: str
    # A `draw` run bills a single fixed-fee draw off-cycle: one item, no period.
    kind: str = "monthly"
    created_at: datetime
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    planned_count: int = 0
    skipped_count: int = 0
    planned_total: float = 0.0
    flag_counts: dict[str, int] = Field(default_factory=dict)


class BillingRunDetail(BillingRunSummary):
    run_flags: list[Flag] = Field(default_factory=list)
    items: list[RunItemResponse] = Field(default_factory=list)


class PlanRunRequest(ORMBase):
    """`run_month` accepts any day; it is normalized to the first of the month.

    Defaults to the current calendar month. Override for backfills, and for the
    case where you run on the 1st or 2nd and mean the prior month.
    """

    run_month: date | None = None


class ItemApprovalRequest(ORMBase):
    """Both fields are optional and independent.

    `approved` moves the group between `planned` and `approved`. `override`
    records that a human accepted an error-severity flag; it is sticky, so
    un-approving does not withdraw it.
    """

    approved: bool | None = None
    override: bool | None = None


class PlaceholderResolutionRequest(ORMBase):
    """The operator's decision about one placeholder line, for one run month.

    `amount` requires `unit_price`; a missing price is refused rather than
    stored as zero, which would be indistinguishable from undecided and would
    bill the client nothing. `quantity` is optional and falls back to the
    template's — present when the placeholder is quantity-shaped, e.g. 12
    overage hours at $175 rather than a flat sum.

    `omitted` needs neither: it drops the line from this month's invoice and
    leaves the template in place, so it comes back next month. `note` is where
    "checked Harvest, no overage" goes — most valuable on an omit, where the
    record would otherwise be indistinguishable from a line that never existed.
    """

    resolution: Literal["amount", "omitted"]
    unit_price: float | None = None
    quantity: float | None = None
    note: str | None = None


class BulkApprovalRequest(ORMBase):
    """`approved=true` approves every group that is *already* approvable — it
    never overrides an error flag on the operator's behalf."""

    approved: bool
