"""Read-only browsing of the Harvest snapshot, for building group config.

The group form needs to answer two questions: which clients exist, and which of
a client's projects are still available to map. Both come from the local cache —
no Harvest call.

Projects carry their current assignment (`billing_group_id` / `billing_group_name`)
rather than being filtered out when claimed. A project already in another group
must be *visible and explained* in the picker; silently omitting it makes the
list look wrong to someone who knows the project exists.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg


async def list_clients(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT c.harvest_id, c.name, c.currency, c.is_active,
               count(p.harvest_id) FILTER (WHERE p.is_billable AND p.is_active)
                   AS billable_project_count
        FROM harvest_clients c
        LEFT JOIN harvest_projects p ON p.client_id = c.harvest_id
        GROUP BY c.harvest_id, c.name, c.currency, c.is_active
        ORDER BY c.name
        """
    )
    return [dict(r) for r in rows]


async def list_projects(
    pool: asyncpg.Pool,
    *,
    client_id: int | None = None,
    include_inactive: bool = False,
    exclude_group_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Snapshot projects with their current billing-group assignment.

    `exclude_group_id` treats the given group's own projects as unassigned, so
    the edit form doesn't report a group as conflicting with itself.
    """
    conditions = ["p.is_billable"]
    params: list[Any] = []

    if not include_inactive:
        conditions.append("p.is_active")
    if client_id is not None:
        params.append(client_id)
        conditions.append(f"p.client_id = ${len(params)}")

    params.append(exclude_group_id)
    exclude_param = f"${len(params)}"

    rows = await pool.fetch(
        f"""
        SELECT p.harvest_id, p.name, p.client_id, p.client_name, p.client_currency,
               p.is_active, p.is_fixed_fee, p.hourly_rate,
               bg.id   AS billing_group_id,
               bg.name AS billing_group_name
        FROM harvest_projects p
        LEFT JOIN billing_group_projects bgp
               ON bgp.harvest_project_id = p.harvest_id
              AND bgp.group_is_active
              AND ({exclude_param}::uuid IS NULL OR bgp.billing_group_id <> {exclude_param}::uuid)
        LEFT JOIN billing_groups bg ON bg.id = bgp.billing_group_id
        WHERE {' AND '.join(conditions)}
        ORDER BY p.client_name, p.name
        """,
        *params,
    )
    return [dict(r) for r in rows]
