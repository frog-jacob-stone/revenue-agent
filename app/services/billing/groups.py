"""Billing-group configuration.

A billing group is the unit that produces exactly one Harvest invoice. Harvest
has no such concept — it lives entirely here.

  - one group  → exactly one Harvest client
  - one group  → one or more Harvest projects
  - one project→ at most one ACTIVE group (enforced by a partial unique index)

A client may own as many groups as it should receive invoices. The uniqueness
rule is on the project, never on the client.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.billing import draws

# Re-exported: callers have always caught `groups.BillingConfigError`, and draw
# validation raised during group CRUD must land in the same net.
from app.services.billing.errors import BillingConfigError

logger = logging.getLogger(__name__)


class ProjectAlreadyGrouped(BillingConfigError):
    """A project is already claimed by another active group."""


_GROUP_COLUMNS = """
    id, name, harvest_client_id, harvest_client_name, billing_type,
    billing_timing, payment_term, custom_net_days, time_summary_type,
    include_expenses, expense_summary_type, attach_receipts, subject_template,
    notes_template, purchase_order, requires_purchase_order, currency,
    is_active, created_at, updated_at
"""

_UPDATABLE = (
    "name", "harvest_client_name", "billing_type", "billing_timing",
    "payment_term", "custom_net_days", "time_summary_type", "include_expenses",
    "expense_summary_type", "attach_receipts", "subject_template",
    "notes_template", "purchase_order", "requires_purchase_order", "currency",
)

# Columns typed as Postgres enums need an explicit cast on parameterized UPDATE.
_ENUM_CASTS = {
    "billing_type": "billing_type",
    "billing_timing": "billing_timing",
    "payment_term": "payment_term",
    "time_summary_type": "time_summary_type",
    "expense_summary_type": "expense_summary_type",
}


# ── Validation ──────────────────────────────────────────────────────────────


async def _validate_projects(
    conn: asyncpg.Connection,
    *,
    harvest_client_id: int,
    project_ids: list[int],
    exclude_group_id: UUID | None = None,
) -> None:
    """Every project must exist in the snapshot and belong to this client.

    A project/client mismatch is exactly the 422 Harvest returns at invoice
    creation. Catching it at config-write time means it can never reach a run.
    """
    if not project_ids:
        return
    if len(set(project_ids)) != len(project_ids):
        raise BillingConfigError("The same project is listed more than once.")

    rows = await conn.fetch(
        "SELECT harvest_id, name, client_id, client_name FROM harvest_projects "
        "WHERE harvest_id = ANY($1::bigint[])",
        project_ids,
    )
    known = {r["harvest_id"]: r for r in rows}

    missing = [p for p in project_ids if p not in known]
    if missing:
        raise BillingConfigError(
            f"Project(s) {missing} are not in the Harvest snapshot. "
            "Refresh the snapshot, or check the project id."
        )

    mismatched = [
        f"{known[p]['name']} (#{p}) belongs to client "
        f"{known[p]['client_name']} (#{known[p]['client_id']})"
        for p in project_ids
        if known[p]["client_id"] != harvest_client_id
    ]
    if mismatched:
        raise BillingConfigError(
            f"Every project must belong to client #{harvest_client_id}. "
            + "; ".join(mismatched)
        )

    # Pre-empt the unique index so the caller gets a useful message rather than
    # a raw constraint violation.
    params: list[Any] = [project_ids]
    clause = ""
    if exclude_group_id is not None:
        params.append(exclude_group_id)
        clause = " AND bgp.billing_group_id <> $2"
    claimed = await conn.fetch(
        f"""
        SELECT bgp.harvest_project_id, bg.name AS group_name
        FROM billing_group_projects bgp
        JOIN billing_groups bg ON bg.id = bgp.billing_group_id
        WHERE bgp.harvest_project_id = ANY($1::bigint[])
          AND bgp.group_is_active{clause}
        """,
        *params,
    )
    if claimed:
        detail = "; ".join(
            f"project #{r['harvest_project_id']} is already in '{r['group_name']}'"
            for r in claimed
        )
        raise ProjectAlreadyGrouped(
            "A project may belong to at most one active billing group. " + detail
        )


# ── Read ────────────────────────────────────────────────────────────────────


async def list_groups(
    pool: asyncpg.Pool,
    *,
    billing_type: str | None = None,
    is_active: bool | None = True,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    if billing_type is not None:
        params.append(billing_type)
        conditions.append(f"billing_type = ${len(params)}::billing_type")
    if is_active is not None:
        params.append(is_active)
        conditions.append(f"is_active = ${len(params)}")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = await pool.fetch(
        f"SELECT {_GROUP_COLUMNS} FROM billing_groups {where} ORDER BY name",
        *params,
    )
    groups = [dict(r) for r in rows]
    if not groups:
        return []

    projects = await pool.fetch(
        """
        SELECT billing_group_id, harvest_project_id, harvest_project_name, sort_order
        FROM billing_group_projects
        WHERE billing_group_id = ANY($1::uuid[])
        ORDER BY sort_order, harvest_project_id
        """,
        [g["id"] for g in groups],
    )
    by_group: dict[UUID, list[dict]] = {}
    for p in projects:
        by_group.setdefault(p["billing_group_id"], []).append(
            {k: v for k, v in dict(p).items() if k != "billing_group_id"}
        )
    for g in groups:
        g["projects"] = by_group.get(g["id"], [])
    return groups


async def get_group(pool: asyncpg.Pool, group_id: UUID) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        f"SELECT {_GROUP_COLUMNS} FROM billing_groups WHERE id = $1", group_id
    )
    if row is None:
        return None
    group = dict(row)
    group["projects"] = [
        dict(r) for r in await pool.fetch(
            """
            SELECT harvest_project_id, harvest_project_name, sort_order
            FROM billing_group_projects WHERE billing_group_id = $1
            ORDER BY sort_order, harvest_project_id
            """,
            group_id,
        )
    ]
    group["schedule_items"] = [
        dict(r) for r in await pool.fetch(
            """
            SELECT d.id, d.harvest_project_id, d.sequence, d.description,
                   d.amount, d.kind, d.scheduled_date, d.released_at,
                   d.released_by, d.invoiced_run_id,
                   live.billing_run_id AS live_run_id
            FROM fixed_fee_schedule_items d
            -- A live ledger row locks the draw's billable fields, so the form
            -- has to know about it before the operator tries to edit them.
            LEFT JOIN LATERAL (
                SELECT bri.billing_run_id FROM billing_run_items bri
                WHERE bri.fixed_fee_schedule_item_id = d.id
                  AND bri.status NOT IN ('failed', 'skipped', 'abandoned')
                LIMIT 1
            ) live ON TRUE
            WHERE d.billing_group_id = $1
            ORDER BY d.sequence
            """,
            group_id,
        )
    ]
    group["recurring_items"] = [
        dict(r) for r in await pool.fetch(
            """
            SELECT id, harvest_project_id, description, quantity, unit_price,
                   kind, is_placeholder, sort_order, effective_from, effective_to
            FROM recurring_line_items WHERE billing_group_id = $1
            ORDER BY sort_order, id
            """,
            group_id,
        )
    ]
    return group


# ── Write ───────────────────────────────────────────────────────────────────


async def _replace_projects(
    conn: asyncpg.Connection, group_id: UUID, projects: list[dict[str, Any]]
) -> None:
    await conn.execute(
        "DELETE FROM billing_group_projects WHERE billing_group_id = $1", group_id
    )
    # As above: list position wins, so `project_ids` order is predictable.
    for order, p in enumerate(projects, start=1):
        pid = int(p["harvest_project_id"])
        name = p.get("harvest_project_name") or await conn.fetchval(
            "SELECT name FROM harvest_projects WHERE harvest_id = $1", pid
        )
        await conn.execute(
            """
            INSERT INTO billing_group_projects
                (billing_group_id, harvest_project_id, harvest_project_name, sort_order)
            VALUES ($1, $2, $3, $4)
            """,
            group_id, pid, name, order,
        )


async def _save_recurring_items(
    conn: asyncpg.Connection, group_id: UUID, items: list[dict[str, Any]]
) -> None:
    """Upsert a group's line items, preserving row identity.

    Was a delete-and-reinsert, on the reasoning that these rows are pure config
    and so their ids carry no meaning. That stopped being true when placeholder
    resolutions arrived: `recurring_line_item_resolutions` keys the operator's
    per-month amount on the line's id, so re-minting ids on every save would
    discard this month's entered amounts as a side effect of editing an
    unrelated fee — silently, and in the direction of under-billing.

    So rows are matched on `id`, rows without one are inserted, and rows in the
    table but absent from the submission are deleted (taking their resolutions
    with them, via `on delete cascade` — removing a fee should remove the
    decisions about it).

    Unlike a draw, nothing here needs locking once billing has begun. A
    resolution is a decision about a month, not an invoice; the ledger row holds
    its own frozen copy of the payload, so a later config edit cannot rewrite
    what was already planned.

    Effective dating still earns its keep for history: a superseded fee keeps
    its row as long as the caller sends it back with an `effective_to` rather
    than dropping it — and now keeps its id too, so its resolution history
    stays attached.
    """
    existing = {
        r["id"] for r in await conn.fetch(
            "SELECT id FROM recurring_line_items WHERE billing_group_id = $1",
            group_id,
        )
    }

    submitted_ids: set[UUID] = set()
    # Position in the submitted list is authoritative. A caller that sends
    # sort_order=0 on every row (or omits it) still gets stable ordering.
    for order, it in enumerate(items, start=1):
        raw_id = it.get("id")
        line_id = UUID(str(raw_id)) if raw_id else None

        if line_id is not None and line_id not in existing:
            # Either a stale id from a concurrent edit, or another group's row.
            # Refusing beats inserting a duplicate under a new id, which would
            # leave the operator with two copies of one fee.
            raise BillingConfigError(
                f"Line item {line_id} does not belong to this billing group. "
                "Reload the group and try again."
            )

        params = (
            int(it["harvest_project_id"]),
            it["description"],
            it.get("quantity", 1),
            0 if it.get("is_placeholder") else it.get("unit_price", 0),
            it.get("kind") or "Service",
            it.get("is_placeholder", False),
            order,
            it.get("effective_from"),
            it.get("effective_to"),
        )

        if line_id is not None:
            submitted_ids.add(line_id)
            await conn.execute(
                """
                UPDATE recurring_line_items
                SET harvest_project_id = $2, description = $3, quantity = $4,
                    unit_price = $5, kind = $6, is_placeholder = $7,
                    sort_order = $8, effective_from = $9, effective_to = $10
                WHERE id = $1
                """,
                line_id, *params,
            )
        else:
            await conn.execute(
                """
                INSERT INTO recurring_line_items
                    (billing_group_id, harvest_project_id, description, quantity,
                     unit_price, kind, is_placeholder, sort_order,
                     effective_from, effective_to)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                group_id, *params,
            )

    removed = [line_id for line_id in existing if line_id not in submitted_ids]
    if removed:
        await conn.execute(
            "DELETE FROM recurring_line_items WHERE id = ANY($1::uuid[])", removed
        )


async def _validate_recurring_items(
    conn: asyncpg.Connection, items: list[dict[str, Any]], project_ids: list[int]
) -> None:
    """Line items must target one of the group's own projects.

    Harvest 422s when a line's project doesn't belong to the invoice client, and
    the group's projects are already client-validated — so membership is the
    whole check.
    """
    if not items:
        return
    allowed = set(project_ids)
    stray = [
        f"'{it.get('description') or 'untitled'}' → project #{it['harvest_project_id']}"
        for it in items
        if int(it["harvest_project_id"]) not in allowed
    ]
    if stray:
        raise BillingConfigError(
            "Every line item must target a project in this billing group. "
            + "; ".join(stray)
        )

    valid_kinds = {
        r["name"] for r in await conn.fetch(
            "SELECT name FROM harvest_invoice_item_categories"
        )
    }
    if valid_kinds:
        bad = sorted({
            it["kind"] for it in items
            if it.get("kind") and it["kind"] not in valid_kinds
        })
        if bad:
            raise BillingConfigError(
                f"Invoice item category {bad} does not exist in this Harvest "
                f"account. Valid: {', '.join(sorted(valid_kinds))}."
            )


async def create_group(
    pool: asyncpg.Pool, data: dict[str, Any], *, actor: str = "system"
) -> dict[str, Any]:
    projects = data.get("projects") or []
    project_ids = [int(p["harvest_project_id"]) for p in projects]
    client_id = int(data["harvest_client_id"])

    async with pool.acquire() as conn:
        async with conn.transaction():
            await _validate_projects(
                conn, harvest_client_id=client_id, project_ids=project_ids
            )
            recurring_items = data.get("recurring_items") or []
            await _validate_recurring_items(conn, recurring_items, project_ids)
            schedule_items = data.get("schedule_items") or []
            await draws.validate_draws(conn, schedule_items, project_ids)
            client_name = data.get("harvest_client_name") or await conn.fetchval(
                "SELECT name FROM harvest_clients WHERE harvest_id = $1", client_id
            )
            row = await conn.fetchrow(
                f"""
                INSERT INTO billing_groups (
                    name, harvest_client_id, harvest_client_name, billing_type,
                    billing_timing, payment_term, custom_net_days,
                    time_summary_type, include_expenses, expense_summary_type,
                    attach_receipts, subject_template, notes_template,
                    purchase_order, requires_purchase_order, currency, is_active
                )
                VALUES (
                    $1,$2,$3,$4::billing_type,$5::billing_timing,$6::payment_term,$7,
                    $8::time_summary_type,$9,$10::expense_summary_type,$11,$12,$13,
                    $14,$15,$16,$17
                )
                RETURNING {_GROUP_COLUMNS}
                """,
                data["name"], client_id, client_name, data["billing_type"],
                data.get("billing_timing", "arrears"),
                data.get("payment_term", "net 30"), data.get("custom_net_days"),
                data.get("time_summary_type"), data.get("include_expenses", False),
                data.get("expense_summary_type"), data.get("attach_receipts", False),
                data.get("subject_template") or "{client_name} — {period_label}",
                data.get("notes_template"), data.get("purchase_order"),
                data.get("requires_purchase_order", False), data.get("currency"),
                data.get("is_active", True),
            )
            await _replace_projects(conn, row["id"], projects)
            if recurring_items:
                await _save_recurring_items(conn, row["id"], recurring_items)
            if schedule_items:
                await draws.save_draws(conn, row["id"], schedule_items)
            await audit.write_audit_event(
                conn,
                events.BILLING_GROUP_CREATED,
                actor=actor,
                payload={
                    "billing_group_id": str(row["id"]),
                    "name": row["name"],
                    "harvest_client_id": client_id,
                    "project_ids": project_ids,
                },
            )
    return await get_group(pool, row["id"])


async def update_group(
    pool: asyncpg.Pool, group_id: UUID, data: dict[str, Any], *, actor: str = "system"
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM billing_groups WHERE id = $1 FOR UPDATE", group_id
            )
            if current is None:
                return None

            client_id = int(data.get("harvest_client_id", current["harvest_client_id"]))
            if "projects" in data:
                project_ids = [int(p["harvest_project_id"]) for p in data["projects"]]
                await _validate_projects(
                    conn,
                    harvest_client_id=client_id,
                    project_ids=project_ids,
                    exclude_group_id=group_id,
                )
            else:
                project_ids = [
                    r["harvest_project_id"] for r in await conn.fetch(
                        "SELECT harvest_project_id FROM billing_group_projects "
                        "WHERE billing_group_id = $1",
                        group_id,
                    )
                ]
            if "recurring_items" in data:
                await _validate_recurring_items(
                    conn, data["recurring_items"] or [], project_ids
                )
            if "schedule_items" in data:
                await draws.validate_draws(
                    conn, data["schedule_items"] or [], project_ids
                )

            sets: list[str] = []
            params: list[Any] = []
            for field in _UPDATABLE:
                if field not in data:
                    continue
                params.append(data[field])
                cast = _ENUM_CASTS.get(field)
                placeholder = f"${len(params)}" + (f"::{cast}" if cast else "")
                sets.append(f"{field} = {placeholder}")
            if "harvest_client_id" in data:
                params.append(client_id)
                sets.append(f"harvest_client_id = ${len(params)}")

            if sets:
                params.append(group_id)
                await conn.execute(
                    f"UPDATE billing_groups SET {', '.join(sets)}, updated_at = now() "
                    f"WHERE id = ${len(params)}",
                    *params,
                )

            if "projects" in data:
                await _replace_projects(conn, group_id, data["projects"])
            if "recurring_items" in data:
                await _save_recurring_items(
                    conn, group_id, data["recurring_items"] or []
                )
            if "schedule_items" in data:
                await draws.save_draws(conn, group_id, data["schedule_items"] or [])

            await audit.write_audit_event(
                conn,
                events.BILLING_GROUP_UPDATED,
                actor=actor,
                payload={
                    "billing_group_id": str(group_id),
                    "fields": sorted(
                        k for k in data
                        if k in _UPDATABLE
                        or k in ("projects", "recurring_items", "schedule_items")
                    ),
                },
            )
    return await get_group(pool, group_id)


async def deactivate_group(
    pool: asyncpg.Pool, group_id: UUID, *, actor: str = "system"
) -> dict[str, Any] | None:
    """Deactivating releases the group's claim on its projects (via trigger),
    so they can be reassigned or flagged as unmapped."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "UPDATE billing_groups SET is_active = false, updated_at = now() "
                "WHERE id = $1 RETURNING id, name",
                group_id,
            )
            if row is None:
                return None
            await audit.write_audit_event(
                conn,
                events.BILLING_GROUP_DEACTIVATED,
                actor=actor,
                payload={"billing_group_id": str(group_id), "name": row["name"]},
            )
    return await get_group(pool, group_id)
