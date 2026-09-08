"""The saved-client repository.

Exists for one reason: drafting a contract for a client we have contracted with
before should not mean retyping their legal name, address, and signatory. That
is transcription, and it is the slowest part of producing a draft.

CRUD, operator-initiated, audited, human-only (ADR-0004). No sync, no Harvest
link, no CRM ambitions — see the migration comment in
`supabase/migrations/20250101000036_contract_clients.sql` for why each of those
is deliberate.

Nothing here validates completeness. A prospect known only by name is a
legitimate row: the missing pieces surface in the draft as `[REVIEW: …]`
markers, which is a better place to notice them than a form that refused to save.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit

# The columns an operator may set, in one place. Both writers below drive their
# SQL off this, so adding a column to the table means touching this tuple and
# nothing else.
_EDITABLE = (
    "legal_entity_name",
    "address_line1",
    "city",
    "state",
    "postal_code",
    "signatory_name",
    "msa_effective_date",
)

_COLUMNS = f"id, {', '.join(_EDITABLE)}, created_at, updated_at, created_by, updated_by"


class DuplicateLegalEntityName(ValueError):
    """Another saved client already uses this legal entity name.

    Its own type so the router can answer 409 instead of letting asyncpg's
    `UniqueViolationError` surface as a 500. The name is unique because it is
    also the label the operator picks from, and two identical labels are an
    unresolvable choice on the form.
    """


async def list_clients(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Every saved client, ordered the way the picker shows them.

    Case-insensitive sort: `ORDER BY legal_entity_name` alone puts "Zeta" before
    "acme" under C collation, which looks broken in a dropdown.
    """
    rows = await pool.fetch(
        f"SELECT {_COLUMNS} FROM contract_clients ORDER BY lower(legal_entity_name)"
    )
    return [dict(r) for r in rows]


async def get_client(pool: asyncpg.Pool, client_id: UUID) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        f"SELECT {_COLUMNS} FROM contract_clients WHERE id = $1", client_id
    )
    return dict(row) if row else None


async def create_client(
    pool: asyncpg.Pool, values: dict[str, Any], *, actor: str
) -> dict[str, Any]:
    """Save a new client. Raises `DuplicateLegalEntityName` on a name collision."""
    supplied = [c for c in _EDITABLE if c in values]
    placeholders = ", ".join(f"${i}" for i in range(1, len(supplied) + 1))
    actor_param = f"${len(supplied) + 1}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO contract_clients
                        ({', '.join(supplied)}, created_by, updated_by)
                    VALUES ({placeholders}, {actor_param}, {actor_param})
                    RETURNING {_COLUMNS}
                    """,
                    *(values[c] for c in supplied), actor,
                )
            except asyncpg.UniqueViolationError as exc:
                raise DuplicateLegalEntityName(
                    f"A saved client named {values.get('legal_entity_name')!r} "
                    f"already exists"
                ) from exc

            created = dict(row)
            await audit.write_audit_event(
                conn,
                events.CONTRACT_CLIENT_CREATED,
                actor=actor,
                payload=_audit_payload(created),
            )
    return created


async def update_client(
    pool: asyncpg.Pool, client_id: UUID, changes: dict[str, Any], *, actor: str
) -> dict[str, Any] | None:
    """Apply a partial edit. Returns None when the row does not exist.

    `changes` holds only the fields the caller actually sent — an omitted field
    is left alone, an empty string clears it. The caller is responsible for that
    distinction (`model_dump(exclude_unset=True)`); passing a full body here
    would silently blank whatever the form did not render.

    An empty `changes` is a no-op that still returns the row, and deliberately
    writes no audit event: nothing changed, and a trail full of "updated,
    nothing different" rows is a trail people stop reading.
    """
    settable = [c for c in _EDITABLE if c in changes]
    if not settable:
        return await get_client(pool, client_id)

    assignments = ", ".join(f"{c} = ${i}" for i, c in enumerate(settable, start=1))
    actor_param = f"${len(settable) + 1}"
    id_param = f"${len(settable) + 2}"

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    f"""
                    UPDATE contract_clients
                       SET {assignments}, updated_by = {actor_param}, updated_at = now()
                     WHERE id = {id_param}
                    RETURNING {_COLUMNS}
                    """,
                    *(changes[c] for c in settable), actor, client_id,
                )
            except asyncpg.UniqueViolationError as exc:
                raise DuplicateLegalEntityName(
                    f"A saved client named {changes.get('legal_entity_name')!r} "
                    f"already exists"
                ) from exc

            if row is None:
                return None

            updated = dict(row)
            await audit.write_audit_event(
                conn,
                events.CONTRACT_CLIENT_UPDATED,
                actor=actor,
                # The fields that changed, not the whole row: "who fixed the
                # ZIP" is the question, and a full snapshot on every edit buries
                # it.
                payload={
                    "id": str(client_id),
                    "legal_entity_name": updated["legal_entity_name"],
                    "changed": {c: changes[c] for c in settable},
                },
            )
    return updated


async def delete_client(
    pool: asyncpg.Pool, client_id: UUID, *, actor: str
) -> bool:
    """Forget a saved client. False when there was nothing to forget.

    A hard delete. Nothing references this row — generated documents are not
    stored, and the audit trail records the values it used inline rather than by
    id — so there is no dangling reference a soft delete would be protecting.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "DELETE FROM contract_clients WHERE id = $1 "
                "RETURNING id, legal_entity_name",
                client_id,
            )
            if row is None:
                return False
            await audit.write_audit_event(
                conn,
                events.CONTRACT_CLIENT_DELETED,
                actor=actor,
                payload={
                    "id": str(row["id"]),
                    "legal_entity_name": row["legal_entity_name"],
                },
            )
    return True


def _audit_payload(row: dict[str, Any]) -> dict[str, Any]:
    """The row as the trail records it.

    Values inline rather than just the id, because the row is mutable and
    deletable: a trail that says "created client <uuid>" tells you nothing once
    the row is gone or renamed.
    """
    payload: dict[str, Any] = {"id": str(row["id"])}
    payload.update({c: row[c] for c in _EDITABLE})
    return payload
