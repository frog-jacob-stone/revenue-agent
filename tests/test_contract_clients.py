"""The saved-client repository.

The behaviour worth pinning is mostly about *not* losing things: a partial edit
must not blank the fields it does not mention, a duplicate name must be a
recognisable error rather than a 500, and every change must leave a trail whose
payload is still interpretable after the row itself is gone.
"""
from __future__ import annotations

import uuid

import pytest

from app.db import get_pool
from app.services.contracts import clients as svc

ACTOR = "jacob.stone@frogslayer.com"

ACME = {
    "legal_entity_name": "Acme Industries, LLC",
    "address_line1": "100 Congress Ave, Suite 400",
    "city": "Austin",
    "state": "TX",
    "postal_code": "78701",
    "signatory_name": "Dana Reyes",
    "msa_effective_date": "January 15, 2026",
}


async def _audit(pool, event_type: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT actor, payload FROM audit_log WHERE event_type = $1 "
        "ORDER BY id",
        event_type,
    )
    return [dict(r) for r in rows]


# ── Create ──────────────────────────────────────────────────────────────────

async def test_create_returns_the_row_and_stamps_the_actor():
    pool = await get_pool()
    row = await svc.create_client(pool, dict(ACME), actor=ACTOR)

    assert row["legal_entity_name"] == "Acme Industries, LLC"
    assert row["city"] == "Austin"
    assert row["created_by"] == ACTOR
    assert row["updated_by"] == ACTOR
    assert isinstance(row["id"], uuid.UUID)


async def test_create_audits_with_the_values_inline():
    """Values, not just the id — the row is editable and deletable.

    A trail that said "created client <uuid>" would be worthless the moment
    someone renamed or removed the row it points at.
    """
    pool = await get_pool()
    await svc.create_client(pool, dict(ACME), actor=ACTOR)

    events = await _audit(pool, "contract.client.created")
    assert len(events) == 1
    assert events[0]["actor"] == ACTOR
    payload = events[0]["payload"]
    assert payload["legal_entity_name"] == "Acme Industries, LLC"
    assert payload["postal_code"] == "78701"
    assert payload["signatory_name"] == "Dana Reyes"


async def test_msa_effective_date_round_trips_as_free_text():
    """Text, not a date, so the operator controls how it reads in the document.

    Asserted with a worded date rather than an ISO one, because that is the
    whole point of the column's type: nothing here parses or reformats it.
    """
    pool = await get_pool()
    row = await svc.create_client(pool, dict(ACME), actor=ACTOR)
    assert row["msa_effective_date"] == "January 15, 2026"

    fetched = await svc.get_client(pool, row["id"])
    assert fetched["msa_effective_date"] == "January 15, 2026"


async def test_msa_effective_date_is_optional():
    """Plenty of clients have no MSA — a first engagement is the common case."""
    pool = await get_pool()
    values = dict(ACME)
    del values["msa_effective_date"]

    row = await svc.create_client(pool, values, actor=ACTOR)
    assert row["msa_effective_date"] is None


async def test_editable_columns_cover_every_client_field():
    """`_EDITABLE` and the template's client fields must not drift.

    A field declared in `fields.py` but missing from `_EDITABLE` is never
    written or read, so the draft silently renders it as a review marker
    forever — the operator fills the form, saves, and the value vanishes.
    """
    from app.services.contracts.fields import client_fields

    declared = {f.client_column for f in client_fields()}
    missing = sorted(declared - set(svc._EDITABLE))
    assert not missing, (
        f"{', '.join(missing)} is a contract field with no column in "
        f"clients._EDITABLE, so it can never be saved"
    )


async def test_create_accepts_a_name_only_prospect():
    """A half-known prospect is a legitimate row.

    The gaps are not silent — they render as [REVIEW: …] markers in the draft,
    which is a better place to notice them than a form that refused to save.
    """
    pool = await get_pool()
    row = await svc.create_client(
        pool, {"legal_entity_name": "Northwind Trading Co"}, actor=ACTOR
    )

    assert row["legal_entity_name"] == "Northwind Trading Co"
    assert row["city"] is None
    assert row["signatory_name"] is None


async def test_duplicate_name_raises_a_typed_error():
    """Not asyncpg's UniqueViolationError, which the router would 500 on."""
    pool = await get_pool()
    await svc.create_client(pool, dict(ACME), actor=ACTOR)

    with pytest.raises(svc.DuplicateLegalEntityName, match="Acme Industries"):
        await svc.create_client(pool, dict(ACME), actor=ACTOR)


async def test_duplicate_name_writes_no_audit_row():
    """The failed create must not leave a trail saying it happened."""
    pool = await get_pool()
    await svc.create_client(pool, dict(ACME), actor=ACTOR)
    with pytest.raises(svc.DuplicateLegalEntityName):
        await svc.create_client(pool, dict(ACME), actor=ACTOR)

    assert len(await _audit(pool, "contract.client.created")) == 1


# ── Read ────────────────────────────────────────────────────────────────────

async def test_list_sorts_case_insensitively():
    """`ORDER BY legal_entity_name` alone puts "Zeta" before "acme"."""
    pool = await get_pool()
    for name in ("Zeta Corp", "acme llc", "Mid Industries"):
        await svc.create_client(pool, {"legal_entity_name": name}, actor=ACTOR)

    names = [r["legal_entity_name"] for r in await svc.list_clients(pool)]
    assert names == ["acme llc", "Mid Industries", "Zeta Corp"]


async def test_get_client_returns_none_for_an_unknown_id():
    pool = await get_pool()
    assert await svc.get_client(pool, uuid.uuid4()) is None


# ── Update ──────────────────────────────────────────────────────────────────

async def test_partial_update_leaves_unmentioned_fields_alone():
    """The whole point of PATCH semantics here."""
    pool = await get_pool()
    created = await svc.create_client(pool, dict(ACME), actor=ACTOR)

    updated = await svc.update_client(
        pool, created["id"], {"postal_code": "78702"}, actor="someone.else@x.com"
    )

    assert updated["postal_code"] == "78702"
    # Untouched.
    assert updated["city"] == "Austin"
    assert updated["signatory_name"] == "Dana Reyes"
    assert updated["legal_entity_name"] == "Acme Industries, LLC"
    # Provenance moves, creation does not.
    assert updated["updated_by"] == "someone.else@x.com"
    assert updated["created_by"] == ACTOR


async def test_empty_string_clears_a_field():
    pool = await get_pool()
    created = await svc.create_client(pool, dict(ACME), actor=ACTOR)

    updated = await svc.update_client(
        pool, created["id"], {"signatory_name": ""}, actor=ACTOR
    )
    assert updated["signatory_name"] == ""


async def test_update_audits_only_what_changed():
    """"Who fixed the ZIP" is the question; a full snapshot buries it."""
    pool = await get_pool()
    created = await svc.create_client(pool, dict(ACME), actor=ACTOR)
    await svc.update_client(
        pool, created["id"], {"postal_code": "78702"}, actor=ACTOR
    )

    events = await _audit(pool, "contract.client.updated")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["changed"] == {"postal_code": "78702"}
    # The name rides along so the row is identifiable without a second lookup.
    assert payload["legal_entity_name"] == "Acme Industries, LLC"


async def test_no_op_update_writes_no_audit_row():
    """A trail full of "updated, nothing different" is a trail nobody reads."""
    pool = await get_pool()
    created = await svc.create_client(pool, dict(ACME), actor=ACTOR)

    row = await svc.update_client(pool, created["id"], {}, actor=ACTOR)
    assert row["id"] == created["id"]
    assert await _audit(pool, "contract.client.updated") == []


async def test_update_of_an_unknown_id_returns_none():
    pool = await get_pool()
    assert await svc.update_client(
        pool, uuid.uuid4(), {"city": "Austin"}, actor=ACTOR
    ) is None


async def test_update_onto_an_existing_name_raises_the_typed_error():
    pool = await get_pool()
    await svc.create_client(pool, dict(ACME), actor=ACTOR)
    other = await svc.create_client(
        pool, {"legal_entity_name": "Northwind Trading Co"}, actor=ACTOR
    )

    with pytest.raises(svc.DuplicateLegalEntityName):
        await svc.update_client(
            pool,
            other["id"],
            {"legal_entity_name": "Acme Industries, LLC"},
            actor=ACTOR,
        )


# ── Delete ──────────────────────────────────────────────────────────────────

async def test_delete_removes_the_row_and_audits_the_name():
    pool = await get_pool()
    created = await svc.create_client(pool, dict(ACME), actor=ACTOR)

    assert await svc.delete_client(pool, created["id"], actor=ACTOR) is True
    assert await svc.get_client(pool, created["id"]) is None

    events = await _audit(pool, "contract.client.deleted")
    assert len(events) == 1
    # The name is the only thing that makes the row identifiable now it is gone.
    assert events[0]["payload"]["legal_entity_name"] == "Acme Industries, LLC"


async def test_delete_of_an_unknown_id_is_false_and_silent():
    """False rather than a reported success — a no-op usually means a wrong id."""
    pool = await get_pool()
    assert await svc.delete_client(pool, uuid.uuid4(), actor=ACTOR) is False
    assert await _audit(pool, "contract.client.deleted") == []


async def test_name_is_reusable_after_delete():
    pool = await get_pool()
    created = await svc.create_client(pool, dict(ACME), actor=ACTOR)
    await svc.delete_client(pool, created["id"], actor=ACTOR)

    again = await svc.create_client(pool, dict(ACME), actor=ACTOR)
    assert again["id"] != created["id"]
