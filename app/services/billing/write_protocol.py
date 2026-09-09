"""The two outcome recorders shared by every Harvest write (PRD §8).

Both write paths — one draw from the Draws tab, and a monthly run's many
invoices — take the same three steps:

    transaction A: write the in_flight ledger row, COMMIT   ← the lock, durable
    POST /v2/invoices                                       ← may time out
    transaction B: record the outcome

Transaction A differs between them (a draw mints its run and row; a monthly
item already has both from plan time) and so does the success half of B (a draw
also consumes its schedule item). What does *not* differ is the two ways a write
can end badly, and those are here — because the distinction between them is the
whole safety property and it must not be re-derived per caller.

**A refusal is a verdict.** Harvest looked at the payload and said no. Nothing
was created, so the lock can be released and the thing retried.

**An unknown is not.** A timeout, a 5xx, a dropped connection: the invoice may
exist. `record_unknown` deliberately writes *nothing* to the item's status or
the run's — `in_flight` and `executing` are the accurate answers, and any guess
here is a guess about money. Only a human looking at Harvest can settle it,
through `inflight.resolve_item`.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit

logger = logging.getLogger(__name__)

RESOLVE_REMEDY = (
    "Check Harvest for an invoice matching this client and amount, then resolve "
    "the in-flight row (link it, or mark it failed)."
)


async def settle_run_status(conn: Any, run_id: UUID) -> str:
    """Derive and store a run's status from its items. Returns what was set.

    Shared because several different things finish a run and they must agree:
    the executor reaching the end of its loop, a human resolving the in-flight
    row that stopped it, and a human closing the run. A draw run has one item,
    so for that path this reduces to exactly what it always did — resolve the
    row, finish the run.

    **`executing` while anything is `in_flight` or `approved`.** Both mean work
    outstanding: one invoice nobody can account for, or one nobody has tried
    yet. Calling that run `completed` would be a claim about invoices that do
    not exist.

    **Leftover `planned` rows send the run back to `awaiting_approval`.**
    Drafting is a batch operation over what is approved *right now*, not a
    one-shot the run gets to have once: an operator may reasonably draft two
    groups today, get an answer from a PM about a third tomorrow, and draft that
    one next. Calling the run `completed` after the first batch would lock the
    rest out — `execute` and `review` both refuse a completed run — and would
    claim the month was billed when most of it was not. `awaiting_approval` is
    what the run actually is: back on the pre-flight, with fewer rows left to
    decide. `review.close_run` is how an operator says the remainder is not
    going out, and that is what settles the run for good.
    """
    counts = await conn.fetchrow(
        """
        SELECT count(*) FILTER (WHERE status = 'in_flight') AS in_flight,
               count(*) FILTER (WHERE status = 'approved')  AS approved,
               count(*) FILTER (WHERE status = 'planned')   AS planned,
               count(*) FILTER (WHERE status = 'failed')    AS failed
        FROM billing_run_items WHERE billing_run_id = $1
        """,
        run_id,
    )
    if counts["in_flight"] or counts["approved"]:
        await conn.execute(
            "UPDATE billing_runs SET status = 'executing' WHERE id = $1", run_id
        )
        return "executing"

    if counts["planned"]:
        # Ahead of the `failed` check on purpose: a group that failed can be
        # fixed and re-planned, but a group nobody has decided on yet can still
        # be billed *from this run*, and that possibility is the more urgent
        # thing for the run's status to say.
        await conn.execute(
            "UPDATE billing_runs SET status = 'awaiting_approval' WHERE id = $1",
            run_id,
        )
        return "awaiting_approval"

    if counts["failed"]:
        # A failed run finished too, but `completed_at` reads as "this produced
        # invoices" everywhere it is displayed. Leave it null.
        await conn.execute(
            "UPDATE billing_runs SET status = 'failed' WHERE id = $1", run_id
        )
        return "failed"

    await conn.execute(
        "UPDATE billing_runs SET status = 'completed', completed_at = now() "
        "WHERE id = $1",
        run_id,
    )
    return "completed"


async def record_failure(
    pool: asyncpg.Pool,
    *,
    item_id: UUID,
    run_id: UUID,
    actor: str,
    error: str,
    extra: dict[str, Any] | None = None,
    fail_run: bool = True,
) -> None:
    """Harvest refused. Release the lock so the item can be fixed and retried.

    `fail_run` is False for a monthly run, where one refused group says nothing
    about the others: the loop keeps going and the run's own status is decided
    once, at the end, from what actually happened.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE billing_run_items "
                "SET status = 'failed'::billing_run_item_status, error_message = $2 "
                "WHERE id = $1",
                item_id, error[:2000],
            )
            if fail_run:
                await conn.execute(
                    "UPDATE billing_runs SET status = 'failed' WHERE id = $1", run_id
                )
            await audit.write_audit_event(
                conn,
                events.BILLING_INVOICE_FAILED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "error": error[:2000],
                    **(extra or {}),
                },
            )


async def record_unknown(
    pool: asyncpg.Pool,
    *,
    item_id: UUID,
    run_id: UUID,
    actor: str,
    cause: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Record that we don't know. Touches no status, on purpose — see the
    module docstring."""
    async with pool.acquire() as conn:
        await audit.write_audit_event(
            conn,
            events.BILLING_INVOICE_UNKNOWN,
            actor=actor,
            payload={
                "billing_run_id": str(run_id),
                "billing_run_item_id": str(item_id),
                "cause": cause[:2000],
                "remedy": RESOLVE_REMEDY,
                **(extra or {}),
            },
        )
