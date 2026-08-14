"""Harvest clients that are not clients.

Our own company is a Harvest client. So, potentially, is a defunct test account
or a partner entity that never bills. Excluding one is a statement about the
account — "this is not someone we deliver to" — so it holds account-wide rather
than per-screen.

Two things live here:

  `not_excluded_sql()`  the predicate every reader applies. A SQL fragment
                        rather than a Python filter, because callers are raw
                        asyncpg queries that need the exclusion inside the
                        WHERE clause (a post-filter would break their LIMITs
                        and their counts).
  CRUD                  operator-initiated, audited, human-only (ADR-0004).

Deliberately *not* applied in `app/services/billing/catalog.py`. Those queries
populate the billing-group form, which must still be able to render and edit a
group that already exists — filtering the options out from under a saved group
would make it uneditable. Exclusion hides a client from the roster and from
reconciliation; it does not retroactively invalidate config someone built.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.orchestrator import events
from app.services import audit


def not_excluded_sql(alias: str = "p", column: str = "client_id") -> str:
    """SQL predicate: this row's client is not excluded.

    Takes no bind parameters, so it composes into any WHERE clause without
    disturbing a caller's `$n` numbering — which matters, because the callers
    build their placeholders positionally.

    `NOT EXISTS` rather than `NOT IN`: the latter evaluates to NULL (and so
    drops every row) if the subquery ever yields a NULL. It cannot today, and
    a query that silently returns nothing the day it can is not worth the
    saving.
    """
    return (
        f"NOT EXISTS (SELECT 1 FROM excluded_harvest_clients x "
        f"WHERE x.harvest_client_id = {alias}.{column})"
    )


async def list_exclusions(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Excluded clients, with the Harvest name where the cache still has one.

    LEFT JOIN, not JOIN: an exclusion outlives the snapshot row it names. If a
    client is deleted in Harvest, or the cache is truncated before a resync, the
    exclusion is still the operator's standing instruction and must not vanish
    from the screen where they would go to undo it.
    """
    rows = await pool.fetch(
        """
        SELECT x.harvest_client_id, x.reason, x.excluded_at, x.excluded_by,
               c.name AS client_name,
               (SELECT count(*) FROM harvest_projects p
                 WHERE p.client_id = x.harvest_client_id) AS project_count
        FROM excluded_harvest_clients x
        LEFT JOIN harvest_clients c ON c.harvest_id = x.harvest_client_id
        ORDER BY coalesce(c.name, x.harvest_client_id::text)
        """
    )
    return [dict(r) for r in rows]


async def is_excluded(pool: asyncpg.Pool, harvest_client_id: int) -> bool:
    return bool(
        await pool.fetchval(
            "SELECT 1 FROM excluded_harvest_clients WHERE harvest_client_id = $1",
            harvest_client_id,
        )
    )


async def add_exclusion(
    pool: asyncpg.Pool,
    harvest_client_id: int,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Exclude a client. Idempotent — re-excluding refreshes the reason.

    No check that the client exists in the snapshot cache. Excluding a client
    the cache has not seen yet is legitimate (a resync may be pending), and
    refusing would make the outcome depend on sync timing.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO excluded_harvest_clients
                    (harvest_client_id, reason, excluded_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (harvest_client_id) DO UPDATE SET
                    reason = EXCLUDED.reason,
                    excluded_by = EXCLUDED.excluded_by,
                    excluded_at = now()
                """,
                harvest_client_id, reason, actor,
            )
            await audit.write_audit_event(
                conn,
                events.CLIENT_EXCLUDED,
                actor=actor,
                payload={"harvest_client_id": harvest_client_id, "reason": reason},
            )

    return {"harvest_client_id": harvest_client_id, "reason": reason}


async def remove_exclusion(
    pool: asyncpg.Pool, harvest_client_id: int, *, actor: str = "system"
) -> bool:
    """Stop excluding a client. Returns False if it was not excluded.

    The caller turns that into a 404 rather than reporting a no-op as success —
    un-excluding something that was never excluded usually means a wrong id.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.fetchval(
                "DELETE FROM excluded_harvest_clients WHERE harvest_client_id = $1 "
                "RETURNING harvest_client_id",
                harvest_client_id,
            )
            if deleted is None:
                return False
            await audit.write_audit_event(
                conn,
                events.CLIENT_EXCLUSION_REMOVED,
                actor=actor,
                payload={"harvest_client_id": harvest_client_id},
            )
    return True
