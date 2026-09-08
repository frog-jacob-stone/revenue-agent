"""Turning a request into a draft document.

The one module here that writes. Two things it is careful about:

**The identity in the contract is the identity in the repository.** When the
request names a saved client, every `source="client"` field is read from that
row server-side and nothing about the counterparty is taken from the request
body. So a stale or tampered client block in the payload cannot end up in an
agreement — the same reasoning as the draw-invoice path recomputing its payload
rather than accepting the one the browser had.

**The audit row is the only record.** The .docx is streamed to the browser and
stored nowhere, so `contract.draft.generated` carries the full resolved field
set and the review list. "What did the draft we sent Acme in September say, and
what was still open in it" is answerable here or not at all.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

from app.orchestrator import events
from app.services import audit
from app.services.contracts import clients as clients_service
from app.services.contracts.fields import (
    TM_FIELDS_BY_TAG,
    client_fields,
    engagement_fields,
)
from app.services.contracts.render import render_tm_agreement


class ClientNotFound(LookupError):
    """The named saved client does not exist."""


class UnknownEngagementFields(ValueError):
    """The request carried tags no template field declares.

    Rejected rather than ignored: a dropped value is a blank in a contract, and
    silence gives the caller no way to notice. Usually means a tag was renamed
    in the .docx and a caller was not updated.
    """


class CounterpartyUnresolved(ValueError):
    """Neither, or both, of `client_id` and inline `client` were given."""


def _slug(text: str) -> str:
    """A filename-safe stem from a legal entity name.

    Collapses everything that is not alphanumeric — commas, periods, and `&`
    are ordinary in entity names and hostile in filenames across the three
    platforms this may be downloaded on.
    """
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:60] or "client"


def draft_filename(legal_entity_name: str, *, on: date) -> str:
    """`acme-industries-llc-tm-agreement-2026-09-08.docx`.

    Dated so successive drafts for one client sort chronologically and do not
    silently overwrite each other in the download folder. The date is passed in
    rather than read here so the caller controls it and tests are not
    time-dependent.
    """
    return f"{_slug(legal_entity_name)}-tm-agreement-{on.isoformat()}.docx"


async def resolve_tm_values(
    pool: asyncpg.Pool,
    *,
    client_id: UUID | None,
    client_inline: dict[str, Any] | None,
    engagement: dict[str, str | None],
) -> tuple[dict[str, str | None], str]:
    """Assemble the full tag→value map. Returns it with the legal entity name.

    Reads; writes nothing. Shared by the preview and the download so the two
    cannot disagree about what would be produced — which matters, because the
    preview is what ADR-0004 requires be shown before the click.
    """
    if (client_id is None) == (client_inline is None):
        raise CounterpartyUnresolved(
            "Provide exactly one of `client_id` (a saved client) or `client` "
            "(a counterparty being contracted for the first time)."
        )

    declared = {f.tag for f in engagement_fields()}
    unknown = sorted(set(engagement) - declared)
    if unknown:
        raise UnknownEngagementFields(
            f"No template field is declared for: {', '.join(unknown)}. "
            f"Known engagement fields: {', '.join(sorted(declared))}."
        )

    if client_id is not None:
        record = await clients_service.get_client(pool, client_id)
        if record is None:
            raise ClientNotFound(f"No saved client with id {client_id}")
        source: dict[str, Any] = record
    else:
        source = dict(client_inline or {})

    values: dict[str, str | None] = {}
    for field in client_fields():
        # `client_column` is guaranteed present for these — ContractField
        # asserts it at import.
        values[field.tag] = source.get(field.client_column)
    for field in engagement_fields():
        values[field.tag] = engagement.get(field.tag)

    legal_entity_name = (
        values.get(_legal_name_tag()) or source.get("legal_entity_name") or "client"
    )
    return values, legal_entity_name


def _legal_name_tag() -> str:
    """The tag backed by `legal_entity_name`, found rather than hardcoded.

    The filename and the audit row both need the entity name, and looking it up
    through the declaration keeps this from breaking silently if the tag is
    renamed in the template.
    """
    for tag, field in TM_FIELDS_BY_TAG.items():
        if field.client_column == "legal_entity_name":
            return tag
    raise RuntimeError(
        "No contract field is backed by contract_clients.legal_entity_name — "
        "the draft filename and audit trail both depend on one existing."
    )


async def generate_tm_draft(
    pool: asyncpg.Pool,
    *,
    client_id: UUID | None,
    client_inline: dict[str, Any] | None,
    engagement: dict[str, str | None],
    save_client: bool,
    actor: str,
    on: date,
) -> tuple[bytes, list[str], str]:
    """Produce the document. Returns bytes, the review labels, and the filename.

    When `save_client` is set for an inline counterparty, the save happens
    *first* and in its own transaction, and the draft is then rendered from the
    saved row. So the document and the repository cannot disagree, and a
    duplicate name fails before anything is generated rather than after the
    operator has already downloaded a file.
    """
    if save_client and client_inline is not None:
        saved = await clients_service.create_client(pool, client_inline, actor=actor)
        client_id, client_inline = saved["id"], None

    values, legal_entity_name = await resolve_tm_values(
        pool,
        client_id=client_id,
        client_inline=client_inline,
        engagement=engagement,
    )

    content, review_labels = render_tm_agreement(values)
    filename = draft_filename(legal_entity_name, on=on)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await audit.write_audit_event(
                conn,
                events.CONTRACT_DRAFT_GENERATED,
                actor=actor,
                payload={
                    "contract_type": "time_and_materials",
                    "filename": filename,
                    "client_id": str(client_id) if client_id else None,
                    "legal_entity_name": legal_entity_name,
                    "values": values,
                    "review_labels": review_labels,
                    "saved_client": save_client,
                },
            )

    return content, review_labels, filename
