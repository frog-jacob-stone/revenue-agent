"""Monthly-run execution — the many-invoice Harvest write (PRD §8, §3.2).

One click on "Create drafts in Harvest" on the pre-flight screen. Every group
the operator approved *at that moment* gets a draft, one at a time, each with
the full §8 protocol around it. The single-draw path in `draws.invoice_draw` is
the precedent and the shape is deliberately identical; the two outcome
recorders they share live in `write_protocol`.

**A click drafts a batch, not the run.** Approving three of nine groups and
clicking drafts three invoices and leaves the other six exactly where they
were — still planned, still billable from this same run once the operator has
the answer they were waiting on. So this never marks a run `completed` on its
own: `_finalize` derives the status from the ledger, and leftover planned rows
put the run back in `awaiting_approval` for the next batch. `review.close_run`
is the deliberate "nothing more is going out from this run".

**Operator-initiated, no approval row** ([ADR-0004]). The operator has just read
each group's exact POST body on the pre-flight — `planner.get_run` serves the
payload re-dated for today, which is the same body this sends — and the click
is the authorization. Nothing calls this but the router: no scheduler, no
planner, no agent, and it is in no tool's `allowed_tools`.

**The payload is never rebuilt from config here.** It comes off the ledger row
the planner froze, with only the due date moved to the draft day
(`planner.draft_dating_for`). A group edited since planning must not leak into
an invoice nobody reviewed, and — for T&M — the `line_items_import` block is
what makes Harvest mark the underlying time billed. Substituting computed
`line_items` would create the exact failure this pipeline exists to avoid:
a client billed for time Harvest still reports as uninvoiced, ready to be
billed again next month. `verify.count_unbilled_after` checks that afterwards.

**Sequential, and it halts on an unknown.** A 4xx is a verdict about one
payload and says nothing about the next, so the loop continues and the run's
status is decided at the end. A timeout or 5xx is not a verdict: that item's
invoice may exist, and continuing would turn one unresolvable row into a
handful while also, most likely, hammering a Harvest that is already unwell.
So the loop stops, the remaining groups stay `approved` and un-attempted, and
the run stays `executing` — which is the true state. Once a human settles the
in-flight row through `inflight.resolve_item`, clicking again resumes exactly
where it left off.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from app.config import Settings
from app.integrations import harvest
from app.orchestrator import events
from app.services import audit
from app.services.billing import planner, verify, write_protocol
from app.services.billing.errors import BillingConfigError

logger = logging.getLogger(__name__)

# Run states a click may start from. `awaiting_approval` is the first run;
# `executing` is the resume after a halt was resolved. Anything else — planning,
# completed, failed, abandoned — is not a run awaiting a Harvest write.
_STARTABLE_RUN = ("awaiting_approval", "executing")


class RunExecutionError(BillingConfigError):
    """The run was refused before anything was attempted. Nothing changed."""


class RunNotFound(RunExecutionError):
    """No such run. A subclass so a caller that only cares about "refused, and
    nothing happened" can keep catching the base."""


def _today() -> date:
    """Indirection so tests can pin the draft day, matching `draws._today`."""
    return date.today()


async def execute_run(
    pool: asyncpg.Pool,
    cfg: Settings,
    run_id: UUID,
    *,
    actor: str = "system",
) -> dict[str, Any]:
    """Create a Harvest draft invoice for every approved group in one run.

    Returns a per-item report rather than a single verdict — a run is many
    independent writes, and collapsing them would hide a failure among
    successes. Raises `RunExecutionError` if the run cannot be started at all.
    """
    items = await _start(pool, run_id, actor=actor)

    results: list[dict[str, Any]] = []
    halted: dict[str, Any] | None = None

    for row in items:
        outcome = await _execute_item(pool, cfg, run_id, row, actor=actor)
        results.append(outcome)
        if outcome["status"] == "in_flight":
            halted = {
                "message": (
                    f"The POST for “{outcome['billing_group_name']}” did not return "
                    f"a verdict. The invoice may exist in Harvest. This run is "
                    f"stopped until a human resolves it; the remaining groups were "
                    f"not attempted."
                ),
                "billing_run_id": str(run_id),
                "billing_run_item_id": str(outcome["billing_run_item_id"]),
                "remedy": write_protocol.RESOLVE_REMEDY,
            }
            break

    created = [r for r in results if r["status"] == "created"]
    failed = [r for r in results if r["status"] == "failed"]
    status, remaining = await _finalize(
        pool, run_id,
        actor=actor,
        counts={"attempted": len(results), "created": len(created), "failed": len(failed)},
    )

    return {
        "billing_run_id": run_id,
        "status": status,
        "attempted": len(results),
        "created": len(created),
        "failed": len(failed),
        "remaining": remaining,
        "items": results,
        "halted": halted is not None,
        "unknown_item": halted,
    }


# ── Start: the preconditions, and the run-level lock ────────────────────────


async def _start(
    pool: asyncpg.Pool, run_id: UUID, *, actor: str
) -> list[asyncpg.Record]:
    """Check the run can be executed, mark it executing, return its work list.

    The `FOR UPDATE` on the run serializes two clicks arriving together, but it
    is not the real defence — it is released the moment this transaction
    commits, well before the first POST. The real defence is per item: each
    row is re-read `FOR UPDATE` and must still be `approved` to be sent, so a
    second click can at worst find every row already taken.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                "SELECT id, status, kind FROM billing_runs WHERE id = $1 FOR UPDATE",
                run_id,
            )
            if run is None:
                raise RunNotFound("Billing run not found.")
            if run["kind"] != "monthly":
                raise RunExecutionError(
                    "This is a draw run. A draw is billed from the Draws tab, one "
                    "at a time, and never rides a monthly run."
                )
            if run["status"] not in _STARTABLE_RUN:
                raise RunExecutionError(
                    f"Run is {run['status']}; only a run awaiting approval, or one "
                    "resuming after a resolved halt, can create drafts."
                )

            stuck = await conn.fetchval(
                "SELECT count(*) FROM billing_run_items "
                "WHERE billing_run_id = $1 AND status = 'in_flight'",
                run_id,
            )
            if stuck:
                raise RunExecutionError(
                    f"{stuck} invoice(s) from this run are in flight — nobody knows "
                    "whether Harvest created them. Resolve those first; drafting "
                    "again could duplicate a real invoice."
                )

            rows = await conn.fetch(
                """
                SELECT i.id, g.name AS billing_group_name
                FROM billing_run_items i
                JOIN billing_groups g ON g.id = i.billing_group_id
                WHERE i.billing_run_id = $1 AND i.status = 'approved'
                -- Alphabetical, so a partially-executed run resumes in the same
                -- order it started and the report reads like the pre-flight.
                ORDER BY lower(g.name), i.id
                """,
                run_id,
            )
            if not rows:
                raise RunExecutionError(
                    "No approved groups. Approve at least one on the pre-flight "
                    "before creating drafts."
                )

            await conn.execute(
                "UPDATE billing_runs "
                "SET status = 'executing', executed_at = coalesce(executed_at, now()) "
                "WHERE id = $1",
                run_id,
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_RUN_EXECUTION_STARTED,
                actor=actor,
                payload={"billing_run_id": str(run_id), "approved_count": len(rows)},
            )
    return rows


# ── One item: transaction A, the POST, transaction B ────────────────────────


async def _execute_item(
    pool: asyncpg.Pool,
    cfg: Settings,
    run_id: UUID,
    row: asyncpg.Record,
    *,
    actor: str,
) -> dict[str, Any]:
    """Draft one group's invoice. Never raises — the outcome is the return value.

    Raising would be wrong here: a run is a sequence, and the caller has to see
    what happened to item 2 in order to decide about item 3. The one outcome
    that stops the sequence is signalled by `status == "in_flight"`.
    """
    item_id = row["id"]
    group_name = row["billing_group_name"]

    lock = await _take_lock(pool, run_id, item_id, actor=actor)
    if lock is None:
        # Un-approved, rejected, or claimed by a concurrent click between
        # `_start` reading the list and now. Not an error — just not ours.
        logger.info("run %s item %s is no longer approved; skipping", run_id, item_id)
        return {
            "billing_run_item_id": item_id,
            "billing_group_name": group_name,
            "status": "skipped",
            "planned_amount": 0.0,
            "error": "No longer approved when the run reached it.",
        }

    base = {
        "billing_run_item_id": item_id,
        "billing_group_name": group_name,
        "planned_amount": lock["planned_amount"],
        "issue_date": lock["issue_date"],
        "due_date": lock["due_date"],
    }

    try:
        invoice = await harvest.create_invoice(cfg, lock["body"])
    except harvest.HarvestRateLimited as exc:
        # Past the retry cap inside `_post`. A 429 never reached creation, so
        # unlike the failures below this one is genuinely safe to reset.
        error = f"Harvest rate limit exceeded after retries. Nothing was created. {exc}"
        await write_protocol.record_failure(
            pool, item_id=item_id, run_id=run_id, actor=actor,
            error=error, fail_run=False,
        )
        return {**base, "status": "failed", "error": error}
    except (harvest.HarvestValidationError, harvest.HarvestAuthError,
            harvest.HarvestNotFoundError) as exc:
        # A 4xx is a verdict: Harvest looked at the payload and refused.
        error = str(exc.body or exc)
        await write_protocol.record_failure(
            pool, item_id=item_id, run_id=run_id, actor=actor,
            error=error, fail_run=False,
        )
        return {**base, "status": "failed", "error": error}
    except Exception as exc:
        # Timeout, connection error, 5xx, or anything unanticipated. The invoice
        # may exist. Write *nothing* to the item — it stays `in_flight`.
        logger.error("run %s item %s: unknown Harvest write outcome", run_id, item_id)
        await write_protocol.record_unknown(
            pool, item_id=item_id, run_id=run_id, actor=actor, cause=repr(exc),
        )
        return {**base, "status": "in_flight", "error": repr(exc)}

    return {**base, **await _record_created(
        pool, cfg, run_id, item_id, lock, invoice, actor=actor,
    )}


async def _take_lock(
    pool: asyncpg.Pool, run_id: UUID, item_id: UUID, *, actor: str
) -> dict[str, Any] | None:
    """Transaction A: flip `approved → in_flight` and commit, before any POST.

    Sharing one transaction with the POST is the natural way to write this and
    it is wrong: a process death mid-request would roll back the lock, leaving
    an invoice in Harvest that this system has no record of — and the next
    click would create a second one. Committing first means the worst case is a
    row nobody can interpret, which a human can fix, rather than money nobody
    can see.

    The payload is stamped back onto the row with the draft day's due date, so
    that after execution the ledger shows what was sent rather than what the
    plan guessed. Returns None if the row is no longer approved.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                """
                SELECT i.id, i.status, i.planned_amount, i.planned_payload,
                       i.issue_date, i.due_date, i.period_start, i.period_end,
                       g.name AS billing_group_name
                FROM billing_run_items i
                JOIN billing_groups g ON g.id = i.billing_group_id
                WHERE i.billing_run_id = $1 AND i.id = $2
                FOR UPDATE OF i
                """,
                run_id, item_id,
            )
            if item is None or item["status"] != "approved":
                return None

            dated = planner.draft_dating_for(item, draft_date=_today())
            body = dated[2] if dated else item["planned_payload"]
            due_date = dated[1] if dated else item["due_date"]
            amount = float(item["planned_amount"])

            await conn.execute(
                """
                UPDATE billing_run_items
                SET status = 'in_flight'::billing_run_item_status,
                    planned_payload = $2, due_date = $3, updated_at = now()
                WHERE id = $1
                """,
                item_id, body, due_date,
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_INVOICE_ATTEMPTED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": item["billing_group_name"],
                    "planned_amount": amount,
                    "issue_date": item["issue_date"].isoformat() if item["issue_date"] else None,
                    "due_date": due_date.isoformat() if due_date else None,
                },
            )
    # Transaction A has committed here. The lock survives anything below.
    return {
        "body": body,
        "planned_amount": amount,
        "issue_date": item["issue_date"],
        "due_date": due_date,
        "period_start": item["period_start"],
        "period_end": item["period_end"],
        "billing_group_name": item["billing_group_name"],
    }


async def _record_created(
    pool: asyncpg.Pool,
    cfg: Settings,
    run_id: UUID,
    item_id: UUID,
    lock: dict[str, Any],
    invoice: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    """Transaction B, then the post-write check."""
    actual = float(invoice.get("amount") or 0.0)
    planned = lock["planned_amount"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE billing_run_items
                SET status = 'created'::billing_run_item_status,
                    harvest_invoice_id = $2,
                    harvest_invoice_number = $3,
                    actual_amount = $4,
                    variance = $4 - planned_amount,
                    updated_at = now()
                WHERE id = $1
                """,
                item_id, int(invoice["id"]), str(invoice.get("number") or ""), actual,
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_INVOICE_CREATED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": lock["billing_group_name"],
                    "harvest_invoice_id": int(invoice["id"]),
                    "harvest_invoice_number": invoice.get("number"),
                    "planned_amount": planned,
                    "actual_amount": actual,
                    "issue_date": lock["issue_date"].isoformat() if lock["issue_date"] else None,
                    "due_date": lock["due_date"].isoformat() if lock["due_date"] else None,
                },
            )

    hours, entries = await _check_unbilled(pool, cfg, item_id, lock)
    return {
        "status": "created",
        "harvest_invoice_id": int(invoice["id"]),
        "harvest_invoice_number": invoice.get("number"),
        "actual_amount": actual,
        "variance": round(actual - planned, 2),
        "unbilled_hours_after": hours,
        "unbilled_entries_after": entries,
    }


async def _check_unbilled(
    pool: asyncpg.Pool, cfg: Settings, item_id: UUID, lock: dict[str, Any]
) -> tuple[float | None, int | None]:
    """Ask Harvest whether it marked the time billed. Never raises.

    Outside transaction B and after it, deliberately. The invoice exists and is
    recorded by this point; a slow or broken read here must not be able to
    unwind that, so every failure mode ends as a null column and a log line.
    See `verify` for why a non-zero reading is worth showing but not worth
    failing on.
    """
    project_ids = verify.imported_project_ids(lock["body"] or {})
    if not project_ids or not lock["period_start"] or not lock["period_end"]:
        return None, None

    try:
        hours, entries = await verify.count_unbilled_after(
            cfg,
            project_ids=project_ids,
            period_start=lock["period_start"],
            period_end=lock["period_end"],
        )
    except Exception:
        logger.exception("post-write unbilled check failed for item %s", item_id)
        return None, None

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE billing_run_items "
            "SET unbilled_hours_after = $2, unbilled_entries_after = $3 WHERE id = $1",
            item_id, hours, entries,
        )
    if hours:
        logger.warning(
            "item %s: %s billable hrs across %d entries still unbilled in Harvest "
            "after the invoice was created", item_id, hours, entries,
        )
    return hours, entries


# ── Finish ──────────────────────────────────────────────────────────────────


async def _finalize(
    pool: asyncpg.Pool, run_id: UUID, *, actor: str, counts: dict[str, int]
) -> tuple[str, int]:
    """Set the run's status from the ledger, once, at the end.

    Derived by `write_protocol.settle_run_status` rather than tracked through
    the loop, and shared with `inflight.resolve_item` and `review.close_run` —
    the things that can finish a run must not be able to disagree about what
    finished means. A halted run comes back `executing`, which is the honest
    answer while one invoice is unaccounted for, and a run with groups still
    undecided comes back `awaiting_approval`, because a batch is not the run.

    Returns the status alongside the number of groups this run could still
    bill, so the report can say "3 created, 4 left" rather than implying the
    month is done.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            status = await write_protocol.settle_run_status(conn, run_id)
            remaining = await conn.fetchval(
                "SELECT count(*) FROM billing_run_items "
                "WHERE billing_run_id = $1 AND status IN ('planned', 'approved')",
                run_id,
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_RUN_EXECUTED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id), "status": status,
                    "remaining": remaining, **counts,
                },
            )
    return status, remaining
