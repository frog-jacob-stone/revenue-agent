"""Account-level billing settings, read from and written to the database.

Named `settings_store` rather than `settings` so it can never be confused with
`app.config.settings`, which is a different thing: deploy configuration read from
the environment. The split is deliberate and worth keeping straight —

    app.config.settings   secrets and deployment identity. A wrong value is a
                          broken deploy. Changing it means a restart.
    billing_settings      copy and preferences a human edits in the UI, audited,
                          effective immediately.

Today there is one setting. The table is key/value so adding the second does not
need a migration, and unknown keys are refused here rather than silently stored —
a typo'd key would otherwise become a row that nothing ever reads.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.billing.errors import BillingConfigError

#: key -> what it is for. The allowlist is the schema.
KNOWN_SETTINGS: dict[str, str] = {
    "default_invoice_notes": (
        "Notes placed on every created invoice unless the billing group overrides "
        "them with its own notes template. Harvest's own default notes never reach "
        "an API-created invoice, so this is the only way they get there."
    ),
}


class UnknownSettingError(BillingConfigError):
    """The key is not one this system recognises."""


async def get_all(pool: asyncpg.Pool) -> dict[str, str]:
    """Every known setting, with an empty string for any that has no row yet.

    Missing and blank deliberately collapse to the same answer. "Never configured"
    and "configured empty" both mean *send no notes*, and a caller forced to tell
    them apart would invent a distinction the operator never made.
    """
    rows = await pool.fetch("SELECT key, value FROM billing_settings")
    stored = {r["key"]: r["value"] for r in rows}
    return {key: stored.get(key, "") for key in KNOWN_SETTINGS}


async def get(conn: Any, key: str) -> str:
    """One setting. Takes a connection so callers already inside a transaction
    (the planner, the draw preview) do not have to acquire a second one."""
    if key not in KNOWN_SETTINGS:
        raise UnknownSettingError(f"Unknown billing setting {key!r}.")
    value = await conn.fetchval(
        "SELECT value FROM billing_settings WHERE key = $1", key
    )
    return value or ""


async def get_default_invoice_notes(conn: Any) -> str:
    """Convenience for the one caller shape that matters, so the key string is
    not repeated at every call site."""
    return await get(conn, "default_invoice_notes")


async def update(
    pool: asyncpg.Pool, values: dict[str, str], *, actor: str = "system"
) -> dict[str, str]:
    """Set one or more settings. Human-only (ADR-0004).

    Every key is validated before anything is written, so a request naming one
    good key and one typo changes nothing rather than half-applying.
    """
    unknown = sorted(set(values) - set(KNOWN_SETTINGS))
    if unknown:
        raise UnknownSettingError(
            f"Unknown billing setting(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(KNOWN_SETTINGS))}."
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            for key, value in values.items():
                await conn.execute(
                    """
                    INSERT INTO billing_settings (key, value, updated_by)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """,
                    key, value or "", actor,
                )
            await audit.write_audit_event(
                conn,
                events.BILLING_SETTINGS_UPDATED,
                actor=actor,
                # The value itself goes in the trail: this is invoice copy a
                # client will read, so "who changed the remit-to details, and to
                # what" is the question worth being able to answer later.
                payload={"settings": {k: (v or "") for k, v in values.items()}},
            )

    return await get_all(pool)
