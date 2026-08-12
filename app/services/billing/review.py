"""Per-group approval of a planned run.

The pre-flight screen is where a human decides which invoices are allowed to
exist. That decision is persisted here, on the ledger row itself, so closing
the tab does not throw it away — and so the record of who approved what
survives the run.

Two rules the service layer owns, not the UI:

  - **Nothing is approved by default.** The planner writes `planned`; only an
    explicit human action moves a row to `approved`.
  - **An error-severity flag blocks approval until it is overridden**, and
    flags in `flags.NON_OVERRIDABLE` (today: `UNRESOLVED_IN_FLIGHT`) can never
    be overridden at all — overriding those risks a duplicate invoice, which is
    the failure the whole in-flight protocol exists to prevent.

Approval here is *review state*, not the Unbreakable Rule #1 approval chain.
The Harvest write in Phase 3 still goes through a real `approvals` row; this
selection is what that row will carry.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.billing import flags

logger = logging.getLogger(__name__)

# Run states in which the plan is still under review.
_REVIEWABLE_RUN = ("planning", "awaiting_approval")
# Item states that can move between approved and unapproved.
_REVIEWABLE_ITEM = ("planned", "approved")


class ApprovalError(Exception):
    """The requested approval transition is not permitted."""


async def _blocking_flags(conn: Any, item_id: UUID) -> list[str]:
    """Error-severity flag codes on an item that forbid approval outright."""
    rows = await conn.fetch(
        "SELECT code FROM billing_run_flags "
        "WHERE billing_run_item_id = $1 AND severity = 'error'",
        item_id,
    )
    return [r["code"] for r in rows if r["code"] in flags.NON_OVERRIDABLE]


async def _has_error_flag(conn: Any, item_id: UUID) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM billing_run_flags "
        "WHERE billing_run_item_id = $1 AND severity = 'error' LIMIT 1",
        item_id,
    ))


async def _load_item(conn: Any, run_id: UUID, item_id: UUID) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT i.id, i.status, i.error_override, i.billing_group_id,
               r.status AS run_status, g.name AS billing_group_name
        FROM billing_run_items i
        JOIN billing_runs r ON r.id = i.billing_run_id
        JOIN billing_groups g ON g.id = i.billing_group_id
        WHERE i.billing_run_id = $1 AND i.id = $2
        FOR UPDATE OF i
        """,
        run_id, item_id,
    )


async def set_item_approval(
    pool: asyncpg.Pool,
    run_id: UUID,
    item_id: UUID,
    *,
    approved: bool | None = None,
    override: bool | None = None,
    actor: str = "system",
) -> bool:
    """Approve, un-approve, and/or record an error override for one group.

    Both fields are optional so the two operator gestures — "I accept this
    error" and "I approve this invoice" — can be recorded independently.
    Returns False if the item does not belong to the run.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await _load_item(conn, run_id, item_id)
            if item is None:
                return False
            if item["run_status"] not in _REVIEWABLE_RUN:
                raise ApprovalError(
                    f"run is {item['run_status']}; approval can only change while "
                    "it is under review"
                )
            if item["status"] not in _REVIEWABLE_ITEM:
                raise ApprovalError(
                    f"this group is {item['status']}; only a planned or approved "
                    "group can change approval"
                )

            effective_override = item["error_override"]

            if override is not None and override != effective_override:
                if override:
                    blocking = await _blocking_flags(conn, item_id)
                    if blocking:
                        raise ApprovalError(
                            f"{', '.join(blocking)} cannot be overridden — resolve it "
                            "instead; approving risks a duplicate invoice"
                        )
                await conn.execute(
                    "UPDATE billing_run_items SET error_override = $2, updated_at = now() "
                    "WHERE id = $1",
                    item_id, override,
                )
                effective_override = override
                await audit.write_audit_event(
                    conn,
                    events.BILLING_ITEM_OVERRIDDEN,
                    actor=actor,
                    payload={
                        "billing_run_id": str(run_id),
                        "billing_run_item_id": str(item_id),
                        "billing_group": item["billing_group_name"],
                        "override": override,
                    },
                )

            if approved is None or approved == (item["status"] == "approved"):
                return True

            if approved:
                blocking = await _blocking_flags(conn, item_id)
                if blocking:
                    raise ApprovalError(
                        f"{', '.join(blocking)} blocks approval and is not "
                        "overridable — resolve it first"
                    )
                if await _has_error_flag(conn, item_id) and not effective_override:
                    raise ApprovalError(
                        "this group carries an error-severity flag; record an "
                        "override before approving"
                    )
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'approved', approved_at = now(), approved_by = $2,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    item_id, actor,
                )
            else:
                await conn.execute(
                    """
                    UPDATE billing_run_items
                    SET status = 'planned', approved_at = NULL, approved_by = NULL,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    item_id,
                )

            await audit.write_audit_event(
                conn,
                events.BILLING_ITEM_APPROVED if approved else events.BILLING_ITEM_UNAPPROVED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "billing_run_item_id": str(item_id),
                    "billing_group": item["billing_group_name"],
                    "error_override": effective_override,
                },
            )
    return True


async def set_all_approvals(
    pool: asyncpg.Pool, run_id: UUID, *, approved: bool, actor: str = "system"
) -> int:
    """Bulk approve or clear.

    Approving in bulk touches only the groups that are already approvable — it
    never silently overrides an error flag. Clearing touches everything
    approved. Returns the number of rows changed.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            run_status = await conn.fetchval(
                "SELECT status FROM billing_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if run_status is None:
                return 0
            if run_status not in _REVIEWABLE_RUN:
                raise ApprovalError(
                    f"run is {run_status}; approval can only change while it is "
                    "under review"
                )

            if approved:
                rows = await conn.fetch(
                    """
                    UPDATE billing_run_items i
                    SET status = 'approved', approved_at = now(), approved_by = $2,
                        updated_at = now()
                    WHERE i.billing_run_id = $1
                      AND i.status = 'planned'
                      AND (
                        i.error_override
                        OR NOT EXISTS (
                            SELECT 1 FROM billing_run_flags f
                            WHERE f.billing_run_item_id = i.id AND f.severity = 'error'
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM billing_run_flags f
                        WHERE f.billing_run_item_id = i.id
                          AND f.code = ANY($3::text[])
                      )
                    RETURNING i.id
                    """,
                    run_id, actor, list(flags.NON_OVERRIDABLE),
                )
            else:
                rows = await conn.fetch(
                    """
                    UPDATE billing_run_items
                    SET status = 'planned', approved_at = NULL, approved_by = NULL,
                        updated_at = now()
                    WHERE billing_run_id = $1 AND status = 'approved'
                    RETURNING id
                    """,
                    run_id,
                )

            if not rows:
                return 0
            await audit.write_audit_event(
                conn,
                events.BILLING_ITEM_APPROVED if approved else events.BILLING_ITEM_UNAPPROVED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "bulk": True,
                    "count": len(rows),
                    "billing_run_item_ids": [str(r["id"]) for r in rows],
                },
            )
    return len(rows)
