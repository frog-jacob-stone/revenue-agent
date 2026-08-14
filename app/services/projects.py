"""Project delivery reads.

Reads the Harvest snapshot cache (`harvest_projects`) from the delivery side
rather than the billing side. `app/services/billing/catalog.py` also lists
projects, but for billing configuration — it joins billing-group assignment and
takes an `exclude_group_id` so the group edit form doesn't report a group as
conflicting with itself. Same table, different question.

Read-only, so nothing here writes `audit_log`.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.services.client_exclusions import not_excluded_sql

# Active reads alphabetically — you are scanning for a name you already have in
# mind. Archived reads newest-closed-first, because the useful archived lookup
# is almost always something that ended recently. Nulls sort last either way
# rather than sorting as the empty string.
_ORDER = {
    False: "name",
    True: "ends_on DESC NULLS LAST, name",
}


async def list_projects(
    pool: asyncpg.Pool, *, archived: bool = False
) -> list[dict[str, Any]]:
    """Billable Harvest projects, either running or closed.

    The two lists are disjoint and never merged: a closed engagement is a
    lookup, not something you scan past to see what is running.

    Two exclusions, and they catch different things:

    - Non-billable projects. Internal time buckets — PTO, overhead, sales — are
      Harvest projects but not engagements.
    - Projects of an excluded client (`excluded_harvest_clients`). Our own
      company is a Harvest client, and some of its internal work *is* flagged
      billable, so `is_billable` alone does not catch it.
    """
    rows = await pool.fetch(
        f"""
        SELECT p.harvest_id, p.name, p.client_name, p.starts_on, p.ends_on,
               p.is_active, p.synced_at
        FROM harvest_projects p
        WHERE p.is_billable
          AND p.is_active = $1
          AND {not_excluded_sql()}
        ORDER BY {_ORDER[archived]}
        """,
        not archived,
    )
    return [dict(r) for r in rows]
