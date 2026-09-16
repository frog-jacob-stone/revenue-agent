"""Per-project revenue recognition configuration.

How a project recognizes, and — for fixed fee — against what contract value.
Replaces the three hand-typed columns on the Airtable Projects table; the
fourth, `Client Id`, has no successor because `harvest_projects.client_id`
already answers it.

Operator-initiated and human-only (ADR-0004). Nothing here is reachable from an
agent, and every write is audited.

The scope question — *which* projects need configuring — is answered here
rather than in the runner, so the setup screen and the run agree by
construction. A project drifting between the two lists would mean the screen
showing "all configured" while the run refuses to start.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.client_exclusions import not_excluded_sql

#: The enum, mirrored for validation at the service boundary. The database is
#: still the authority — a bad value raises there regardless — but failing here
#: produces "not a revenue type" rather than asyncpg's enum error, and does it
#: before a transaction is opened.
REVENUE_TYPES = (
    "fixed_fee",
    "time_and_materials",
    "msf",
    "hosting",
    "retainer",
)

#: Billable, active, and not an excluded client's. The same three conditions
#: the runner applies, as one fragment so they cannot drift apart.
#:
#: `is_billable` alone is not enough: our own company is a Harvest client and
#: some of its internal work is flagged billable, which is exactly why
#: `excluded_harvest_clients` exists.
IN_SCOPE_SQL = f"p.is_billable AND p.is_active AND {not_excluded_sql()}"


class RevenueConfigError(Exception):
    """The configuration is not valid. Raised before anything is written."""


def _validate(revenue_type: str, contracted_fees: float | None) -> None:
    if revenue_type not in REVENUE_TYPES:
        raise RevenueConfigError(
            f"Unknown revenue type {revenue_type!r}. "
            f"Known: {', '.join(REVENUE_TYPES)}."
        )
    # Mirrors the CHECK. A fixed-fee project with no contract value does not
    # compute to zero — it computes to nonsense, silently, every month.
    if revenue_type == "fixed_fee" and contracted_fees is None:
        raise RevenueConfigError(
            "A fixed-fee project needs contracted fees — percent-complete "
            "recognition has nothing to be a percentage of without them."
        )


async def list_projects(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Every in-scope project, with its configuration if it has one.

    LEFT JOIN from the project, not from the config: the useful screen is "what
    still needs setting up", and an inner join would hide exactly the rows a
    human came to find. `revenue_type IS NULL` is the unconfigured marker.

    Unconfigured first, then by name — the work sorts to the top.
    """
    rows = await pool.fetch(
        f"""
        SELECT p.harvest_id AS harvest_project_id,
               p.name       AS harvest_project_name,
               p.client_name,
               c.revenue_type, c.contracted_fees, c.notes,
               c.updated_at, c.updated_by
        FROM harvest_projects p
        LEFT JOIN revenue_project_config c ON c.harvest_project_id = p.harvest_id
        WHERE {IN_SCOPE_SQL}
        ORDER BY (c.revenue_type IS NOT NULL), p.name
        """
    )
    return [dict(r) for r in rows]


async def unconfigured_projects(conn: Any) -> list[dict[str, Any]]:
    """In-scope projects with no configuration row — the run's gate.

    Takes a connection so the planner can call it inside its transaction.

    Absence is the only case to test for. "Configured but unusable" — a
    fixed-fee project with no contracted fees — cannot exist: the
    `fixed_fee_needs_contracted_fees` CHECK is on the table, so no writer can
    produce it, this service and the backfill included.
    """
    rows = await conn.fetch(
        f"""
        SELECT p.harvest_id AS harvest_project_id,
               p.name       AS harvest_project_name
        FROM harvest_projects p
        LEFT JOIN revenue_project_config c ON c.harvest_project_id = p.harvest_id
        WHERE {IN_SCOPE_SQL}
          AND c.harvest_project_id IS NULL
        ORDER BY p.name
        """
    )
    return [dict(r) for r in rows]


async def set_config(
    pool: asyncpg.Pool,
    harvest_project_id: int,
    *,
    revenue_type: str,
    contracted_fees: float | None = None,
    notes: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Create or update a project's configuration. Idempotent.

    One operation rather than separate create and update paths: the caller is a
    form that is saved, and "has this project been configured before" is not a
    distinction the person filling it in is making.

    No check that the project exists in the snapshot cache. Configuring a
    project the cache has not seen yet is legitimate — a resync may be pending —
    and refusing would make the outcome depend on sync timing. Same reasoning as
    `client_exclusions.add_exclusion`.
    """
    _validate(revenue_type, contracted_fees)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO revenue_project_config (
                    harvest_project_id, revenue_type, contracted_fees, notes,
                    created_by, updated_by
                ) VALUES ($1, $2::revenue_type, $3, $4, $5, $5)
                ON CONFLICT (harvest_project_id) DO UPDATE SET
                    revenue_type    = EXCLUDED.revenue_type,
                    contracted_fees = EXCLUDED.contracted_fees,
                    notes           = EXCLUDED.notes,
                    updated_by      = EXCLUDED.updated_by,
                    updated_at      = now()
                """,
                harvest_project_id, revenue_type, contracted_fees, notes, actor,
            )
            # Read back joined, so the caller gets the same shape
            # `list_projects` returns and the UI can drop it straight into the
            # row it just edited. LEFT JOIN because configuring a project the
            # snapshot has not seen yet is legitimate.
            row = await conn.fetchrow(
                """
                SELECT c.harvest_project_id,
                       coalesce(p.name, '(not in the Harvest snapshot)')
                           AS harvest_project_name,
                       p.client_name,
                       c.revenue_type, c.contracted_fees, c.notes,
                       c.updated_at, c.updated_by
                FROM revenue_project_config c
                LEFT JOIN harvest_projects p ON p.harvest_id = c.harvest_project_id
                WHERE c.harvest_project_id = $1
                """,
                harvest_project_id,
            )
            await audit.write_audit_event(
                conn,
                events.REVENUE_CONFIG_SET,
                actor=actor,
                # The values ride along. A contracted fee is the denominator of
                # every future fixed-fee month, so "who set this, to what, and
                # when" is the question worth answering when a figure is
                # queried a year from now.
                payload={
                    "harvest_project_id": harvest_project_id,
                    "revenue_type": revenue_type,
                    "contracted_fees": (
                        None if contracted_fees is None else str(contracted_fees)
                    ),
                    "notes": notes,
                },
            )
    return dict(row)


async def remove_config(
    pool: asyncpg.Pool, harvest_project_id: int, *, actor: str = "system"
) -> bool:
    """Unconfigure a project. Returns False if it had no configuration.

    The caller turns that into a 404 rather than reporting a no-op as success.

    Past entries are untouched: they snapshot their own `revenue_type`, and a
    month that was recognized stays recognized. This only stops future runs
    from including the project — which they will then refuse to start over,
    by design.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.fetchval(
                "DELETE FROM revenue_project_config WHERE harvest_project_id = $1 "
                "RETURNING harvest_project_id",
                harvest_project_id,
            )
            if deleted is None:
                return False
            await audit.write_audit_event(
                conn,
                events.REVENUE_CONFIG_REMOVED,
                actor=actor,
                payload={"harvest_project_id": harvest_project_id},
            )
    return True
