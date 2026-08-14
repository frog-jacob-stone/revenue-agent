"""Forecast snapshot — the delivery forecast, cached per project.

Mirrors `billing/harvest_snapshot.py` in shape: read-only against the vendor,
idempotent upsert keyed on the project, one audit event per refresh.

It differs in one way worth stating. The Harvest snapshot caches records; this
caches a *derivation* — one date reduced from thousands of assignment rows. So
unlike that cache, a row here can go stale in a way a resync fixes but a partial
read cannot: if a project's assignments are all deleted, the row must be updated
to null rather than left holding yesterday's date. `_prune` is what makes that
true.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import asyncpg

from app.config import Settings
from app.integrations import forecast
from app.orchestrator import events
from app.services import audit

logger = logging.getLogger(__name__)


class ForecastNotConfigured(RuntimeError):
    """No Forecast account id. The caller turns this into a 503 rather than
    reporting a refresh that fetched nothing as success."""


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


async def refresh_forecast_schedule(
    pool: asyncpg.Pool, cfg: Settings, *, actor: str = "system"
) -> dict[str, Any]:
    """Pull each project's last scheduled day into the local cache.

    Read-only against Forecast.
    """
    if not cfg.forecast_account_id:
        raise ForecastNotConfigured(
            "FORECAST_ACCOUNT_ID is not set, so there is no Forecast account to read."
        )

    schedule = await forecast.get_last_scheduled_by_harvest_id(cfg)

    async with pool.acquire() as conn:
        async with conn.transaction():
            for harvest_id, row in schedule.items():
                await conn.execute(
                    """
                    INSERT INTO forecast_project_schedule
                        (harvest_project_id, forecast_project_id,
                         last_scheduled_on, assignment_count, synced_at)
                    VALUES ($1, $2, $3, $4, now())
                    ON CONFLICT (harvest_project_id) DO UPDATE SET
                        forecast_project_id = EXCLUDED.forecast_project_id,
                        last_scheduled_on = EXCLUDED.last_scheduled_on,
                        assignment_count = EXCLUDED.assignment_count,
                        synced_at = now()
                    """,
                    harvest_id,
                    row["forecast_project_id"],
                    _as_date(row["last_scheduled_on"]),
                    row["assignment_count"],
                )

            # A project unlinked from Forecast, or deleted there, would
            # otherwise keep serving the date it had when it vanished.
            pruned = await conn.fetchval(
                """
                WITH deleted AS (
                    DELETE FROM forecast_project_schedule
                    WHERE harvest_project_id <> ALL($1::bigint[])
                    RETURNING 1
                )
                SELECT count(*) FROM deleted
                """,
                list(schedule),
            )

            scheduled = sum(
                1 for r in schedule.values() if r["last_scheduled_on"] is not None
            )
            summary = {
                "projects": len(schedule),
                "with_schedule": scheduled,
                "without_schedule": len(schedule) - scheduled,
                "pruned": pruned,
            }
            await audit.write_audit_event(
                conn,
                events.FORECAST_SCHEDULE_REFRESHED,
                actor=actor,
                payload=summary,
            )

    logger.info("forecast schedule refreshed: %s", summary)
    return summary
