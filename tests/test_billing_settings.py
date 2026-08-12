"""Account-level billing settings (migration 0029).

One setting today: the invoice notes that Harvest will not send for us. The tests
that matter are about the boundary — an unknown key must be refused rather than
stored, and a blank value must be storable, because "send no notes" is a real
choice an operator can make.
"""
from __future__ import annotations

import pytest

from app.db import get_pool
from app.services.billing import settings_store


async def test_starts_seeded_and_empty():
    """The migration seeds the row, so a GET never has to distinguish "never
    configured" from "deliberately blank" — both mean send no notes."""
    pool = await get_pool()
    assert await settings_store.get_all(pool) == {"default_invoice_notes": ""}


async def test_update_and_read_back():
    pool = await get_pool()

    result = await settings_store.update(
        pool, {"default_invoice_notes": "Remit to: Frogslayer LLC."},
        actor="jacob@frogslayer.com",
    )

    assert result["default_invoice_notes"] == "Remit to: Frogslayer LLC."
    assert (await settings_store.get_all(pool))["default_invoice_notes"] == (
        "Remit to: Frogslayer LLC."
    )


async def test_multi_line_values_survive():
    """Remit-to blocks are several lines; this is the normal shape, not an edge."""
    pool = await get_pool()
    text = "Remit to:\nFrogslayer LLC\nPO Box 1234\n\nQuestions: ar@frogslayer.com"

    await settings_store.update(pool, {"default_invoice_notes": text})

    assert (await settings_store.get_all(pool))["default_invoice_notes"] == text


async def test_clearing_is_allowed():
    """Blank is a decision, not a missing value, so it must be storable."""
    pool = await get_pool()
    await settings_store.update(pool, {"default_invoice_notes": "something"})

    await settings_store.update(pool, {"default_invoice_notes": ""})

    assert (await settings_store.get_all(pool))["default_invoice_notes"] == ""


async def test_an_unknown_key_is_refused():
    """Without the allowlist a typo'd key becomes a row nothing ever reads."""
    pool = await get_pool()
    with pytest.raises(settings_store.UnknownSettingError, match="invoice_note"):
        await settings_store.update(pool, {"invoice_note": "typo"})


async def test_nothing_is_written_when_any_key_is_unknown():
    """All-or-nothing: a request with one good key and one typo must not
    half-apply, or the operator sees a success and a partial change."""
    pool = await get_pool()
    with pytest.raises(settings_store.UnknownSettingError):
        await settings_store.update(pool, {
            "default_invoice_notes": "should not land",
            "nonsense": "x",
        })

    assert (await settings_store.get_all(pool))["default_invoice_notes"] == ""


async def test_get_refuses_an_unknown_key():
    pool = await get_pool()
    async with pool.acquire() as conn:
        with pytest.raises(settings_store.UnknownSettingError):
            await settings_store.get(conn, "not_a_setting")


async def test_the_change_is_audited_with_the_actor_and_the_value():
    """This is invoice copy a client reads. "Who changed the remit-to details, and
    to what" has to be answerable later."""
    pool = await get_pool()

    await settings_store.update(
        pool, {"default_invoice_notes": "Pay via ACH."}, actor="jacob@frogslayer.com",
    )

    row = await pool.fetchrow(
        "SELECT actor, payload FROM audit_log "
        "WHERE event_type = 'billing.settings.updated' ORDER BY id DESC LIMIT 1"
    )
    assert row["actor"] == "jacob@frogslayer.com"
    assert row["payload"]["settings"]["default_invoice_notes"] == "Pay via ACH."


async def test_updated_by_is_recorded_on_the_row():
    pool = await get_pool()
    await settings_store.update(
        pool, {"default_invoice_notes": "x"}, actor="jacob@frogslayer.com",
    )

    row = await pool.fetchrow(
        "SELECT updated_by, updated_at FROM billing_settings "
        "WHERE key = 'default_invoice_notes'"
    )
    assert row["updated_by"] == "jacob@frogslayer.com"
    assert row["updated_at"] is not None


# ── Router ──────────────────────────────────────────────────────────────────


async def test_settings_endpoints_require_auth(unauthed_client):
    res = await unauthed_client.get("/billing/settings")
    assert res.status_code in (401, 403)
    res = await unauthed_client.patch("/billing/settings", json={})
    assert res.status_code in (401, 403)


async def test_get_returns_the_settings(client):
    res = await client.get("/billing/settings")
    assert res.status_code == 200
    assert res.json() == {"default_invoice_notes": ""}


async def test_patch_updates_and_returns_the_new_state(client):
    res = await client.patch(
        "/billing/settings", json={"default_invoice_notes": "Remit to: us."},
    )
    assert res.status_code == 200
    assert res.json()["default_invoice_notes"] == "Remit to: us."

    assert (await client.get("/billing/settings")).json()["default_invoice_notes"] == (
        "Remit to: us."
    )


async def test_patch_records_the_authenticated_user(client):
    await client.patch("/billing/settings", json={"default_invoice_notes": "x"})

    pool = await get_pool()
    actor = await pool.fetchval(
        "SELECT updated_by FROM billing_settings WHERE key = 'default_invoice_notes'"
    )
    assert actor not in (None, "", "system")


async def test_an_omitted_field_is_left_alone(client):
    """PATCH semantics: absent means "don't touch", which is not the same as
    empty. A form that submits one field must not blank the others."""
    await client.patch("/billing/settings", json={"default_invoice_notes": "keep me"})

    res = await client.patch("/billing/settings", json={})

    assert res.status_code == 200
    assert res.json()["default_invoice_notes"] == "keep me"


async def test_an_explicit_empty_string_clears_it(client):
    await client.patch("/billing/settings", json={"default_invoice_notes": "clear me"})

    res = await client.patch("/billing/settings", json={"default_invoice_notes": ""})

    assert res.json()["default_invoice_notes"] == ""


async def test_settings_never_expose_secrets(client):
    """Deploy config and credentials stay in env and must not leak through this
    endpoint just because it is called "settings"."""
    body = (await client.get("/billing/settings")).json()

    assert set(body) == {"default_invoice_notes"}
