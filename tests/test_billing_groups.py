"""Billing-group CRUD and its validation rules."""
from __future__ import annotations

import pytest

from app.db import get_pool
from app.services.billing import groups as groups_service

ACME = 5735774
NORTHWIND = 5735801


async def _seed_snapshot(pool):
    """Two clients; Acme owns three projects, Northwind one."""
    for cid, name in ((ACME, "Acme Corp"), (NORTHWIND, "Northwind Industrial")):
        await pool.execute(
            "INSERT INTO harvest_clients (harvest_id, name, currency) VALUES ($1,$2,'USD')",
            cid, name,
        )
    projects = [
        (14307913, "Acme Platform", ACME, "Acme Corp"),
        (14307914, "Acme Mobile", ACME, "Acme Corp"),
        (14307915, "Acme Innovation Lab", ACME, "Acme Corp"),
        (14308221, "Northwind Data Platform", NORTHWIND, "Northwind Industrial"),
    ]
    for pid, pname, cid, cname in projects:
        await pool.execute(
            """
            INSERT INTO harvest_projects
                (harvest_id, name, client_id, client_name, client_currency,
                 is_billable, is_active)
            VALUES ($1,$2,$3,$4,'USD',true,true)
            """,
            pid, pname, cid, cname,
        )


def _spec(name: str, client_id: int, project_ids: list[int], **over):
    return {
        "name": name,
        "harvest_client_id": client_id,
        "billing_type": "time_and_materials",
        "projects": [
            {"harvest_project_id": p, "sort_order": i}
            for i, p in enumerate(project_ids, start=1)
        ],
        **over,
    }


async def test_create_group_denormalizes_client_and_project_names():
    pool = await get_pool()
    await _seed_snapshot(pool)

    group = await groups_service.create_group(
        pool, _spec("Acme — Platform + Mobile", ACME, [14307913, 14307914])
    )

    assert group["harvest_client_name"] == "Acme Corp"
    assert [p["harvest_project_name"] for p in group["projects"]] == [
        "Acme Platform", "Acme Mobile",
    ]
    assert group["is_active"] is True


async def test_project_from_another_client_is_rejected():
    """This is the 422 Harvest would return at invoice creation, caught at
    config-write time so it can never reach a run."""
    pool = await get_pool()
    await _seed_snapshot(pool)

    with pytest.raises(groups_service.BillingConfigError, match="must belong to client"):
        await groups_service.create_group(
            pool, _spec("Mixed up", ACME, [14307913, 14308221])
        )


async def test_unknown_project_is_rejected():
    pool = await get_pool()
    await _seed_snapshot(pool)

    with pytest.raises(groups_service.BillingConfigError, match="not in the Harvest snapshot"):
        await groups_service.create_group(pool, _spec("Ghost", ACME, [99999999]))


async def test_duplicate_project_within_one_group_is_rejected():
    pool = await get_pool()
    await _seed_snapshot(pool)

    with pytest.raises(groups_service.BillingConfigError, match="more than once"):
        await groups_service.create_group(
            pool, _spec("Doubled", ACME, [14307913, 14307913])
        )


async def test_project_already_in_another_active_group_is_rejected_with_context():
    pool = await get_pool()
    await _seed_snapshot(pool)
    await groups_service.create_group(pool, _spec("First", ACME, [14307913]))

    with pytest.raises(groups_service.ProjectAlreadyGrouped) as exc:
        await groups_service.create_group(pool, _spec("Second", ACME, [14307913]))

    # The operator needs to know *which* group holds it, not just that one does.
    assert "First" in str(exc.value)


async def test_one_client_can_own_multiple_groups():
    """Acme takes two invoices: T&M for Platform+Mobile, a retainer for the Lab."""
    pool = await get_pool()
    await _seed_snapshot(pool)

    await groups_service.create_group(
        pool, _spec("Acme — Platform + Mobile", ACME, [14307913, 14307914])
    )
    lab = await groups_service.create_group(
        pool,
        _spec(
            "Acme — Innovation Lab", ACME, [14307915],
            billing_type="recurring_monthly", billing_timing="advance",
        ),
    )

    assert lab["billing_timing"] == "advance"
    listed = await groups_service.list_groups(pool)
    acme_groups = [g for g in listed if g["harvest_client_id"] == ACME]
    assert len(acme_groups) == 2


async def test_deactivating_frees_the_project_for_another_group():
    pool = await get_pool()
    await _seed_snapshot(pool)
    first = await groups_service.create_group(pool, _spec("First", ACME, [14307913]))

    await groups_service.deactivate_group(pool, first["id"])

    moved = await groups_service.create_group(pool, _spec("Second", ACME, [14307913]))
    assert moved["projects"][0]["harvest_project_id"] == 14307913


async def test_update_replaces_projects_without_tripping_on_itself():
    """Re-saving a group that keeps one of its own projects must not read as a
    conflict with itself."""
    pool = await get_pool()
    await _seed_snapshot(pool)
    group = await groups_service.create_group(
        pool, _spec("Acme", ACME, [14307913, 14307914])
    )

    updated = await groups_service.update_group(
        pool, group["id"],
        {"projects": [
            {"harvest_project_id": 14307913, "sort_order": 1},
            {"harvest_project_id": 14307915, "sort_order": 2},
        ]},
    )

    assert [p["harvest_project_id"] for p in updated["projects"]] == [14307913, 14307915]


async def test_update_writes_only_supplied_fields():
    pool = await get_pool()
    await _seed_snapshot(pool)
    group = await groups_service.create_group(
        pool, _spec("Acme", ACME, [14307913], purchase_order="PO-1")
    )

    updated = await groups_service.update_group(
        pool, group["id"], {"payment_term": "net 45"}
    )

    assert updated["payment_term"] == "net 45"
    assert updated["purchase_order"] == "PO-1"
    assert updated["name"] == "Acme"


async def test_update_missing_group_returns_none():
    from uuid import uuid4
    pool = await get_pool()
    assert await groups_service.update_group(pool, uuid4(), {"name": "x"}) is None


async def test_group_detail_includes_schedule_and_recurring_items():
    pool = await get_pool()
    await _seed_snapshot(pool)
    group = await groups_service.create_group(
        pool, _spec("Acme Lab", ACME, [14307915], billing_type="recurring_monthly")
    )
    await pool.execute(
        """
        INSERT INTO recurring_line_items
            (billing_group_id, harvest_project_id, description, quantity, unit_price)
        VALUES ($1, 14307915, 'Innovation Lab retainer — {period_label}', 1, 15000)
        """,
        group["id"],
    )

    detail = await groups_service.get_group(pool, group["id"])
    assert len(detail["recurring_items"]) == 1
    assert detail["recurring_items"][0]["unit_price"] == 15000
    assert detail["schedule_items"] == []
