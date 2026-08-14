"""Client exclusions — the account-wide "this is not a client" switch.

The point of keying on the client rather than the project is that it covers
projects nobody has created yet. Several tests below exist to pin that, and to
pin the one place exclusion deliberately does *not* reach: the billing-group
form, which must keep working for config that already exists.
"""
from __future__ import annotations

from app.db import get_pool
from app.services import client_exclusions, projects
from app.services.billing import catalog

ACME = 5735774        # a real client
OURSELVES = 11771973  # us


async def _seed(pool) -> None:
    for cid, name in ((ACME, "Acme Corp"), (OURSELVES, "Frogslayer")):
        await pool.execute(
            "INSERT INTO harvest_clients (harvest_id, name, currency) "
            "VALUES ($1, $2, 'USD')", cid, name,
        )
    rows = [
        (14307913, "Acme Platform", ACME, True, True),
        (14307914, "Acme Legacy", ACME, True, False),
        # Internal work flagged *billable* in Harvest — the case `is_billable`
        # cannot catch and the whole reason this feature exists.
        (14307915, "Frogslayer - Olympus", OURSELVES, True, True),
        (14307916, "Frogslayer - Trident", OURSELVES, True, True),
        (14307917, "Frogslayer - GreenTouch", OURSELVES, True, False),
        (14307918, "Frogslayer - Time Off", OURSELVES, False, True),
    ]
    for pid, name, cid, billable, active in rows:
        await pool.execute(
            """
            INSERT INTO harvest_projects
                (harvest_id, name, client_id, client_name, client_currency,
                 is_billable, is_active)
            VALUES ($1,$2,$3,(SELECT name FROM harvest_clients WHERE harvest_id=$3),
                    'USD',$4,$5)
            """,
            pid, name, cid, billable, active,
        )


async def test_exclusion_endpoints_require_auth(unauthed_client):
    for method, path in (
        ("get", "/client-exclusions"),
        ("post", "/client-exclusions"),
        ("delete", "/client-exclusions/1"),
    ):
        res = await getattr(unauthed_client, method)(path)
        assert res.status_code in (401, 403), f"{method.upper()} {path} was not protected"


async def test_excluding_a_client_hides_all_its_projects(client):
    pool = await get_pool()
    await _seed(pool)

    before = await projects.list_projects(pool)
    assert "Frogslayer - Olympus" in [p["name"] for p in before]

    res = await client.post("/client-exclusions", json={
        "harvest_client_id": OURSELVES,
        "reason": "This is us, not a client",
    })
    assert res.status_code == 201, res.text

    active = [p["name"] for p in await projects.list_projects(pool)]
    archived = [p["name"] for p in await projects.list_projects(pool, archived=True)]

    assert active == ["Acme Platform"]
    assert archived == ["Acme Legacy"]
    # Both billable internal projects are gone, from both views.
    assert not any("Frogslayer" in n for n in active + archived)


async def test_exclusion_covers_projects_created_later(client):
    """The reason this is keyed on the client. A project that did not exist
    when the exclusion was made must still be covered."""
    pool = await get_pool()
    await _seed(pool)
    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    await pool.execute(
        """
        INSERT INTO harvest_projects
            (harvest_id, name, client_id, client_name, is_billable, is_active)
        VALUES (14309999, 'Frogslayer - Brand New', $1, 'Frogslayer', true, true)
        """,
        OURSELVES,
    )

    names = [p["name"] for p in await projects.list_projects(pool)]
    assert "Frogslayer - Brand New" not in names
    assert names == ["Acme Platform"]


async def test_exclusion_suppresses_unmapped_reconciliation_flags(client):
    """Excluded work bills nowhere by definition, so "where does this bill?"
    should not be asked. This is what the manual "Frogslayer - Exclusion"
    billing group was standing in for."""
    from app.services.billing.reconcile import _unmapped_candidates

    pool = await get_pool()
    await _seed(pool)

    before = {p["name"] for p in await _unmapped_candidates(pool)}
    assert "Frogslayer - Olympus" in before

    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    after = {p["name"] for p in await _unmapped_candidates(pool)}
    assert after == {"Acme Platform"}


async def test_new_billing_group_cannot_pick_an_excluded_client(client):
    """You should not be able to build new billing config for a client the
    account says is not a client."""
    pool = await get_pool()
    await _seed(pool)
    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    res = await client.get("/billing/harvest/clients")
    assert res.status_code == 200
    names = [c["name"] for c in res.json()]
    assert names == ["Acme Corp"]
    assert "Frogslayer" not in names

    projects = await client.get(
        "/billing/harvest/projects", params={"client_id": OURSELVES}
    )
    assert projects.json() == []


async def test_edit_form_still_sees_an_excluded_client(client):
    """The escape hatch. A group built before its client was excluded must stay
    editable — the client select is editable, so a missing option would blank
    the field and let a save wipe it."""
    pool = await get_pool()
    await _seed(pool)
    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    res = await client.get(
        "/billing/harvest/clients", params={"include_excluded": "true"}
    )
    assert "Frogslayer" in [c["name"] for c in res.json()]

    res = await client.get(
        "/billing/harvest/projects",
        params={"client_id": OURSELVES, "include_excluded": "true"},
    )
    assert "Frogslayer - Olympus" in [p["name"] for p in res.json()]

    # And the service default stays closed, so a new caller opts in explicitly.
    assert "Frogslayer" not in [c["name"] for c in await catalog.list_clients(pool)]


async def test_excluding_a_client_that_still_bills_is_an_error_flag(client):
    """The dangerous combination, and the reason exclusion needs a guard:
    hiding a client from reporting does not stop its invoices."""
    from app.services.billing.reconcile import _excluded_with_active_group

    pool = await get_pool()
    await _seed(pool)

    created = await client.post("/billing/groups", json={
        "name": "Frogslayer — internal",
        "harvest_client_id": OURSELVES,
        "billing_type": "manual",
        "time_summary_type": "task",
        "include_expenses": False,
        "expense_summary_type": "category",
        "projects": [{"harvest_project_id": 14307915, "sort_order": 1}],
    })
    assert created.status_code == 201, created.text

    assert await _excluded_with_active_group(pool) == []

    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    rows = await _excluded_with_active_group(pool)
    assert len(rows) == 1
    assert rows[0]["group_name"] == "Frogslayer — internal"


async def test_exclusion_is_idempotent_and_updates_the_reason(client):
    pool = await get_pool()
    await _seed(pool)

    await client.post("/client-exclusions", json={
        "harvest_client_id": OURSELVES, "reason": "first",
    })
    res = await client.post("/client-exclusions", json={
        "harvest_client_id": OURSELVES, "reason": "second",
    })
    assert res.status_code == 201
    rows = res.json()
    assert len(rows) == 1, "re-excluding created a duplicate row"
    assert rows[0]["reason"] == "second"
    assert rows[0]["client_name"] == "Frogslayer"
    assert rows[0]["project_count"] == 4


async def test_removing_an_exclusion_brings_projects_back(client):
    pool = await get_pool()
    await _seed(pool)
    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    res = await client.delete(f"/client-exclusions/{OURSELVES}")
    assert res.status_code == 200, res.text
    assert res.json() == []

    names = [p["name"] for p in await projects.list_projects(pool)]
    assert "Frogslayer - Olympus" in names


async def test_removing_a_client_that_was_not_excluded_is_a_404(client):
    await _seed(await get_pool())
    res = await client.delete(f"/client-exclusions/{ACME}")
    assert res.status_code == 404


async def test_exclusion_survives_a_snapshot_the_client_has_left(client):
    """An exclusion outlives the cache row it names. If the client vanishes
    from Harvest the standing instruction must still be listed, or there is no
    way to undo it."""
    pool = await get_pool()
    await _seed(pool)
    await client.post("/client-exclusions", json={"harvest_client_id": OURSELVES})

    await pool.execute("DELETE FROM harvest_projects WHERE client_id = $1", OURSELVES)
    await pool.execute("DELETE FROM harvest_clients WHERE harvest_id = $1", OURSELVES)

    rows = (await client.get("/client-exclusions")).json()
    assert len(rows) == 1
    assert rows[0]["harvest_client_id"] == OURSELVES
    assert rows[0]["client_name"] is None
    assert rows[0]["project_count"] == 0


async def test_exclusion_writes_an_audit_trail(client):
    pool = await get_pool()
    await _seed(pool)

    await client.post("/client-exclusions", json={
        "harvest_client_id": OURSELVES, "reason": "This is us",
    })
    await client.delete(f"/client-exclusions/{OURSELVES}")

    events = [
        r["event_type"] for r in await pool.fetch(
            "SELECT event_type FROM audit_log WHERE event_type LIKE 'client.%' "
            "ORDER BY id"
        )
    ]
    assert events == ["client.excluded", "client.exclusion.removed"]

    payload = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE event_type = 'client.excluded'"
    )
    # The reason is the point of the trail — "why is this client hidden".
    assert payload["reason"] == "This is us"
    assert payload["harvest_client_id"] == OURSELVES


async def test_predicate_helper_is_parameterless():
    """It composes into callers that build `$n` placeholders positionally, so
    it must not introduce one of its own."""
    sql = client_exclusions.not_excluded_sql()
    assert "$" not in sql
    assert "NOT EXISTS" in sql
