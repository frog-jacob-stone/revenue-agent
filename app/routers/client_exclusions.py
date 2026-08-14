"""Client exclusions router — which Harvest clients are not clients.

Operator-initiated (ADR-0004). Excluding a client is a write, but it needs no
approval row: the operator picks the client from a list in Settings, the exact
payload is what they chose, this endpoint is human-only and in no agent's
`allowed_tools`, and every transition writes `audit_log`.

Not under `/billing` on purpose. An exclusion is account-wide — it hides a
client from the Projects roster as much as from config reconciliation — and
filing it under billing would imply a scope it does not have.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthUser, get_current_user
from app.db import get_pool
from app.models.client_exclusions import ExcludeClientRequest, ExcludedClient
from app.services import client_exclusions

router = APIRouter(prefix="/client-exclusions", tags=["client-exclusions"])


async def _db() -> asyncpg.Pool:
    return await get_pool()


@router.get("", response_model=list[ExcludedClient])
async def list_client_exclusions(pool: asyncpg.Pool = Depends(_db)):
    """Every excluded client, with its Harvest name and project count."""
    rows = await client_exclusions.list_exclusions(pool)
    return [ExcludedClient.model_validate(r) for r in rows]


@router.post("", response_model=list[ExcludedClient], status_code=201)
async def exclude_client(
    body: ExcludeClientRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Exclude a client account-wide. Idempotent — re-posting updates the reason.

    Returns the whole list rather than the one row, so the caller re-renders
    from the server's answer instead of patching local state and hoping.
    """
    await client_exclusions.add_exclusion(
        pool,
        body.harvest_client_id,
        reason=body.reason,
        actor=user.email or str(user.id),
    )
    rows = await client_exclusions.list_exclusions(pool)
    return [ExcludedClient.model_validate(r) for r in rows]


@router.delete("/{harvest_client_id}", response_model=list[ExcludedClient])
async def unexclude_client(
    harvest_client_id: int,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Stop excluding a client. 404 when it was not excluded — a no-op reported
    as success usually means a wrong id."""
    removed = await client_exclusions.remove_exclusion(
        pool, harvest_client_id, actor=user.email or str(user.id)
    )
    if not removed:
        raise HTTPException(status_code=404, detail="Client is not excluded")
    rows = await client_exclusions.list_exclusions(pool)
    return [ExcludedClient.model_validate(r) for r in rows]
