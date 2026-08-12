"""Harvest API v2 client.

⚠️ WRITE GUARDRAIL — the following endpoints must never appear in this module
or anywhere else under `app/`. `tests/test_harvest_write_guardrail.py` enforces
this in CI (see docs/prd/harvest-invoicing-requirements.md §4.9):

  - POST   /v2/invoices/{id}/messages   (any variant, including event_type=send)
  - DELETE /v2/invoices/{id}
  - PATCH  /v2/invoices/{id}
  - POST   /v2/invoices/{id}/payments
  - the `retainer_id` field on invoice creation

The system creates draft invoices and stops. A human sends them from Harvest.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.integrations.harvest_limiter import HarvestRateLimiter
from app.integrations.harvest_limiter import limiter as _default_limiter

logger = logging.getLogger(__name__)

_BASE = "https://api.harvestapp.com/v2"

# Harvest caps per_page at 2000; the default of 100 keeps individual responses
# small enough that a timeout doesn't cost a whole page of work.
_DEFAULT_PER_PAGE = 100
_MAX_PER_PAGE = 2000

_MAX_429_RETRIES = 3
_DEFAULT_RETRY_AFTER = 15.0
_TIMEOUT = httpx.Timeout(30.0)


# ── Exceptions ──────────────────────────────────────────────────────────────


class HarvestError(Exception):
    """Base for every Harvest API failure."""

    def __init__(self, message: str, *, status: int | None = None,
                 path: str = "", body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.path = path
        self.body = body


class HarvestAuthError(HarvestError):
    """401 / 403 — token or account id is wrong, or the scope is insufficient."""


class HarvestNotFoundError(HarvestError):
    """404 — the resource does not exist."""


class HarvestValidationError(HarvestError):
    """422 — Harvest rejected the payload. `body` carries its explanation.

    The common cause on invoice creation is a project that does not belong to
    the given client. Pre-flight validates that case so it never reaches here.
    """


class HarvestRateLimited(HarvestError):
    """429 — retry is safe; the request never reached the resource."""

    def __init__(self, message: str, *, retry_after: float, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class HarvestServerError(HarvestError):
    """5xx — Harvest is unwell. Never auto-retried on write paths."""


def _retry_after_seconds(resp: httpx.Response) -> float:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return _DEFAULT_RETRY_AFTER
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_RETRY_AFTER


def _raise_for_status(resp: httpx.Response, path: str) -> None:
    status = resp.status_code
    if status < 400:
        return
    try:
        body: Any = resp.json()
    except Exception:
        body = resp.text
    msg = f"Harvest {status} on {path}: {body}"
    if status in (401, 403):
        raise HarvestAuthError(msg, status=status, path=path, body=body)
    if status == 404:
        raise HarvestNotFoundError(msg, status=status, path=path, body=body)
    if status == 422:
        raise HarvestValidationError(msg, status=status, path=path, body=body)
    if status == 429:
        raise HarvestRateLimited(
            msg, status=status, path=path, body=body,
            retry_after=_retry_after_seconds(resp),
        )
    if status >= 500:
        raise HarvestServerError(msg, status=status, path=path, body=body)
    raise HarvestError(msg, status=status, path=path, body=body)


# ── Transport ───────────────────────────────────────────────────────────────


def _headers(cfg: Settings) -> dict[str, str]:
    contact = getattr(cfg, "harvest_user_agent_contact", "") or ""
    agent = f"RevenueAgent/1.0 ({contact})" if contact else "RevenueAgent/1.0"
    return {
        "Authorization": f"Bearer {cfg.harvest_token.get_secret_value()}",
        "Harvest-Account-Id": cfg.harvest_account_id,
        "User-Agent": agent,
    }


async def _request(
    cfg: Settings,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    limiter: HarvestRateLimiter | None = None,
) -> dict[str, Any]:
    """One rate-limited GET, retrying only on 429 (which never reached the resource)."""
    lim = limiter or _default_limiter
    attempts = 0
    while True:
        await lim.acquire(path)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_BASE}{path}", headers=_headers(cfg), params=params)
        try:
            _raise_for_status(resp, path)
        except HarvestRateLimited as exc:
            attempts += 1
            if attempts > _MAX_429_RETRIES:
                raise
            await lim.penalize(path, exc.retry_after)
            continue
        return resp.json()


async def _post(
    cfg: Settings,
    path: str,
    body: dict[str, Any],
    *,
    limiter: HarvestRateLimiter | None = None,
) -> dict[str, Any]:
    """One rate-limited POST. Retries **only** on 429.

    Deliberately a sibling of `_request` rather than a `method=` parameter on it.
    The two have the same retry rule for opposite reasons, and collapsing them
    would hide that: a GET may be retried because repeating it is harmless, while
    a POST to /invoices may be retried *only* on 429 because that is the one
    status proving the request never reached invoice creation. Harvest has no
    idempotency keys, so any other retry risks a second invoice (PRD §8).

    Every non-429 failure — 4xx, 5xx, timeout, connection error — propagates
    untouched. The caller decides what it means, because only the caller knows
    whether the write may have landed. See `draws.invoice_draw`.
    """
    lim = limiter or _default_limiter
    attempts = 0
    while True:
        await lim.acquire(path)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(f"{_BASE}{path}", headers=_headers(cfg), json=body)
        try:
            _raise_for_status(resp, path)
        except HarvestRateLimited as exc:
            attempts += 1
            if attempts > _MAX_429_RETRIES:
                raise
            await lim.penalize(path, exc.retry_after)
            continue
        return resp.json()


async def _get_all(
    cfg: Settings,
    path: str,
    key: str,
    params: dict[str, Any] | None = None,
    *,
    per_page: int = _DEFAULT_PER_PAGE,
    limiter: HarvestRateLimiter | None = None,
) -> list[dict[str, Any]]:
    """Paginate through every page of a Harvest list endpoint."""
    results: list[dict] = []
    page = 1
    per_page = min(per_page, _MAX_PER_PAGE)
    while True:
        data = await _request(
            cfg, path,
            params={**(params or {}), "page": page, "per_page": per_page},
            limiter=limiter,
        )
        results.extend(data.get(key, []))
        if data.get("next_page") is None:
            break
        page += 1
    return results


async def _get_one(
    cfg: Settings, path: str, *, limiter: HarvestRateLimiter | None = None
) -> dict[str, Any]:
    return await _request(cfg, path, limiter=limiter)


# ── Reads: clients & projects ───────────────────────────────────────────────


async def get_clients(cfg: Settings) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/clients", "clients", {"is_active": "true"})


async def get_client(cfg: Settings, client_id: int) -> dict[str, Any]:
    return await _get_one(cfg, f"/clients/{client_id}")


async def get_project(cfg: Settings, project_id: int) -> dict[str, Any]:
    return await _get_one(cfg, f"/projects/{project_id}")


async def get_active_projects(cfg: Settings) -> list[dict[str, Any]]:
    return await _get_all(cfg, "/projects", "projects", {"is_active": "true"})


async def list_projects_detailed(
    cfg: Settings, *, is_active: bool | None = True
) -> list[dict[str, Any]]:
    """Projects with the full field set the billing snapshot needs.

    Harvest returns everything on the list endpoint already — this exists as a
    distinct name because `get_active_projects` is contracted to the rev-rec
    path and shouldn't grow a filter argument.
    """
    params: dict[str, Any] = {}
    if is_active is not None:
        params["is_active"] = "true" if is_active else "false"
    return await _get_all(cfg, "/projects", "projects", params)


async def get_task_assignments(cfg: Settings, project_id: int) -> list[dict[str, Any]]:
    """Task assignments for a project — the last rung of the rate-resolution
    ladder (billable_rate → project hourly_rate → task assignment rate)."""
    return await _get_all(
        cfg, f"/projects/{project_id}/task_assignments", "task_assignments"
    )


async def get_invoice_item_categories(cfg: Settings) -> list[dict[str, Any]]:
    """Valid `kind` values for free-form invoice line items.

    Validated against in pre-flight so an invalid category surfaces there
    rather than as a 422 mid-execution.
    """
    return await _get_all(
        cfg, "/invoice_item_categories", "invoice_item_categories"
    )


# ── Reads: time & expenses ──────────────────────────────────────────────────


async def get_time_entries(cfg: Settings, project_id: int, to_date: str) -> float:
    """Total hours logged for a project up to and including to_date.

    Contracted to the revenue-recognition path (`trigger_revenue_recognition`).
    Do not change the signature or return shape — use `list_time_entries` for
    anything that needs the raw entries.
    """
    entries = await _get_all(
        cfg,
        "/time_entries",
        "time_entries",
        {"project_id": project_id, "to": to_date},
    )
    return round(sum(float(e.get("hours", 0)) for e in entries), 4)


async def list_time_entries(
    cfg: Settings, *, project_id: int, from_: str, to: str
) -> list[dict[str, Any]]:
    """Raw time entries for a project within [from_, to].

    `is_billed` is deliberately not passed as a query parameter — v2's support
    for it is unverified (PRD §4.3), so callers filter client-side on the
    `is_billed` field instead.
    """
    return await _get_all(
        cfg,
        "/time_entries",
        "time_entries",
        {"project_id": project_id, "from": from_, "to": to},
    )


async def list_time_entries_all(
    cfg: Settings, *, from_: str, to: str
) -> list[dict[str, Any]]:
    """Every time entry in [from_, to] across the whole account, in one sweep.

    Harvest has no bulk "entries for these N projects" filter, so asking
    per-project costs one paginated query per project. When the caller needs a
    wide slice — reconciling every unmapped project, say — one account-wide
    sweep grouped client-side is dramatically cheaper: tens of pages instead of
    hundreds of separate paginated queries against the same rate-limit bucket.

    Use `list_time_entries` when you want a handful of known projects.

    Pages at the API maximum rather than the default 100: the whole point is to
    spend as few requests as possible, and at 100/page a busy account's quarter
    runs to ~90 requests against a bucket that allows 100 per 15 seconds.
    """
    return await _get_all(
        cfg, "/time_entries", "time_entries", {"from": from_, "to": to},
        per_page=_MAX_PER_PAGE,
    )


async def list_expenses(
    cfg: Settings, *, project_id: int, from_: str, to: str
) -> list[dict[str, Any]]:
    return await _get_all(
        cfg,
        "/expenses",
        "expenses",
        {"project_id": project_id, "from": from_, "to": to},
    )


# ── Reads: invoices ─────────────────────────────────────────────────────────


async def list_invoices(
    cfg: Settings, *, client_id: int, from_: str, to: str
) -> list[dict[str, Any]]:
    """Invoices for a client whose issue_date falls within [from_, to].

    Used by the duplicate guard. Note the guard must cross-reference the local
    ledger before flagging: a client with more than one billing group will
    legitimately have several invoices in this window.
    """
    return await _get_all(
        cfg,
        "/invoices",
        "invoices",
        {"client_id": client_id, "from": from_, "to": to},
    )


# ── Write: invoice creation (the only write in this module) ──────────────────


async def create_invoice(cfg: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Create a **draft** invoice. Returns Harvest's created invoice object.

    The only call in this system that writes to Harvest. A created invoice is a
    draft: Harvest does not notify the client until someone sends it, and sending
    is a banned endpoint (see the module guardrail). Nothing here transitions an
    invoice's state.

    Callers must treat any exception other than `HarvestValidationError` (and the
    other 4xx types) as *unknown outcome* rather than failure — a timeout means
    the invoice may exist. `_post` retries 429 and nothing else, so reaching this
    function's caller with an exception never implies "no invoice was created".
    """
    invoice = await _post(cfg, "/invoices", payload)
    logger.info(
        "created Harvest draft invoice id=%s number=%s amount=%s",
        invoice.get("id"), invoice.get("number"), invoice.get("amount"),
    )
    return invoice


async def get_invoice_totals_by_project(
    cfg: Settings, to_date: str
) -> dict[int, dict[str, Any]]:
    """Return invoice totals keyed by Harvest project ID, for invoices issued on
    or before to_date. Used by the rev rec chain to compute T&M / MSF / Hosting
    revenue from already-invoiced amounts. Each value:
    `{"total_amount": float, "billable_expenses": float}`.
    """
    invoices = await _get_all(cfg, "/invoices", "invoices")
    totals: dict[int, dict[str, Any]] = {}

    for invoice in invoices:
        issue_date = invoice.get("issue_date", "")
        if not issue_date or issue_date > to_date:
            continue

        for item in invoice.get("line_items", []):
            project = item.get("project")
            if not project:
                continue
            pid = int(project["id"])
            if pid not in totals:
                totals[pid] = {"total_amount": 0.0, "billable_expenses": 0.0}
            amount = float(item.get("amount") or 0)
            totals[pid]["total_amount"] = round(totals[pid]["total_amount"] + amount, 2)
            if str(item.get("kind", "")).lower() == "expense":
                totals[pid]["billable_expenses"] = round(
                    totals[pid]["billable_expenses"] + amount, 2
                )

    return totals
