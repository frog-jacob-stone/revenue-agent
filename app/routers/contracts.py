"""Contracts router — the saved-client repository and T&M draft generation.

Operator-initiated (ADR-0004) throughout. Generating a draft and editing a saved
client are both writes, and neither needs an approval row: the operator is on
the screen, the exact payload is the form they filled and the preview they just
read, these endpoints are human-only and in no agent's `allowed_tools` and are
not executors, and every transition writes `audit_log`.

There is no agent anywhere in this path and no LLM. A T&M draft is deterministic
substitution into a versioned Word template — see `app/services/contracts/`.

`POST /contracts/tm/preview` exists to satisfy ADR-0004's first condition. The
download response can only report what was left for review *after* the file is
already on disk; the preview is what lets the operator see it before deciding.
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response

from app.auth import AuthUser, get_current_user
from app.db import get_pool
from app.models.contracts import (
    ContractClient,
    ContractClientInput,
    ContractClientPatch,
    ContractFieldSpec,
    TmDraftPreview,
    TmDraftRequest,
)
from app.services.contracts import clients as clients_service
from app.services.contracts import drafts
from app.services.contracts.fields import TM_FIELDS
from app.services.contracts.render import TemplateMissing

router = APIRouter(prefix="/contracts", tags=["contracts"])

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


async def _db() -> asyncpg.Pool:
    return await get_pool()


def _actor(user: AuthUser) -> str:
    return user.email or str(user.id)


# ── Saved clients ───────────────────────────────────────────────────────────

@router.get("/clients", response_model=list[ContractClient])
async def list_contract_clients(pool: asyncpg.Pool = Depends(_db)):
    """Every saved client, ordered as the picker shows them."""
    rows = await clients_service.list_clients(pool)
    return [ContractClient.model_validate(r) for r in rows]


@router.post("/clients", response_model=ContractClient, status_code=201)
async def create_contract_client(
    body: ContractClientInput,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Save a counterparty so the next contract for them needs no retyping."""
    try:
        row = await clients_service.create_client(
            pool, body.model_dump(), actor=_actor(user)
        )
    except clients_service.DuplicateLegalEntityName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ContractClient.model_validate(row)


@router.patch("/clients/{client_id}", response_model=ContractClient)
async def update_contract_client(
    client_id: UUID,
    body: ContractClientPatch,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Edit a saved client. Omitted fields are left alone; `""` clears one.

    `exclude_unset` is what makes that true and is not optional — dumping the
    whole model would send explicit nulls for every field the caller did not
    mention and blank them.
    """
    try:
        row = await clients_service.update_client(
            pool,
            client_id,
            body.model_dump(exclude_unset=True),
            actor=_actor(user),
        )
    except clients_service.DuplicateLegalEntityName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="No such saved client")
    return ContractClient.model_validate(row)


@router.delete("/clients/{client_id}", status_code=204)
async def delete_contract_client(
    client_id: UUID,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Forget a saved client. 404 when there was nothing to forget."""
    removed = await clients_service.delete_client(
        pool, client_id, actor=_actor(user)
    )
    if not removed:
        raise HTTPException(status_code=404, detail="No such saved client")
    return Response(status_code=204)


# ── T&M drafting ────────────────────────────────────────────────────────────

@router.get("/tm/fields", response_model=list[ContractFieldSpec])
async def list_tm_fields():
    """The blanks a T&M draft asks about, in form order.

    Served rather than duplicated in the UI so labels and ordering come from the
    same declaration the renderer uses. Reads no database and needs none.
    """
    return [
        ContractFieldSpec(
            tag=f.tag,
            label=f.label,
            source=f.source,
            client_field=f.client_column,
        )
        for f in TM_FIELDS
    ]


@router.post("/tm/preview", response_model=TmDraftPreview)
async def preview_tm_draft(
    body: TmDraftRequest,
    pool: asyncpg.Pool = Depends(_db),
):
    """What generating would produce. Writes nothing — not even an audit row.

    Deliberately not audited: it is a read, and a trail of previews would bury
    the `contract.draft.generated` rows that record actual output.
    """
    values, legal_entity_name = await _resolve(pool, body)
    review_labels = [
        f.label for f in TM_FIELDS if not (values.get(f.tag) or "").strip()
    ]
    return TmDraftPreview(
        values=values,
        review_labels=review_labels,
        filename=drafts.draft_filename(legal_entity_name, on=date.today()),
    )


@router.post("/tm/draft", response_class=Response)
async def generate_tm_draft(
    body: TmDraftRequest,
    pool: asyncpg.Pool = Depends(_db),
    user: AuthUser = Depends(get_current_user),
):
    """Generate the .docx and return it as a download.

    The file is streamed and stored nowhere; the audit row is the record. See
    `app/services/contracts/drafts.py`.
    """
    try:
        content, review_labels, filename = await drafts.generate_tm_draft(
            pool,
            client_id=body.client_id,
            client_inline=body.client.model_dump() if body.client else None,
            engagement=body.engagement,
            save_client=body.save_client,
            actor=_actor(user),
            on=date.today(),
        )
    except drafts.ClientNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except clients_service.DuplicateLegalEntityName as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (drafts.CounterpartyUnresolved, drafts.UnknownEngagementFields) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TemplateMissing as exc:
        # 503, not 500: the endpoint is fine and the request was valid: the
        # deployment is missing an asset. Distinguishing them is what stops this
        # being debugged as a code bug.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # A convenience echo of the preview, so a caller that skipped the
            # preview still learns what was left open. Labels are declared in
            # `fields.py` and are plain ASCII; commas are stripped anyway so the
            # value stays unambiguously splittable.
            "X-Contract-Review-Fields": ", ".join(
                label.replace(",", " ") for label in review_labels
            ),
        },
    )


async def _resolve(pool: asyncpg.Pool, body: TmDraftRequest):
    """Shared error translation for the preview and the download."""
    try:
        return await drafts.resolve_tm_values(
            pool,
            client_id=body.client_id,
            client_inline=body.client.model_dump() if body.client else None,
            engagement=body.engagement,
        )
    except drafts.ClientNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (drafts.CounterpartyUnresolved, drafts.UnknownEngagementFields) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
