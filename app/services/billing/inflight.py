"""Resolving an in-flight ledger row. Human-only.

An `in_flight` row means a POST to Harvest never returned a verdict, so the
system does not know whether the invoice exists. PRD §8 is explicit that this
escalates to a person rather than being guessed at: no retry, no inference, no
timeout-means-failure. The row stays in flight, which locks the draw (or group)
out of billing, until a human looks at Harvest and says which happened.

Two resolutions, and both are statements of fact about the outside world:

    link    — an invoice was created; here is its id
    failed  — nothing was created

Item-level rather than draw-level on purpose. `in_flight` is a property of the
ledger, and the monthly run will produce these rows too; when it does, it reuses
this verbatim.

Kept out of `review.py`, which owns *pre-flight* approval state. Approving a
planned group and settling a half-finished write are different questions asked at
different times, and the one rule they would share — "a human decides" — is not
enough to justify one module.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.billing.errors import BillingConfigError

RESOLUTIONS = ("link", "failed")


class InFlightError(BillingConfigError):
    """The row cannot be resolved as asked. Surfaces as 409."""


async def resolve_item(
    pool: asyncpg.Pool,
    run_id: UUID,
    item_id: UUID,
    *,
    resolution: str,
    harvest_invoice_id: int | None = None,
    harvest_invoice_number: str | None = None,
    actual_amount: float | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    """Settle one in-flight row.

    `link` requires `harvest_invoice_id` — the operator read it off the invoice in
    Harvest. It is deliberately not verified against the API: PRD §8 sends
    ambiguity to a human, and a system that second-guesses the human it just asked
    has not resolved anything. A wrong id is visible and correctable; a system that
    silently rejected it would leave the draw locked with no explanation.

    `actual_amount` stays null when the operator does not supply it, and so does
    `variance`. Defaulting it to `planned_amount` would manufacture a variance of
    exactly zero — the one value that looks like a verified match.
    """
    if resolution not in RESOLUTIONS:
        raise InFlightError(
            f"Unknown resolution '{resolution}'. Expected one of: "
            f"{', '.join(RESOLUTIONS)}."
        )
    if resolution == "link" and harvest_invoice_id is None:
        raise InFlightError(
            "Linking requires the Harvest invoice id. Open the invoice in Harvest "
            "and copy its id — or, if no invoice was created, resolve as 'failed'."
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                """
                SELECT bri.id, bri.status, bri.planned_amount, bri.billing_group_id,
                       bri.fixed_fee_schedule_item_id, g.name AS billing_group_name
                FROM billing_run_items bri
                LEFT JOIN billing_groups g ON g.id = bri.billing_group_id
                WHERE bri.id = $1 AND bri.billing_run_id = $2
                FOR UPDATE OF bri
                """,
                item_id, run_id,
            )
            if item is None:
                return {}
            if item["status"] != "in_flight":
                raise InFlightError(
                    f"This row is '{item['status']}', not 'in_flight'. Only a row "
                    "whose Harvest write never returned can be resolved."
                )

            draw_id = item["fixed_fee_schedule_item_id"]

            if resolution == "link":
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'created'::billing_run_item_status,
                        harvest_invoice_id = $2,
                        harvest_invoice_number = $3,
                        actual_amount = $4,
                        variance = CASE WHEN $4::numeric IS NULL THEN NULL
                                        ELSE $4::numeric - planned_amount END
                    WHERE id = $1
                    """,
                    item_id, harvest_invoice_id,
                    harvest_invoice_number or "", actual_amount,
                )
                if draw_id is not None:
                    await conn.execute(
                        "UPDATE fixed_fee_schedule_items SET invoiced_run_id = $2 "
                        "WHERE id = $1",
                        draw_id, run_id,
                    )
                await conn.execute(
                    "UPDATE billing_runs SET status = 'completed', completed_at = now() "
                    "WHERE id = $1",
                    run_id,
                )
                event = events.BILLING_INVOICE_RESOLVED_LINKED
            else:
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'failed'::billing_run_item_status,
                        error_message = $2
                    WHERE id = $1
                    """,
                    item_id,
                    f"Resolved by {actor}: no invoice was created in Harvest.",
                )
                # The draw is untouched — `invoiced_run_id` was never set — so
                # dropping the live row returns it to `ready` by derivation.
                await conn.execute(
                    "UPDATE billing_runs SET status = 'failed' WHERE id = $1", run_id
                )
                event = events.BILLING_INVOICE_RESOLVED_FAILED

            await audit.write_audit_event(
                conn,
                event,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group_id": str(item["billing_group_id"]),
                    "billing_group": item["billing_group_name"],
                    "fixed_fee_schedule_item_id": str(draw_id) if draw_id else None,
                    "resolution": resolution,
                    "harvest_invoice_id": harvest_invoice_id,
                    "actual_amount": actual_amount,
                },
            )

    return {
        "billing_run_id": run_id,
        "billing_run_item_id": item_id,
        "resolution": resolution,
        "status": "created" if resolution == "link" else "failed",
        "fixed_fee_schedule_item_id": draw_id,
        "harvest_invoice_id": harvest_invoice_id,
    }


async def list_unresolved(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Every in-flight row, newest first. The queue for the resolution UI."""
    rows = await pool.fetch(
        """
        SELECT bri.id AS billing_run_item_id, bri.billing_run_id,
               bri.planned_amount, bri.issue_date, bri.created_at,
               bri.billing_group_id, g.name AS billing_group_name,
               g.harvest_client_name, bri.fixed_fee_schedule_item_id,
               d.description AS draw_description
        FROM billing_run_items bri
        LEFT JOIN billing_groups g ON g.id = bri.billing_group_id
        LEFT JOIN fixed_fee_schedule_items d
               ON d.id = bri.fixed_fee_schedule_item_id
        WHERE bri.status = 'in_flight'
        ORDER BY bri.created_at DESC
        """
    )
    return [dict(r) for r in rows]
