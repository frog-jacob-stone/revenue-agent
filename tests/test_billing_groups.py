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


# ── Recurring line items keep their identity across a save ──────────────────
#
# Placeholder resolutions are keyed on `recurring_line_items.id`, so a save that
# re-minted ids would discard the amounts already entered for the month — as a
# side effect of editing something unrelated, and in the direction of
# under-billing. These tests are that guarantee.


def _line(project_id: int, description: str, **over):
    return {
        "harvest_project_id": project_id,
        "description": description,
        "quantity": 1,
        "unit_price": 1000,
        "kind": "Service",
        "is_placeholder": False,
        **over,
    }


async def _recurring_group(pool, lines: list[dict]):
    await _seed_snapshot(pool)
    return await groups_service.create_group(
        pool,
        _spec(
            "Northwind — Managed", NORTHWIND, [14308221],
            billing_type="recurring_monthly", recurring_items=lines,
        ),
    )


async def test_editing_a_line_keeps_every_id():
    pool = await get_pool()
    group = await _recurring_group(pool, [
        _line(14308221, "Management fee"),
        _line(14308221, "Hosting", is_placeholder=True, unit_price=0),
    ])
    before = {r["description"]: r["id"] for r in group["recurring_items"]}

    updated = await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            {**_line(14308221, "Management fee", unit_price=2500),
             "id": before["Management fee"]},
            {**_line(14308221, "Hosting", is_placeholder=True, unit_price=0),
             "id": before["Hosting"]},
        ],
    })

    after = {r["description"]: r["id"] for r in updated["recurring_items"]}
    assert after == before
    fee = next(r for r in updated["recurring_items"] if r["description"] == "Management fee")
    assert fee["unit_price"] == 2500


async def test_reordering_keeps_ids_and_rewrites_sort_order():
    """Position in the submitted list is authoritative, but identity is not
    positional — the ids must follow the rows, not the slots."""
    pool = await get_pool()
    group = await _recurring_group(pool, [
        _line(14308221, "First"),
        _line(14308221, "Second"),
    ])
    ids = {r["description"]: r["id"] for r in group["recurring_items"]}

    updated = await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            {**_line(14308221, "Second"), "id": ids["Second"]},
            {**_line(14308221, "First"), "id": ids["First"]},
        ],
    })

    rows = {r["description"]: r for r in updated["recurring_items"]}
    assert rows["Second"]["id"] == ids["Second"]
    assert rows["First"]["id"] == ids["First"]
    assert rows["Second"]["sort_order"] == 1
    assert rows["First"]["sort_order"] == 2


async def test_a_line_omitted_from_the_submission_is_deleted():
    pool = await get_pool()
    group = await _recurring_group(pool, [
        _line(14308221, "Keep"),
        _line(14308221, "Drop"),
    ])
    keep_id = next(
        r["id"] for r in group["recurring_items"] if r["description"] == "Keep"
    )

    updated = await groups_service.update_group(pool, group["id"], {
        "recurring_items": [{**_line(14308221, "Keep"), "id": keep_id}],
    })

    assert [r["description"] for r in updated["recurring_items"]] == ["Keep"]


async def test_a_line_with_no_id_is_inserted_alongside_the_existing_ones():
    pool = await get_pool()
    group = await _recurring_group(pool, [_line(14308221, "Management fee")])
    existing_id = group["recurring_items"][0]["id"]

    updated = await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            {**_line(14308221, "Management fee"), "id": existing_id},
            _line(14308221, "New tooling fee"),  # no id
        ],
    })

    rows = {r["description"]: r["id"] for r in updated["recurring_items"]}
    assert rows["Management fee"] == existing_id
    assert rows["New tooling fee"] != existing_id


async def test_a_line_id_from_another_group_is_refused():
    """Better to refuse than to insert a second copy under a fresh id — the
    operator would end up billing one fee twice."""
    pool = await get_pool()
    await _seed_snapshot(pool)
    other = await groups_service.create_group(
        pool,
        _spec("Acme Lab", ACME, [14307915], billing_type="recurring_monthly",
              recurring_items=[_line(14307915, "Someone else's fee")]),
    )
    mine = await groups_service.create_group(
        pool,
        _spec("Northwind — Managed", NORTHWIND, [14308221],
              billing_type="recurring_monthly",
              recurring_items=[_line(14308221, "Management fee")]),
    )
    stolen = other["recurring_items"][0]["id"]

    with pytest.raises(groups_service.BillingConfigError, match="does not belong"):
        await groups_service.update_group(pool, mine["id"], {
            "recurring_items": [
                {**_line(14308221, "Management fee"), "id": stolen},
            ],
        })


async def test_a_resolution_survives_an_unrelated_edit():
    """The whole reason ids became stable. Entering August's hosting amount and
    then changing the management fee must not discard the hosting amount."""
    pool = await get_pool()
    group = await _recurring_group(pool, [
        _line(14308221, "Management fee"),
        _line(14308221, "Hosting", is_placeholder=True, unit_price=0),
    ])
    ids = {r["description"]: r["id"] for r in group["recurring_items"]}

    await pool.execute(
        """
        INSERT INTO recurring_line_item_resolutions
            (recurring_line_item_id, run_month, resolution, unit_price)
        VALUES ($1, '2026-08-01', 'amount', 1240.00)
        """,
        ids["Hosting"],
    )

    await groups_service.update_group(pool, group["id"], {
        "recurring_items": [
            {**_line(14308221, "Management fee", unit_price=2500),
             "id": ids["Management fee"]},
            {**_line(14308221, "Hosting", is_placeholder=True, unit_price=0),
             "id": ids["Hosting"]},
        ],
    })

    assert await pool.fetchval(
        "SELECT unit_price FROM recurring_line_item_resolutions "
        "WHERE recurring_line_item_id = $1 AND run_month = '2026-08-01'",
        ids["Hosting"],
    ) == 1240.00
