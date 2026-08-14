"""Harvest snapshot — read-through cache of the account.

Fetches clients, projects, invoice item categories, and task assignments into
local tables. Idempotent: every write is an upsert keyed on the Harvest id, so
re-running is always safe.

Task assignments are cached here rather than fetched during planning because
they cost one request per project. Doing that per run would put ~60 requests
on the general rate-limit bucket every time someone presses Plan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import asyncpg

from app.config import Settings
from app.integrations import harvest
from app.orchestrator import events
from app.services import audit

logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    """Harvest's date-only strings ("2025-02-03") to a `date`.

    asyncpg will not coerce a string into a `date` column, so this is load-
    bearing rather than tidiness. Unparseable values become null: a date we
    cannot read is a date we do not have.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


async def _upsert_clients(conn: asyncpg.Connection, clients: list[dict]) -> int:
    for c in clients:
        await conn.execute(
            """
            INSERT INTO harvest_clients (harvest_id, name, currency, is_active, synced_at)
            VALUES ($1, $2, $3, $4, now())
            ON CONFLICT (harvest_id) DO UPDATE SET
                name = EXCLUDED.name,
                currency = EXCLUDED.currency,
                is_active = EXCLUDED.is_active,
                synced_at = now()
            """,
            int(c["id"]), c.get("name") or "", c.get("currency"),
            bool(c.get("is_active", True)),
        )
    return len(clients)


async def _upsert_projects(conn: asyncpg.Connection, projects: list[dict]) -> int:
    for p in projects:
        client = p.get("client") or {}
        await conn.execute(
            """
            INSERT INTO harvest_projects (
                harvest_id, name, code, client_id, client_name, client_currency,
                is_billable, is_fixed_fee, bill_by, hourly_rate, fee, budget,
                budget_by, budget_is_monthly, is_active, starts_on, ends_on,
                synced_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17, now())
            ON CONFLICT (harvest_id) DO UPDATE SET
                name = EXCLUDED.name,
                code = EXCLUDED.code,
                client_id = EXCLUDED.client_id,
                client_name = EXCLUDED.client_name,
                client_currency = EXCLUDED.client_currency,
                is_billable = EXCLUDED.is_billable,
                is_fixed_fee = EXCLUDED.is_fixed_fee,
                bill_by = EXCLUDED.bill_by,
                hourly_rate = EXCLUDED.hourly_rate,
                fee = EXCLUDED.fee,
                budget = EXCLUDED.budget,
                budget_by = EXCLUDED.budget_by,
                budget_is_monthly = EXCLUDED.budget_is_monthly,
                is_active = EXCLUDED.is_active,
                starts_on = EXCLUDED.starts_on,
                ends_on = EXCLUDED.ends_on,
                synced_at = now()
            """,
            int(p["id"]), p.get("name") or "", p.get("code"),
            int(client.get("id") or 0), client.get("name"), client.get("currency"),
            bool(p.get("is_billable", False)), bool(p.get("is_fixed_fee", False)),
            p.get("bill_by"), _num(p.get("hourly_rate")), _num(p.get("fee")),
            _num(p.get("budget")), p.get("budget_by"), p.get("budget_is_monthly"),
            bool(p.get("is_active", True)),
            _date(p.get("starts_on")), _date(p.get("ends_on")),
        )
    return len(projects)


async def _upsert_categories(conn: asyncpg.Connection, categories: list[dict]) -> int:
    for c in categories:
        await conn.execute(
            """
            INSERT INTO harvest_invoice_item_categories (harvest_id, name, synced_at)
            VALUES ($1, $2, now())
            ON CONFLICT (harvest_id) DO UPDATE SET
                name = EXCLUDED.name, synced_at = now()
            """,
            int(c["id"]), c.get("name") or "",
        )
    return len(categories)


async def _upsert_task_assignments(
    conn: asyncpg.Connection, project_id: int, assignments: list[dict]
) -> int:
    for a in assignments:
        task = a.get("task") or {}
        await conn.execute(
            """
            INSERT INTO harvest_task_assignments (
                harvest_id, harvest_project_id, task_id, task_name,
                hourly_rate, is_active, synced_at
            )
            VALUES ($1,$2,$3,$4,$5,$6, now())
            ON CONFLICT (harvest_id) DO UPDATE SET
                harvest_project_id = EXCLUDED.harvest_project_id,
                task_id = EXCLUDED.task_id,
                task_name = EXCLUDED.task_name,
                hourly_rate = EXCLUDED.hourly_rate,
                is_active = EXCLUDED.is_active,
                synced_at = now()
            """,
            int(a["id"]), project_id, int(task.get("id") or 0), task.get("name"),
            _num(a.get("hourly_rate")), bool(a.get("is_active", True)),
        )
    return len(assignments)


async def refresh_snapshot(
    pool: asyncpg.Pool, cfg: Settings, *, actor: str = "system"
) -> dict[str, Any]:
    """Pull the account into the local cache. Read-only against Harvest."""
    clients, projects, categories = await asyncio.gather(
        harvest.get_clients(cfg),
        harvest.list_projects_detailed(cfg, is_active=None),
        harvest.get_invoice_item_categories(cfg),
    )

    # Only billable, active projects need rate resolution.
    rate_targets = [
        int(p["id"]) for p in projects
        if p.get("is_active", True) and p.get("is_billable", False)
    ]
    assignments_by_project: dict[int, list[dict]] = {}
    for project_id in rate_targets:
        assignments_by_project[project_id] = await harvest.get_task_assignments(
            cfg, project_id
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            n_clients = await _upsert_clients(conn, clients)
            n_projects = await _upsert_projects(conn, projects)
            n_categories = await _upsert_categories(conn, categories)
            n_assignments = 0
            for project_id, assignments in assignments_by_project.items():
                n_assignments += await _upsert_task_assignments(
                    conn, project_id, assignments
                )

            summary = {
                "clients": n_clients,
                "projects": n_projects,
                "invoice_item_categories": n_categories,
                "task_assignments": n_assignments,
            }
            await audit.write_audit_event(
                conn,
                events.BILLING_SNAPSHOT_REFRESHED,
                actor=actor,
                payload=summary,
            )

    logger.info("harvest snapshot refreshed: %s", summary)
    return summary


async def get_snapshot_info(
    pool: asyncpg.Pool, cfg: Settings | None = None
) -> dict[str, Any]:
    """Freshness and counts for the health strip, plus the account web address.

    `harvest_base_uri` rides along here because the UI needs it to link out to an
    invoice and no Harvest endpoint exposes it — `GET /v2/company` has no such
    field. Empty when unset, and the UI then renders no link rather than guessing
    a subdomain and sending someone to a 404.
    """
    row = await pool.fetchrow(
        """
        SELECT
            (SELECT count(*) FROM harvest_clients)  AS clients,
            (SELECT count(*) FROM harvest_projects) AS projects,
            (SELECT max(synced_at) FROM harvest_projects) AS projects_synced_at,
            (SELECT max(synced_at) FROM harvest_clients)  AS clients_synced_at
        """
    )
    categories = await pool.fetch(
        "SELECT name FROM harvest_invoice_item_categories ORDER BY name"
    )
    synced = [t for t in (row["projects_synced_at"], row["clients_synced_at"]) if t]
    return {
        "clients": row["clients"],
        "projects": row["projects"],
        "invoice_item_categories": [c["name"] for c in categories],
        "fetched_at": max(synced) if synced else None,
        "harvest_base_uri": (cfg.harvest_base_uri if cfg else "").rstrip("/"),
    }
