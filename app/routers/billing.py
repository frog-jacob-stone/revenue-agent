"""Billing / invoicing router.

Operator-initiated throughout. Every endpoint here is reached by a human in the
UI, authenticated, and audited — and none of them is in any agent's
`allowed_tools`.

Unbreakable Rule #1 and ADR-0004: the rule requires a human to authorize the
specific payload, not that an `approvals` row exist. For `POST /draws/{id}/invoice`
— the one endpoint in this system that writes to Harvest — the operator has just
read the exact invoice via `GET /draws/{id}/preview`, and their click is the
authorization. There is no approval row and no executor. Everything else here
writes only to our own store.
"""
from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import AuthUser, get_current_user
from app.config import settings
from app.db import get_pool
from app.integrations import harvest
from app.models.billing import (
    BillingGroupCreate,
    BillingGroupResponse,
    BillingGroupUpdate,
    BillingRunDetail,
    BillingRunSummary,
    BillingSettings,
    BillingSettingsUpdate,
    BulkApprovalRequest,
    CreatedInvoice,
    CreatedInvoiceTotals,
    DrawInvoiceRequest,
    DrawInvoiceResponse,
    DrawPreview,
    DrawReleaseRequest,
    DrawResponse,
    HarvestClientOption,
    HarvestProjectOption,
    HealthResponse,
    InFlightItem,
    InvoiceItemCategory,
    ItemApprovalRequest,
    PlaceholderResolutionRequest,
    PlanRunRequest,
    ResolveInFlightRequest,
    ResolveInFlightResponse,
    SnapshotRefreshResponse,
)
from app.services.billing import (
    catalog,
    draws,
    harvest_snapshot,
    inflight,
    invoices,
    placeholders,
    planner,
    reconcile,
    review,
    settings_store,
)
from app.services.billing import groups as groups_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


# ── Billing groups ──────────────────────────────────────────────────────────


@router.get("/groups", response_model=list[BillingGroupResponse])
async def list_billing_groups(
    billing_type: str | None = None,
    include_inactive: bool = False,
    pool: asyncpg.Pool = Depends(_db),
):
    rows = await groups_service.list_groups(
        pool,
        billing_type=billing_type,
        is_active=None if include_inactive else True,
    )
    return [BillingGroupResponse.model_validate(r) for r in rows]


@router.post("/groups", response_model=BillingGroupResponse, status_code=201)
async def create_billing_group(
    body: BillingGroupCreate,
    pool: asyncpg.Pool = Depends(_db),
):
    try:
        row = await groups_service.create_group(pool, body.model_dump())
    except groups_service.BillingConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return BillingGroupResponse.model_validate(row)


@router.get("/groups/{group_id}", response_model=BillingGroupResponse)
async def get_billing_group(group_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    row = await groups_service.get_group(pool, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Billing group not found")
    return BillingGroupResponse.model_validate(row)


@router.patch("/groups/{group_id}", response_model=BillingGroupResponse)
async def update_billing_group(
    group_id: UUID,
    body: BillingGroupUpdate,
    pool: asyncpg.Pool = Depends(_db),
):
    payload = body.model_dump(exclude_unset=True)
    try:
        row = await groups_service.update_group(pool, group_id, payload)
    except groups_service.BillingConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="Billing group not found")
    return BillingGroupResponse.model_validate(row)


@router.post("/groups/{group_id}/deactivate", response_model=BillingGroupResponse)
async def deactivate_billing_group(group_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    row = await groups_service.deactivate_group(pool, group_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Billing group not found")
    return BillingGroupResponse.model_validate(row)


# ── Harvest catalog ─────────────────────────────────────────────────────────
# Reads the local snapshot cache, not Harvest. Backs the group-config form.


@router.get("/harvest/item-categories", response_model=list[InvoiceItemCategory])
async def list_invoice_item_categories(pool: asyncpg.Pool = Depends(_db)):
    """Valid `kind` values for free-form line items, from the cached snapshot."""
    rows = await pool.fetch(
        "SELECT harvest_id, name FROM harvest_invoice_item_categories ORDER BY name"
    )
    return [InvoiceItemCategory.model_validate(dict(r)) for r in rows]


@router.get("/harvest/clients", response_model=list[HarvestClientOption])
async def list_harvest_clients(
    include_excluded: bool = Query(
        False,
        description=(
            "Include clients on the account-wide exclusion list. Off for new "
            "config; the edit form turns it on so a group whose client was "
            "excluded after it was built stays editable."
        ),
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    rows = await catalog.list_clients(pool, include_excluded=include_excluded)
    return [HarvestClientOption.model_validate(r) for r in rows]


@router.get("/harvest/projects", response_model=list[HarvestProjectOption])
async def list_harvest_projects(
    client_id: int | None = None,
    include_inactive: bool = False,
    include_excluded: bool = Query(
        False,
        description="Include projects of clients on the account-wide exclusion list.",
    ),
    exclude_group_id: UUID | None = Query(
        None,
        description=(
            "Treat this group's own projects as unassigned, so the edit form "
            "doesn't report a group as conflicting with itself."
        ),
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    rows = await catalog.list_projects(
        pool,
        client_id=client_id,
        include_inactive=include_inactive,
        include_excluded=include_excluded,
        exclude_group_id=exclude_group_id,
    )
    return [HarvestProjectOption.model_validate(r) for r in rows]


# ── Config health ───────────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
async def billing_health(
    include_time: bool = Query(
        True,
        description=(
            "Price uninvoiced time on unmapped projects. Costs one Harvest "
            "request per unmapped project; set false for a fast structural check."
        ),
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    report = await reconcile.reconcile_config(pool, settings, include_time=include_time)
    return HealthResponse.model_validate(report)


@router.post("/snapshot/refresh", response_model=SnapshotRefreshResponse)
async def refresh_harvest_snapshot(pool: asyncpg.Pool = Depends(_db)):
    """Pull clients, projects, invoice item categories, and task assignments
    into the local cache. Read-only against Harvest."""
    summary = await harvest_snapshot.refresh_snapshot(pool, settings)
    return SnapshotRefreshResponse.model_validate(summary)


# ── Billing runs ────────────────────────────────────────────────────────────


@router.get("/runs", response_model=list[BillingRunSummary])
async def list_billing_runs(
    limit: int = Query(24, ge=1, le=120),
    kind: str | None = Query(
        "monthly",
        description=(
            "monthly | draw. Defaults to monthly; pass an empty value for both. "
            "Draw runs are single-invoice and frequent, so mixing them in by "
            "default would bury the monthly history."
        ),
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    rows = await planner.list_runs(pool, limit=limit, kind=kind or None)
    return [BillingRunSummary.model_validate(r) for r in rows]


@router.post("/runs", response_model=BillingRunDetail, status_code=201)
async def plan_billing_run(
    body: PlanRunRequest | None = None,
    pool: asyncpg.Pool = Depends(_db),
):
    """Plan a run. Strictly read-only against Harvest — no invoice is created.

    Re-planning a month abandons the prior live plan for that month, except for
    in-flight rows, which only a human may resolve.
    """
    run_month = body.run_month if body else None
    run_id = await planner.plan_run(pool, settings, run_month=run_month)
    detail = await planner.get_run(pool, run_id)
    return BillingRunDetail.model_validate(detail)


@router.get("/runs/{run_id}", response_model=BillingRunDetail)
async def get_billing_run(run_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    detail = await planner.get_run(pool, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Billing run not found")
    return BillingRunDetail.model_validate(detail)


# ── Fixed-fee draws ─────────────────────────────────────────────────────────
#
# Draws are event-driven: a draw becomes billable when a human confirms
# delivery, and is then billed on its own, off-cycle. They never ride a monthly
# run, which is why these are their own endpoints rather than run parameters.


@router.get("/draws", response_model=list[DrawResponse])
async def list_draws(
    group_id: UUID | None = None,
    state: str | None = Query(
        None, description="pending | ready | prepared | invoiced. Derived, not stored."
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    try:
        rows = await draws.list_draws(pool, group_id=group_id, state=state)
    except draws.DrawError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [DrawResponse.model_validate(r) for r in rows]


@router.post("/draws/{draw_id}/release", response_model=DrawResponse)
async def release_draw(
    draw_id: UUID,
    body: DrawReleaseRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Confirm (or withdraw) delivery. Human-only — this is the entire billing
    trigger for a fixed-fee contract, so nothing in the system calls it."""
    try:
        row = await draws.set_release(
            pool, draw_id,
            released=body.released,
            actor=user.email or str(user.id),
        )
    except draws.DrawError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="Draw not found")
    return DrawResponse.model_validate(row)


@router.get("/draws/{draw_id}/preview", response_model=DrawPreview)
async def preview_draw_invoice(
    draw_id: UUID,
    issue_date: date | None = Query(
        None, description="Defaults to today. A draw covers no period."
    ),
    pool: asyncpg.Pool = Depends(_db),
):
    """The exact invoice this draw would produce. Writes nothing, anywhere.

    A GET on purpose: the invoice is a pure function of the group config and the
    draw, so there is no staged copy to keep in sync and no state to unwind if
    the operator looks and walks away. The ledger row is written by execution,
    immediately before the POST to Harvest.
    """
    preview = await draws.preview_draw_invoice(pool, draw_id, issue_date=issue_date)
    if preview is None:
        raise HTTPException(status_code=404, detail="Draw not found")
    return DrawPreview.model_validate(preview)


@router.post("/draws/{draw_id}/invoice", response_model=DrawInvoiceResponse)
async def invoice_draw(
    draw_id: UUID,
    body: DrawInvoiceRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Create the Harvest **draft** invoice for one released draw. Human-only.

    The only endpoint in this system that writes to Harvest. Operator-initiated
    per ADR-0004: the caller has just seen this exact payload via
    `GET /draws/{draw_id}/preview` and their click is the authorization, so there
    is no approval row. Nothing in the system calls this — no scheduler, no
    planner, no agent, and it is in no tool's `allowed_tools`.

    Status codes carry the §8 distinction that matters:

      200 — created. `harvest_invoice_id` is real.
      409 — refused before anything was attempted (not released, already
            invoiced, in flight, or blocked by an error flag). Nothing changed.
      422 — Harvest rejected the payload. Nothing was created; fix and retry.
      502 — **unknown**. The POST never returned a verdict, so the invoice may
            exist. The ledger row is left in flight and this draw is locked until
            a human resolves it. Deliberately not 500: the request was
            well-formed and our side behaved correctly.
    """
    actor = user.email or str(user.id)
    try:
        result = await draws.invoice_draw(
            pool, settings, draw_id, issue_date=body.issue_date, actor=actor,
        )
    except draws.DrawError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except draws.DrawWriteUnknown as exc:
        # The one case where the operator must be told to go look at Harvest.
        logger.error("draw %s: unknown Harvest write outcome: %s", draw_id, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "billing_run_id": str(exc.run_id),
                "billing_run_item_id": str(exc.item_id),
                "remedy": (
                    "Check Harvest for an invoice matching this client and amount, "
                    "then resolve the in-flight row: link it if it exists, or mark "
                    "it failed if it does not."
                ),
            },
        )
    except harvest.HarvestValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc.body or exc))
    except harvest.HarvestRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Harvest rate limit exceeded after retries; nothing was created. {exc}",
        )
    except harvest.HarvestError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return DrawInvoiceResponse.model_validate(result)


# ── Account-level settings ──────────────────────────────────────────────────


@router.get("/settings", response_model=BillingSettings)
async def get_billing_settings(pool: asyncpg.Pool = Depends(_db)):
    """Account-level billing config. Secrets and deploy identity are not here —
    those stay in environment variables and are never served over the API."""
    return BillingSettings.model_validate(await settings_store.get_all(pool))


@router.patch("/settings", response_model=BillingSettings)
async def update_billing_settings(
    body: BillingSettingsUpdate,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Update account-level billing config. Human-only (ADR-0004).

    Only fields present in the body are written. An empty string is a real value
    — for notes it means "send none" — so it is stored rather than ignored.
    """
    values = body.model_dump(exclude_unset=True, exclude_none=True)
    if not values:
        return BillingSettings.model_validate(await settings_store.get_all(pool))
    try:
        updated = await settings_store.update(
            pool, values, actor=user.email or str(user.id)
        )
    except settings_store.UnknownSettingError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return BillingSettings.model_validate(updated)


# ── Drafted: invoices this system created ───────────────────────────────────


@router.get("/invoices", response_model=list[CreatedInvoice])
async def list_created_invoices(
    kind: str | None = Query(
        None, description="draw | monthly. Omit for both — the usual case."
    ),
    status: str = Query(
        "created",
        description=(
            "created | failed | in_flight. Defaults to `created`, the only "
            "status that means an invoice exists in Harvest."
        ),
    ),
    group_id: UUID | None = None,
    since: date | None = Query(
        None, description="Only rows created on or after this date."
    ),
    limit: int = Query(200, ge=1, le=1000),
    pool: asyncpg.Pool = Depends(_db),
):
    """Every invoice this system created, newest first, both kinds together.

    Reads the ledger. Ordered by creation rather than issue date: monthly issue
    dates are backdated to period boundaries, so ordering by them would bury a
    July invoice created in September among July's own work.
    """
    try:
        rows = await invoices.list_created_invoices(
            pool, kind=kind, status=status, group_id=group_id,
            since=since, limit=limit,
        )
    except invoices.InvoiceQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [CreatedInvoice.model_validate(r) for r in rows]


@router.get("/invoices/totals", response_model=CreatedInvoiceTotals)
async def created_invoice_totals(
    since: date | None = None, pool: asyncpg.Pool = Depends(_db),
):
    """Counts and value of what has been created, split by kind."""
    return CreatedInvoiceTotals.model_validate(
        await invoices.created_invoice_totals(pool, since=since)
    )


# ── In-flight resolution ────────────────────────────────────────────────────


@router.get("/in-flight", response_model=list[InFlightItem])
async def list_in_flight(pool: asyncpg.Pool = Depends(_db)):
    """Every ledger row whose Harvest write never returned a verdict.

    Should always be empty. A non-empty list means a draw or group is locked and
    needs a human to look at Harvest.
    """
    rows = await inflight.list_unresolved(pool)
    return [InFlightItem.model_validate(r) for r in rows]


@router.post(
    "/runs/{run_id}/items/{item_id}/resolve", response_model=ResolveInFlightResponse
)
async def resolve_in_flight(
    run_id: UUID,
    item_id: UUID,
    body: ResolveInFlightRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Settle one in-flight row. Human-only.

    Item-level rather than draw-level because `in_flight` belongs to the ledger;
    the monthly run will produce these rows too and will reuse this endpoint
    unchanged. The `harvest_invoice_id` supplied for a `link` is taken at face
    value — PRD §8 escalates ambiguity to a person, and a system that then
    second-guesses that person has resolved nothing.
    """
    actor = user.email or str(user.id)
    try:
        result = await inflight.resolve_item(
            pool, run_id, item_id,
            resolution=body.resolution,
            harvest_invoice_id=body.harvest_invoice_id,
            harvest_invoice_number=body.harvest_invoice_number,
            actual_amount=body.actual_amount,
            actor=actor,
        )
    except inflight.InFlightError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not result:
        raise HTTPException(status_code=404, detail="In-flight row not found")
    return ResolveInFlightResponse.model_validate(result)


@router.post("/runs/{run_id}/items/{item_id}/approval", response_model=BillingRunDetail)
async def set_item_approval(
    run_id: UUID,
    item_id: UUID,
    body: ItemApprovalRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Approve or un-approve one group, and/or record an error override.

    Persisted immediately — the pre-flight review survives a reload. Approval
    is human-only; the actor recorded is the authenticated user.
    """
    try:
        found = await review.set_item_approval(
            pool, run_id, item_id,
            approved=body.approved,
            override=body.override,
            actor=user.email or str(user.id),
        )
    except review.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not found:
        raise HTTPException(status_code=404, detail="Billing run item not found")
    return BillingRunDetail.model_validate(await planner.get_run(pool, run_id))


@router.post(
    "/runs/{run_id}/items/{item_id}/placeholders/{line_item_id}",
    response_model=BillingRunDetail,
)
async def set_placeholder_resolution(
    run_id: UUID,
    item_id: UUID,
    line_item_id: UUID,
    body: PlaceholderResolutionRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Price one placeholder line, or omit it for this run month. Human-only.

    Writes nothing to Harvest — this settles what the eventual draft will say,
    which is why it has to happen before approval rather than in the Harvest
    draft afterwards. Rebuilds `planned_payload` from the ledger row, so the
    payload on screen stays the payload that would be sent (ADR-0004 condition
    1); if the group was already approved, that approval is withdrawn, because
    it described the old numbers.

    Idempotent on `(item, line, month)` — re-submitting an amount replaces the
    decision rather than recording a second one — but `POST` rather than `PUT`,
    matching every other state change in this router. `POST .../approval` is the
    direct precedent: also idempotent, also a decision recorded against one
    ledger row. `PUT` would additionally be the app's only one, and the CORS
    middleware's `allow_methods` does not list it.
    """
    try:
        found = await placeholders.set_resolution(
            pool, run_id, item_id, line_item_id,
            resolution=body.resolution,
            unit_price=body.unit_price,
            quantity=body.quantity,
            note=body.note,
            actor=user.email or str(user.id),
        )
    except placeholders.PlaceholderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except placeholders.PlaceholderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not found:
        raise HTTPException(status_code=404, detail="Billing run item not found")
    return BillingRunDetail.model_validate(await planner.get_run(pool, run_id))


@router.delete(
    "/runs/{run_id}/items/{item_id}/placeholders/{line_item_id}",
    response_model=BillingRunDetail,
)
async def clear_placeholder_resolution(
    run_id: UUID,
    item_id: UUID,
    line_item_id: UUID,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Withdraw a decision, returning the line to undecided at $0. Human-only.

    The line blocks approval again, which is the point: this is the retreat from
    a number entered by mistake, not a way to skip the question.
    """
    try:
        found = await placeholders.clear_resolution(
            pool, run_id, item_id, line_item_id,
            actor=user.email or str(user.id),
        )
    except placeholders.PlaceholderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except placeholders.PlaceholderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not found:
        raise HTTPException(status_code=404, detail="Billing run item not found")
    return BillingRunDetail.model_validate(await planner.get_run(pool, run_id))


@router.post("/runs/{run_id}/approval", response_model=BillingRunDetail)
async def set_run_approval(
    run_id: UUID,
    body: BulkApprovalRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Approve every already-approvable group, or clear every approval."""
    try:
        await review.set_all_approvals(
            pool, run_id, approved=body.approved, actor=user.email or str(user.id)
        )
    except review.ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    detail = await planner.get_run(pool, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Billing run not found")
    return BillingRunDetail.model_validate(detail)


@router.post("/runs/{run_id}/abandon", response_model=BillingRunDetail)
async def abandon_billing_run(run_id: UUID, pool: asyncpg.Pool = Depends(_db)):
    try:
        found = await planner.abandon_run(pool, run_id)
    except planner.RunStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not found:
        raise HTTPException(status_code=404, detail="Billing run not found")
    return BillingRunDetail.model_validate(await planner.get_run(pool, run_id))
