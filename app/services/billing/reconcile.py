"""Config reconciliation — every billable project maps to exactly one group.

This is the highest-value check in the system. An active billable project that
belongs to no billing group accrues time nobody ever invoices, and nothing else
in the pipeline will notice.

`manual` groups exist solely to make that check survivable. Projects invoiced by
hand map to a `manual` group, which suppresses UNMAPPED_PROJECT for them. Without
it every milestone-billed project raises an error on every run, and error fatigue
is how a real UNMAPPED_PROJECT eventually gets ignored.

Read-only. Callable outside a billing run so config can be fixed without
burning a run.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import asyncpg

from app.config import Settings
from app.integrations import harvest
from app.services.billing import rates
from app.services.billing.harvest_snapshot import get_snapshot_info
from app.services.client_exclusions import not_excluded_sql

logger = logging.getLogger(__name__)

# How far back to price uninvoiced time on *unmapped* projects. Deliberately
# narrower than the estimator's straggler window: this only answers "is there
# work here nobody is invoicing", and it runs account-wide, so a wider window
# costs paginated requests without changing the answer.
#
# A constant rather than a setting. It was never set in any environment, and a
# tuning value that only ever holds its default is easier to find and reason
# about next to the code that reads it.
UNMAPPED_LOOKBACK_DAYS = 90


def _flag(code: str, severity: str, message: str, **context: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "context": context}


async def _unmapped_candidates(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Active billable projects claimed by no active group.

    Projects in a `manual` group are excluded — being manually invoiced is a
    valid answer to "where does this project bill?".

    So are projects of an excluded client. "Where does this bill?" has no answer
    for our own internal work because it never bills, and the question should
    not be asked. This is what the `manual` group named "Frogslayer - Exclusion"
    was standing in for; once a client is excluded here, that group can go.
    """
    rows = await pool.fetch(
        f"""
        SELECT p.harvest_id, p.name, p.client_name, p.client_id, p.is_active
        FROM harvest_projects p
        WHERE p.is_billable
          AND p.is_active
          AND {not_excluded_sql()}
          AND NOT EXISTS (
              SELECT 1 FROM billing_group_projects bgp
              WHERE bgp.harvest_project_id = p.harvest_id
                AND bgp.group_is_active
          )
        ORDER BY p.client_name, p.name
        """
    )
    return [dict(r) for r in rows]


async def _inactive_with_grouping(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Archived projects that still sit in an active group — they may carry
    uninvoiced time that will never be swept up."""
    rows = await pool.fetch(
        """
        SELECT p.harvest_id, p.name, p.client_name
        FROM harvest_projects p
        JOIN billing_group_projects bgp
          ON bgp.harvest_project_id = p.harvest_id AND bgp.group_is_active
        WHERE NOT p.is_active
        ORDER BY p.client_name, p.name
        """
    )
    return [dict(r) for r in rows]


async def _type_mismatches(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """A group's billing_type disagreeing with the project's Harvest setup."""
    rows = await pool.fetch(
        """
        SELECT bg.id AS group_id, bg.name AS group_name, bg.billing_type,
               p.harvest_id, p.name AS project_name, p.is_fixed_fee
        FROM billing_groups bg
        JOIN billing_group_projects bgp ON bgp.billing_group_id = bg.id
        JOIN harvest_projects p ON p.harvest_id = bgp.harvest_project_id
        WHERE bg.is_active
          AND (
                (bg.billing_type = 'time_and_materials' AND p.is_fixed_fee)
             OR (bg.billing_type IN ('fixed_fee_schedule','recurring_monthly')
                 AND NOT p.is_fixed_fee)
          )
        """
    )
    return [dict(r) for r in rows]


async def _client_mismatches(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT bg.id AS group_id, bg.name AS group_name, bg.harvest_client_id,
               p.harvest_id, p.name AS project_name, p.client_id, p.client_name
        FROM billing_groups bg
        JOIN billing_group_projects bgp ON bgp.billing_group_id = bg.id
        JOIN harvest_projects p ON p.harvest_id = bgp.harvest_project_id
        WHERE bg.is_active AND p.client_id <> bg.harvest_client_id
        """
    )
    return [dict(r) for r in rows]


async def _currency_mismatches(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT bg.id AS group_id, bg.name AS group_name,
               array_agg(DISTINCT p.client_currency) AS currencies
        FROM billing_groups bg
        JOIN billing_group_projects bgp ON bgp.billing_group_id = bg.id
        JOIN harvest_projects p ON p.harvest_id = bgp.harvest_project_id
        WHERE bg.is_active AND p.client_currency IS NOT NULL
        GROUP BY bg.id, bg.name
        HAVING count(DISTINCT p.client_currency) > 1
        """
    )
    return [dict(r) for r in rows]


async def _excluded_with_active_group(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Excluded clients that still have active billing config.

    The one genuinely dangerous combination. Exclusion hides a client from the
    Projects roster and from the unmapped check, but it does not stop a run
    invoicing it — so this state means invoices keep going out for a client the
    account has been told is not a client. Silent otherwise: excluding is done
    on a Settings screen that knows nothing about billing groups.
    """
    rows = await pool.fetch(
        """
        SELECT x.harvest_client_id, c.name AS client_name,
               bg.id AS group_id, bg.name AS group_name
        FROM excluded_harvest_clients x
        JOIN billing_groups bg
          ON bg.harvest_client_id = x.harvest_client_id AND bg.is_active
        LEFT JOIN harvest_clients c ON c.harvest_id = x.harvest_client_id
        ORDER BY bg.name
        """
    )
    return [dict(r) for r in rows]


async def _exhausted_schedules(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT bg.id AS group_id, bg.name AS group_name
        FROM billing_groups bg
        WHERE bg.is_active
          AND bg.billing_type = 'fixed_fee_schedule'
          AND EXISTS (SELECT 1 FROM fixed_fee_schedule_items f
                      WHERE f.billing_group_id = bg.id)
          AND NOT EXISTS (SELECT 1 FROM fixed_fee_schedule_items f
                          WHERE f.billing_group_id = bg.id
                            AND f.invoiced_run_id IS NULL)
        """
    )
    return [dict(r) for r in rows]


async def _price_uninvoiced(
    pool: asyncpg.Pool,
    cfg: Settings,
    projects: list[dict[str, Any]],
    *,
    as_of: date,
) -> dict[int, dict[str, float]]:
    """Uninvoiced billable hours and value per unmapped project.

    Deliberately one account-wide sweep rather than a query per project. Before
    any config exists, *every* billable project is unmapped — which is exactly
    when someone first opens this screen — so per-project queries would mean
    hundreds of paginated requests against the general rate-limit bucket on the
    very first page load.

    The window is `UNMAPPED_LOOKBACK_DAYS`, not the straggler lookback:
    the question here is "does this project have uninvoiced work worth
    noticing", which a recent slice answers. Straggler detection on *configured*
    groups is a separate, wider query in the estimator.
    """
    if not projects:
        return {}

    from_ = (as_of - timedelta(days=UNMAPPED_LOOKBACK_DAYS)).isoformat()
    to = as_of.isoformat()

    wanted = {int(p["harvest_id"]) for p in projects}
    ctx = await rates.load_rate_context(pool, sorted(wanted))

    by_project: dict[int, list[dict[str, Any]]] = {pid: [] for pid in wanted}
    for entry in await harvest.list_time_entries_all(cfg, from_=from_, to=to):
        pid = int((entry.get("project") or {}).get("id") or 0)
        if pid in wanted and rates.is_uninvoiced_billable(entry):
            by_project[pid].append(entry)

    out: dict[int, dict[str, float]] = {}
    for pid, entries in by_project.items():
        amount, hours, _unresolved = rates.price_entries(entries, ctx)
        out[pid] = {"uninvoiced_hours": hours, "estimated_value": amount}
    return out


async def reconcile_config(
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    include_time: bool = True,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Full config health report.

    `include_time=False` skips the per-project Harvest queries, giving a fast
    structural-only check.
    """
    as_of = as_of or date.today()

    candidates = await _unmapped_candidates(pool)
    priced: dict[int, dict[str, float]] = {}
    if include_time and candidates:
        priced = await _price_uninvoiced(pool, cfg, candidates, as_of=as_of)

    unmapped: list[dict[str, Any]] = []
    for p in candidates:
        pid = int(p["harvest_id"])
        stats = priced.get(pid, {"uninvoiced_hours": 0.0, "estimated_value": 0.0})
        unmapped.append({
            "harvest_project_id": pid,
            "harvest_project_name": p["name"],
            "harvest_client_name": p["client_name"],
            "is_active": p["is_active"],
            **stats,
        })

    flags: list[dict[str, Any]] = []

    # Only projects actually carrying uninvoiced time are errors — that is
    # money on the floor right now. A billable project in no group with no time
    # is a config gap: nothing is lost yet, but the moment someone logs time to
    # it, it is. Surfaced as a warning so it is visible without competing with
    # the errors.
    for u in unmapped:
        if u["uninvoiced_hours"] > 0:
            flags.append(_flag(
                "UNMAPPED_PROJECT", "error",
                f"{u['harvest_client_name']} — {u['harvest_project_name']} "
                f"(#{u['harvest_project_id']}) has {u['uninvoiced_hours']:g} hrs of "
                f"uninvoiced billable time and belongs to no active billing group.",
                harvest_project_id=u["harvest_project_id"],
                hours=u["uninvoiced_hours"],
                estimated_value=u["estimated_value"],
            ))
        # With include_time=False nothing was priced, so every project reads as
        # zero hours. Claiming "no uninvoiced time" there would be a guess.
        elif include_time:
            flags.append(_flag(
                "UNMAPPED_PROJECT_NO_TIME", "warning",
                f"{u['harvest_client_name']} — {u['harvest_project_name']} "
                f"(#{u['harvest_project_id']}) is billable and belongs to no active "
                f"billing group. No uninvoiced time in the last "
                f"{UNMAPPED_LOOKBACK_DAYS} days, so nothing is missing "
                "from this run — but time logged to it would go uninvoiced.",
                harvest_project_id=u["harvest_project_id"],
                hours=0.0,
                estimated_value=0.0,
                lookback_days=UNMAPPED_LOOKBACK_DAYS,
            ))

    for r in await _client_mismatches(pool):
        flags.append(_flag(
            "PROJECT_CLIENT_MISMATCH", "error",
            f"'{r['group_name']}' bills client #{r['harvest_client_id']}, but "
            f"{r['project_name']} (#{r['harvest_id']}) belongs to "
            f"{r['client_name']} (#{r['client_id']}). This would be a 422.",
            billing_group_id=str(r["group_id"]),
            harvest_project_id=r["harvest_id"],
        ))

    for r in await _currency_mismatches(pool):
        flags.append(_flag(
            "CURRENCY_MISMATCH", "error",
            f"'{r['group_name']}' spans multiple currencies: "
            f"{', '.join(c for c in r['currencies'] if c)}.",
            billing_group_id=str(r["group_id"]),
        ))

    for r in await _type_mismatches(pool):
        expected = "fixed fee" if r["is_fixed_fee"] else "not fixed fee"
        flags.append(_flag(
            "TYPE_MISMATCH", "warning",
            f"'{r['group_name']}' is a {r['billing_type']} group, but Harvest "
            f"project {r['project_name']} (#{r['harvest_id']}) is {expected}.",
            billing_group_id=str(r["group_id"]),
            harvest_project_id=r["harvest_id"],
        ))

    for r in await _inactive_with_grouping(pool):
        flags.append(_flag(
            "INACTIVE_PROJECT_WITH_TIME", "warning",
            f"{r['client_name']} — {r['name']} (#{r['harvest_id']}) is archived "
            f"but still sits in an active billing group.",
            harvest_project_id=r["harvest_id"],
        ))

    for r in await _excluded_with_active_group(pool):
        name = r["client_name"] or f"#{r['harvest_client_id']}"
        flags.append(_flag(
            "EXCLUDED_CLIENT_HAS_ACTIVE_GROUP", "error",
            f"{name} is on the exclusion list but still has the active billing "
            f"group '{r['group_name']}'. Exclusion hides a client from reporting; "
            f"it does not stop it billing, so invoices will still go out. "
            f"Deactivate the group or remove the exclusion.",
            billing_group_id=str(r["group_id"]),
            harvest_client_id=r["harvest_client_id"],
        ))

    for r in await _exhausted_schedules(pool):
        flags.append(_flag(
            "SCHEDULE_EXHAUSTED", "info",
            f"'{r['group_name']}' has consumed every schedule item. "
            "Consider deactivating the group.",
            billing_group_id=str(r["group_id"]),
        ))

    return {
        "unmapped_projects": unmapped,
        "flags": flags,
        "snapshot": await get_snapshot_info(pool, cfg),
        "counts": {
            "error": sum(1 for f in flags if f["severity"] == "error"),
            "warning": sum(1 for f in flags if f["severity"] == "warning"),
            "info": sum(1 for f in flags if f["severity"] == "info"),
        },
    }
