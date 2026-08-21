"""Billing run planner — read-only pre-flight.

Produces a reviewable plan and a ledger of `planned` rows. Writes nothing to
Harvest; that happens in Phase 3, behind an approval.

    1. SNAPSHOT   refresh the Harvest cache
    2. RECONCILE  every billable project ↔ exactly one active group
    3. BUILD      per group: dates, estimate, duplicate guard, payload, flags
    4. RENDER     freeze into plan_snapshot; status → awaiting_approval

Groups are processed sequentially. Volume is low and sequential execution keeps
partial-failure state trivial to reason about.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from app.config import Settings
from app.orchestrator import events
from app.services import audit
from app.services.billing import (
    draws,
    duplicate_guard,
    estimator,
    flags,
    harvest_snapshot,
    reconcile,
    recurring,
    settings_store,
)
from app.services.billing import payload as payload_builder
from app.services.billing.dates import (
    month_label,
    normalize_run_month,
    resolve_due_date,
    resolve_period,
)

logger = logging.getLogger(__name__)

# Percentage swing versus the prior month that raises AMOUNT_VARIANCE. A warning
# flag, not a block — it asks a human to look, so the cost of it being slightly
# wrong is one extra glance.
#
# A constant rather than a setting: it was never set in any environment. This is
# the one value here with a plausible reason to change — if 20% turns out to be
# noisy after a few months of real runs, tune it. That is a one-line edit, and if
# it ever needs changing without a deploy it belongs in `billing_settings`
# (migration 0029) alongside the invoice notes, not in the environment.
VARIANCE_PCT_THRESHOLD = 20.0

# Billing types the monthly run can build a payload for.
# `fixed_fee_schedule` is absent on purpose, not for want of implementation:
# draws are billed individually, off-cycle, from the Draws tab. See draws.py.
_PLANNABLE = {"time_and_materials", "recurring_monthly"}


class RunStateError(Exception):
    """The run is not in a state that permits this transition."""


async def _abandon_live_runs(
    conn: asyncpg.Connection, run_month: date, *, actor: str
) -> None:
    """Clear the decks for a re-plan.

    The partial unique index permits one live ledger row per group per month,
    so a prior plan for the same month must be abandoned before a new one can
    be written. `in_flight` rows are deliberately NOT abandoned — those are
    unresolved writes and only a human may clear them.

    Draw rows are excluded. They are billed off-cycle and hold their own index,
    not the month's, so a monthly re-plan has no reason to touch them — and
    sweeping them up would silently discard an invoice the operator had already
    prepared for a delivered milestone.
    """
    rows = await conn.fetch(
        """
        UPDATE billing_run_items
        SET status = 'abandoned', updated_at = now()
        WHERE run_month = $1
          AND status IN ('planned', 'approved')
          AND fixed_fee_schedule_item_id IS NULL
        RETURNING billing_run_id
        """,
        run_month,
    )
    if not rows:
        return
    run_ids = {r["billing_run_id"] for r in rows}
    await conn.execute(
        "UPDATE billing_runs SET status = 'abandoned' "
        "WHERE id = ANY($1::uuid[]) AND status IN ('planning','awaiting_approval')",
        list(run_ids),
    )
    await audit.write_audit_event(
        conn,
        events.BILLING_RUN_ABANDONED,
        actor=actor,
        payload={"run_month": run_month.isoformat(), "run_ids": [str(r) for r in run_ids]},
    )


async def _prior_amount(
    conn: asyncpg.Connection, group_id: UUID, run_month: date
) -> float | None:
    """Last created amount for this group, for the month-over-month check."""
    value = await conn.fetchval(
        """
        SELECT actual_amount FROM billing_run_items
        WHERE billing_group_id = $1 AND run_month < $2 AND status = 'created'
          AND actual_amount IS NOT NULL
        ORDER BY run_month DESC LIMIT 1
        """,
        group_id, run_month,
    )
    return float(value) if value is not None else None


async def _plan_group(
    conn: asyncpg.Connection,
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    group: dict[str, Any],
    run_month: date,
) -> dict[str, Any]:
    """Plan one group. Returns a dict describing the ledger row to write."""
    group_id = group["id"]
    project_ids = [p["harvest_project_id"] for p in group["projects"]]
    billing_type = group["billing_type"]

    # `manual` groups are acknowledged config, not work. No payload, no
    # estimate, no ledger row — their only job is suppressing UNMAPPED_PROJECT.
    if billing_type == "manual":
        return {
            "skip": True,
            "write_row": False,
            "skip_reason": (
                "Manual group. Invoiced by hand — no payload built, no ledger "
                "row written."
            ),
            "group_flags": [],
        }

    # Draw-billed groups are skipped by design, not by omission: a draw is a
    # contract event billed the day delivery is confirmed, so it never rides a
    # monthly run. The run still says what is outstanding, because a delivered
    # milestone nobody bills is exactly the failure this system exists to catch.
    if billing_type == "fixed_fee_schedule":
        return {
            "skip": True,
            "write_row": True,
            "skip_reason": (
                "Draw-billed group. Draws are billed individually from the Draws "
                "tab when delivery is confirmed, not on the monthly run."
            ),
            "group_flags": await draws.group_flags(conn, group_id),
        }

    if billing_type not in _PLANNABLE:
        return {
            "skip": True,
            "write_row": True,
            "skip_reason": (
                f"Billing type '{billing_type}' is not automated yet (PRD Phase 4). "
                "Invoice this group by hand for now."
            ),
            "group_flags": [],
        }

    period = resolve_period(run_month, group["billing_timing"])
    payment_term, due_date = resolve_due_date(
        period.issue_date, group["payment_term"], group["custom_net_days"]
    )
    group_flags: list[flags.Flag] = []

    # Layer 1 of the duplicate guard — two ledger states block planning
    # outright. Both hold the C6 unique index, so the group is skipped with an
    # explanation rather than allowed to collide with the constraint.
    in_flight = await duplicate_guard.find_unresolved_in_flight(conn, group_id)
    if in_flight:
        return {
            "skip": True,
            "write_row": True,
            "skip_reason": (
                "Blocked by an unresolved in-flight row. A human must check "
                "Harvest and resolve it before this group can be planned again."
            ),
            "group_flags": [flags.unresolved_in_flight(
                run_label=month_label(in_flight["run_label"]),
                item_id=str(in_flight["id"]),
                run_id=str(in_flight["billing_run_id"]),
            )],
            "period": period,
            "due_date": due_date,
        }

    created = await duplicate_guard.find_created_this_month(conn, group_id, run_month)
    if created:
        return {
            "skip": True,
            "write_row": True,
            "skip_reason": (
                f"Already invoiced for {month_label(run_month)} "
                f"(invoice #{created['harvest_invoice_number'] or '—'})."
            ),
            "group_flags": [flags.already_invoiced_this_run(
                run_label=month_label(run_month),
                invoice_number=created["harvest_invoice_number"],
            )],
            "period": period,
            "due_date": due_date,
        }

    # ── Type-specific resolution ────────────────────────────────────────────
    # Each branch yields: a total, display line items, payload line data, and
    # its own flags. Everything after this point is common to both.
    subject = payload_builder.render_template(
        group["subject_template"],
        client_name=group["harvest_client_name"] or "",
        period_label=period.label,
    )
    # Account default from `billing_settings` when the group has none. Harvest's
    # own default notes never reach an API-created invoice — see
    # `payload.resolve_notes`.
    notes = payload_builder.resolve_notes(
        group["notes_template"],
        await settings_store.get_default_invoice_notes(conn),
        client_name=group["harvest_client_name"] or "",
        period_label=period.label,
    )

    if billing_type == "recurring_monthly":
        valid_kinds = await recurring.load_valid_kinds(conn)
        res = await recurring.resolve(
            conn,
            billing_group_id=group_id,
            period=period,
            run_month=run_month,
            client_name=group["harvest_client_name"] or "",
            group_project_ids=project_ids,
            valid_kinds=valid_kinds,
        )

        if res.invalid_kinds:
            group_flags.append(flags.invalid_item_category(
                offenders=res.invalid_kinds, valid=sorted(valid_kinds)
            ))
        if res.off_group_projects:
            group_flags.append(flags.line_item_off_group_project(
                offenders=res.off_group_projects
            ))
        if res.placeholders:
            group_flags.append(flags.placeholder_line_items(
                unresolved=res.unresolved_placeholders,
                resolved=res.resolved_placeholders,
                omitted=res.omitted_placeholders,
            ))

        planned_total = res.total
        display_items = res.estimated_line_items
        empty = not res.line_items
        empty_flag = flags.no_recurring_items(period_label=period.label)
        # Two ways to reach empty, and they are not the same news. Nothing in
        # effect is a config question; everything omitted is a decision the
        # operator already made, and saying "no line items in effect" about it
        # would send them looking for a problem that isn't there.
        if res.omitted_placeholders and not res.line_items:
            empty_reason = (
                f"Every line item was omitted for {period.label}. "
                "Skipped rather than creating an empty invoice."
            )
        else:
            empty_reason = (
                f"No recurring line items in effect for {period.label}. "
                "Skipped rather than creating an empty invoice."
            )
        body = payload_builder.build_free_form_payload(
            harvest_client_id=group["harvest_client_id"],
            subject=subject,
            issue_date=period.issue_date,
            payment_term=payment_term,
            due_date=due_date,
            line_items=res.line_items,
            purchase_order=group["purchase_order"],
            notes=notes,
        )
    else:
        est = await estimator.estimate_group(
            pool, cfg,
            project_ids=project_ids,
            period=period,
            time_summary_type=group["time_summary_type"] or "project",
            include_expenses=group["include_expenses"],
            expense_summary_type=group["expense_summary_type"],
        )

        if est.unresolved_rate_entries:
            hours = round(sum(
                float(e.get("hours") or 0) for e in est.unresolved_rate_entries
            ), 2)
            group_flags.append(flags.no_rate_resolved(
                entries=est.unresolved_rate_entries, hours=hours
            ))

        if est.unapproved_entries:
            hours = round(sum(
                float(e.get("hours") or 0) for e in est.unapproved_entries
            ), 2)
            group_flags.append(flags.unapproved_time(
                entries=est.unapproved_entries, hours=hours
            ))

        if est.straggler_hours > 0:
            group_flags.append(flags.straggler_time(
                hours=est.straggler_hours,
                earliest=est.straggler_earliest.isoformat() if est.straggler_earliest else None,
                period_start=period.start.isoformat(),
            ))

        if est.late_hours > 0 and group["billing_timing"] == "arrears":
            group_flags.append(flags.late_time(
                hours=est.late_hours, period_end=period.end.isoformat()
            ))

        planned_total = est.total
        display_items = est.line_items
        empty = est.total <= 0 and not est.line_items
        empty_flag = flags.no_uninvoiced_time(period_label=period.label)
        empty_reason = (
            f"Zero billable uninvoiced time for {period.label}. "
            "Skipped rather than creating a zero invoice."
        )
        body = payload_builder.build_time_and_materials_payload(
            harvest_client_id=group["harvest_client_id"],
            subject=subject,
            issue_date=period.issue_date,
            payment_term=payment_term,
            due_date=due_date,
            project_ids=project_ids,
            period_start=period.start,
            period_end=period.end,
            time_summary_type=group["time_summary_type"] or "project",
            include_expenses=group["include_expenses"],
            expense_summary_type=group["expense_summary_type"],
            purchase_order=group["purchase_order"],
            notes=notes,
        )

    # ── Common flags ────────────────────────────────────────────────────────

    if group["requires_purchase_order"] and not (group["purchase_order"] or "").strip():
        group_flags.append(flags.missing_po())

    # Layer 2: Harvest invoices in the window we have no ledger row for.
    unrecognized = await duplicate_guard.find_unrecognized_harvest_invoices(
        pool, cfg,
        harvest_client_id=group["harvest_client_id"],
        window_start=period.start,
        window_end=period.end,
    )
    if unrecognized:
        group_flags.append(flags.existing_harvest_invoice(invoices=unrecognized))

    prior = await _prior_amount(conn, group_id, run_month)
    if prior and prior > 0:
        pct = ((planned_total - prior) / prior) * 100
        if abs(pct) > VARIANCE_PCT_THRESHOLD:
            group_flags.append(flags.amount_variance(
                planned=planned_total, prior=prior, pct=pct,
                threshold=VARIANCE_PCT_THRESHOLD,
            ))

    # Nothing to bill → skip rather than create an empty invoice.
    if empty:
        group_flags.append(empty_flag)
        return {
            "skip": True,
            "write_row": True,
            "skip_reason": empty_reason,
            "group_flags": group_flags,
            "period": period,
            "due_date": due_date,
            "prior_amount": prior,
        }

    return {
        "skip": False,
        "write_row": True,
        "period": period,
        "due_date": due_date,
        "planned_amount": planned_total,
        "prior_amount": prior,
        "estimated_line_items": display_items,
        "planned_payload": body,
        "group_flags": group_flags,
    }


async def plan_run(
    pool: asyncpg.Pool,
    cfg: Settings,
    *,
    run_month: date | None = None,
    actor: str = "system",
    refresh: bool = True,
) -> UUID:
    """Plan a billing run. Returns the run id. Read-only against Harvest."""
    run_month = normalize_run_month(run_month or date.today())

    if refresh:
        await harvest_snapshot.refresh_snapshot(pool, cfg, actor=actor)

    config_report = await reconcile.reconcile_config(pool, cfg)

    from app.services.billing import groups as groups_service
    active_groups = await groups_service.list_groups(pool, is_active=True)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _abandon_live_runs(conn, run_month, actor=actor)

            run_id = await conn.fetchval(
                "INSERT INTO billing_runs (run_month, status) "
                "VALUES ($1, 'planning') RETURNING id",
                run_month,
            )

            for f in config_report["flags"]:
                await conn.execute(
                    """
                    INSERT INTO billing_run_flags
                        (billing_run_id, code, severity, message, context)
                    VALUES ($1, $2, $3::billing_flag_severity, $4, $5)
                    """,
                    run_id, f["code"], f["severity"], f["message"], f["context"],
                )

            for group in active_groups:
                result = await _plan_group(
                    conn, pool, cfg, group=group, run_month=run_month
                )
                if not result["write_row"]:
                    continue

                period = result.get("period")
                item_id = await conn.fetchval(
                    """
                    INSERT INTO billing_run_items (
                        billing_run_id, billing_group_id, run_month, status,
                        planned_amount, planned_payload, estimated_line_items,
                        prior_amount, issue_date, due_date, period_start,
                        period_end, skip_reason
                    )
                    VALUES ($1,$2,$3,$4::billing_run_item_status,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    RETURNING id
                    """,
                    run_id, group["id"], run_month,
                    "skipped" if result["skip"] else "planned",
                    result.get("planned_amount", 0),
                    result.get("planned_payload", {}),
                    result.get("estimated_line_items", []),
                    result.get("prior_amount"),
                    period.issue_date if period else None,
                    result.get("due_date"),
                    period.start if period else None,
                    period.end if period else None,
                    result.get("skip_reason"),
                )
                for f in result["group_flags"]:
                    await conn.execute(
                        """
                        INSERT INTO billing_run_flags
                            (billing_run_id, billing_run_item_id, code, severity,
                             message, context)
                        VALUES ($1,$2,$3,$4::billing_flag_severity,$5,$6)
                        """,
                        run_id, item_id, f.code, f.severity, f.message, f.context,
                    )

            snapshot = await _build_snapshot(conn, run_id)
            await conn.execute(
                "UPDATE billing_runs SET status = 'awaiting_approval', "
                "plan_snapshot = $2 WHERE id = $1",
                run_id, snapshot,
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_RUN_PLANNED,
                actor=actor,
                payload={
                    "billing_run_id": str(run_id),
                    "run_month": run_month.isoformat(),
                    "planned_count": snapshot["planned_count"],
                    "planned_total": snapshot["planned_total"],
                    "flag_counts": snapshot["flag_counts"],
                },
            )

    logger.info(
        "planned billing run %s for %s: %d invoices, $%.2f",
        run_id, run_month, snapshot["planned_count"], snapshot["planned_total"],
    )
    return run_id


async def abandon_run(
    pool: asyncpg.Pool, run_id: UUID, *, actor: str = "system"
) -> bool:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT status, run_month FROM billing_runs WHERE id = $1 FOR UPDATE",
                run_id,
            )
            if row is None:
                return False
            if row["status"] not in ("planning", "awaiting_approval"):
                raise RunStateError(
                    f"run is {row['status']}; only a planning or awaiting_approval "
                    "run can be abandoned"
                )
            await conn.execute(
                "UPDATE billing_run_items SET status = 'abandoned', updated_at = now() "
                "WHERE billing_run_id = $1 AND status IN ('planned','approved')",
                run_id,
            )
            await conn.execute(
                "UPDATE billing_runs SET status = 'abandoned' WHERE id = $1", run_id
            )
            await audit.write_audit_event(
                conn,
                events.BILLING_RUN_ABANDONED,
                actor=actor,
                payload={"billing_run_id": str(run_id)},
            )
    return True


# ── Read models ─────────────────────────────────────────────────────────────


async def _build_snapshot(conn: asyncpg.Connection, run_id: UUID) -> dict[str, Any]:
    """The frozen pre-flight, persisted so the plan the operator approved can
    always be reconstructed even if config changes underneath it."""
    detail = await _load_run(conn, run_id)
    return {
        "planned_count": detail["planned_count"],
        "skipped_count": detail["skipped_count"],
        "planned_total": detail["planned_total"],
        "flag_counts": detail["flag_counts"],
        "items": [
            {
                "billing_group_id": str(i["billing_group_id"]),
                "billing_group_name": i["billing_group_name"],
                "status": i["status"],
                "planned_amount": float(i["planned_amount"]),
                "planned_payload": i["planned_payload"],
            }
            for i in detail["items"]
        ],
        "run_flags": detail["run_flags"],
    }


async def _load_run(conn: Any, run_id: UUID) -> dict[str, Any] | None:
    run = await conn.fetchrow("SELECT * FROM billing_runs WHERE id = $1", run_id)
    if run is None:
        return None

    item_rows = await conn.fetch(
        """
        SELECT i.*, g.name AS billing_group_name, g.harvest_client_name,
               g.billing_type, g.billing_timing
        FROM billing_run_items i
        JOIN billing_groups g ON g.id = i.billing_group_id
        WHERE i.billing_run_id = $1
        -- Skipped groups sink to the bottom; everything else is alphabetical by
        -- group name. Ordering by amount put the list in a different order
        -- every month, which is the wrong shape for a review you work top to
        -- bottom and compare against last month.
        ORDER BY (i.status = 'skipped'), lower(g.name), i.id
        """,
        run_id,
    )
    flag_rows = await conn.fetch(
        "SELECT * FROM billing_run_flags WHERE billing_run_id = $1 ORDER BY severity, code",
        run_id,
    )

    by_item: dict[UUID, list[dict]] = {}
    run_flags: list[dict] = []
    for f in flag_rows:
        payload = {
            "code": f["code"], "severity": f["severity"],
            "message": f["message"], "context": f["context"],
        }
        if f["billing_run_item_id"] is None:
            run_flags.append(payload)
        else:
            by_item.setdefault(f["billing_run_item_id"], []).append(payload)

    items = []
    for r in item_rows:
        item = dict(r)
        item["flags"] = by_item.get(r["id"], [])
        items.append(item)

    planned = [i for i in items if i["status"] != "skipped"]
    return {
        **dict(run),
        "label": month_label(run["run_month"]),
        "items": items,
        "run_flags": run_flags,
        "planned_count": len(planned),
        "skipped_count": len(items) - len(planned),
        "planned_total": round(sum(float(i["planned_amount"]) for i in planned), 2),
        "flag_counts": flags.counts(
            run_flags + [f for i in items for f in i["flags"]]
        ),
    }


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> dict[str, Any] | None:
    return await _load_run(pool, run_id)


async def list_runs(
    pool: asyncpg.Pool, *, limit: int = 24, kind: str | None = "monthly"
) -> list[dict[str, Any]]:
    """Defaults to monthly runs. Draw runs are single-invoice and frequent, so
    including them by default would bury the monthly history."""
    kind_clause = "WHERE r.kind = $2::billing_run_kind" if kind else ""
    params: list[Any] = [limit] + ([kind] if kind else [])
    rows = await pool.fetch(
        f"""
        SELECT r.*,
               count(i.id) FILTER (WHERE i.status <> 'skipped')      AS planned_count,
               count(i.id) FILTER (WHERE i.status = 'skipped')       AS skipped_count,
               coalesce(sum(i.planned_amount)
                        FILTER (WHERE i.status <> 'skipped'), 0)     AS planned_total,
               coalesce(sum(i.actual_amount), 0)                     AS actual_total
        FROM billing_runs r
        LEFT JOIN billing_run_items i ON i.billing_run_id = r.id
        {kind_clause}
        GROUP BY r.id
        ORDER BY r.created_at DESC
        LIMIT $1
        """,
        *params,
    )
    if not rows:
        return []

    flag_rows = await pool.fetch(
        "SELECT billing_run_id, severity, count(*) AS n FROM billing_run_flags "
        "WHERE billing_run_id = ANY($1::uuid[]) GROUP BY billing_run_id, severity",
        [r["id"] for r in rows],
    )
    counts_by_run: dict[UUID, dict[str, int]] = {}
    for f in flag_rows:
        counts_by_run.setdefault(f["billing_run_id"], {})[f["severity"]] = f["n"]

    out = []
    for r in rows:
        run = dict(r)
        run["label"] = month_label(r["run_month"])
        run["planned_total"] = float(r["planned_total"])
        base = {flags.ERROR: 0, flags.WARNING: 0, flags.INFO: 0}
        run["flag_counts"] = {**base, **counts_by_run.get(r["id"], {})}
        out.append(run)
    return out
