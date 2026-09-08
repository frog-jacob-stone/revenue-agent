"""Contracts router.

The draft endpoints are exercised against a synthetic template, for the same
reason `test_contract_render.py` is: the operator's real .docx is theirs to
change, and a router test that broke every time they reworded a clause would
teach people to ignore it. What is asserted here is the HTTP contract — status
codes, the download headers, and that the identity in the document comes from
the server rather than the request.
"""
from __future__ import annotations

import uuid
import zipfile
from io import BytesIO

import pytest
from docx import Document

from app.db import get_pool
from app.main import app
from app.routers.contracts import DOCX_MEDIA_TYPE
from app.services.contracts import render as render_module
from app.services.contracts.fields import (
    TM_FIELDS,
    client_fields,
    engagement_fields,
    review_marker,
)

# Every client-sourced field populated. That completeness is load-bearing for
# the review-list assertions below: they assert that a *fully known* saved
# client leaves only the engagement fields to flag, so a gap here would show up
# as an unexplained extra label rather than as a failure anyone could read.
ACME = {
    "legal_entity_name": "Acme Industries, LLC",
    "address_line1": "100 Congress Ave, Suite 400",
    "city": "Austin",
    "state": "TX",
    "postal_code": "78701",
    "signatory_name": "Dana Reyes",
    "msa_effective_date": "January 15, 2026",
}


@pytest.fixture
def template(tmp_path, monkeypatch):
    """A template declaring exactly the tags `TM_FIELDS` declares."""
    doc = Document()
    for field in TM_FIELDS:
        doc.add_paragraph("%s: {{ %s }}" % (field.label, field.tag))
    path = tmp_path / "tm_agreement.docx"
    doc.save(path)
    monkeypatch.setattr(render_module, "TM_TEMPLATE", path)
    return path


def _text(content: bytes) -> str:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        return "".join(
            archive.read(n).decode()
            for n in archive.namelist()
            if n.startswith("word/") and n.endswith(".xml")
        )


def _engagement() -> dict[str, str]:
    return {f.tag: f"value for {f.tag}" for f in engagement_fields()}


# ── Saved clients ───────────────────────────────────────────────────────────

def test_acme_fixture_covers_every_client_field():
    """The fixture must stay complete as client fields are added.

    Without this, adding a `source="client"` field makes three review-list
    assertions fail with an unexplained extra label — which is how
    `msa_effective_date` landed. This says the actual problem instead.
    """
    missing = sorted({f.client_column for f in client_fields()} - set(ACME))
    assert not missing, (
        f"ACME is missing {', '.join(missing)}. Every client-sourced field needs "
        f"a value here, or the review-list assertions below will flag it and "
        f"read as an unrelated failure."
    )


async def test_client_crud_round_trip(client):
    created = await client.post("/contracts/clients", json=ACME)
    assert created.status_code == 201
    body = created.json()
    assert body["legal_entity_name"] == "Acme Industries, LLC"
    client_id = body["id"]

    listed = await client.get("/contracts/clients")
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [client_id]

    patched = await client.patch(
        f"/contracts/clients/{client_id}", json={"postal_code": "78702"}
    )
    assert patched.status_code == 200
    assert patched.json()["postal_code"] == "78702"
    # PATCH means partial: the fields the body omitted are untouched.
    assert patched.json()["city"] == "Austin"

    deleted = await client.delete(f"/contracts/clients/{client_id}")
    assert deleted.status_code == 204
    assert (await client.get("/contracts/clients")).json() == []


async def test_create_requires_a_legal_entity_name(client):
    res = await client.post("/contracts/clients", json={"city": "Austin"})
    assert res.status_code == 422


async def test_blank_legal_entity_name_is_rejected(client):
    res = await client.post("/contracts/clients", json={"legal_entity_name": ""})
    assert res.status_code == 422


async def test_duplicate_name_is_409_not_500(client):
    await client.post("/contracts/clients", json=ACME)
    again = await client.post("/contracts/clients", json=ACME)
    assert again.status_code == 409
    assert "already exists" in again.json()["detail"]


async def test_patch_and_delete_of_unknown_id_are_404(client):
    unknown = uuid.uuid4()
    assert (
        await client.patch(f"/contracts/clients/{unknown}", json={"city": "Austin"})
    ).status_code == 404
    assert (await client.delete(f"/contracts/clients/{unknown}")).status_code == 404


# ── Field declarations ──────────────────────────────────────────────────────

async def test_fields_endpoint_serves_the_declaration_in_order(client):
    res = await client.get("/contracts/tm/fields")
    assert res.status_code == 200
    served = res.json()
    assert [f["tag"] for f in served] == [f.tag for f in TM_FIELDS]
    assert [f["label"] for f in served] == [f.label for f in TM_FIELDS]
    assert {f["source"] for f in served} <= {"client", "engagement"}


# ── Preview ─────────────────────────────────────────────────────────────────

async def test_preview_reports_blanks_without_writing_anything(client, template):
    """ADR-0004 wants the payload visible before the click; this is that."""
    pool = await get_pool()
    created = await client.post("/contracts/clients", json=ACME)
    client_id = created.json()["id"]

    res = await client.post(
        "/contracts/tm/preview",
        json={"client_id": client_id, "engagement": {}},
    )
    assert res.status_code == 200
    body = res.json()

    assert body["values"]["client_legal_name"] == "Acme Industries, LLC"
    # Every engagement field was left empty, so every one is flagged.
    assert body["review_labels"] == [f.label for f in engagement_fields()]
    assert body["filename"].startswith("acme-industries-llc-tm-agreement-")
    assert body["filename"].endswith(".docx")

    # A preview is a read and must leave no trail of its own.
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE event_type = 'contract.draft.generated'"
    ) == 0


async def test_preview_requires_exactly_one_counterparty(client, template):
    neither = await client.post("/contracts/tm/preview", json={"engagement": {}})
    assert neither.status_code == 422

    both = await client.post(
        "/contracts/tm/preview",
        json={"client_id": str(uuid.uuid4()), "client": ACME, "engagement": {}},
    )
    assert both.status_code == 422


async def test_unknown_engagement_tag_is_rejected(client, template):
    """A silently dropped value is a blank in a contract."""
    res = await client.post(
        "/contracts/tm/preview",
        json={"client": ACME, "engagement": {"not_a_field": "x"}},
    )
    assert res.status_code == 422
    assert "not_a_field" in res.json()["detail"]


async def test_unknown_client_id_is_404(client, template):
    res = await client.post(
        "/contracts/tm/preview",
        json={"client_id": str(uuid.uuid4()), "engagement": {}},
    )
    assert res.status_code == 404


# ── Draft download ──────────────────────────────────────────────────────────

async def test_draft_returns_a_docx_with_download_headers(client, template):
    created = await client.post("/contracts/clients", json=ACME)
    client_id = created.json()["id"]

    res = await client.post(
        "/contracts/tm/draft",
        json={"client_id": client_id, "engagement": _engagement()},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == DOCX_MEDIA_TYPE

    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "acme-industries-llc-tm-agreement-" in disposition

    # Fully populated, so nothing is flagged.
    assert res.headers["x-contract-review-fields"] == ""

    # A real, openable document with the saved identity in it.
    text = _text(res.content)
    assert "Acme Industries, LLC" in text
    assert "{{" not in text
    assert Document(BytesIO(res.content)).paragraphs


async def test_draft_reports_review_fields_in_a_header(client, template):
    created = await client.post("/contracts/clients", json=ACME)

    res = await client.post(
        "/contracts/tm/draft",
        json={"client_id": created.json()["id"], "engagement": {}},
    )
    assert res.status_code == 200

    reported = res.headers["x-contract-review-fields"]
    for field in engagement_fields():
        assert field.label in reported
        assert review_marker(field.label) in _text(res.content)


async def test_draft_audits_the_values_and_the_review_list(client, template):
    pool = await get_pool()
    created = await client.post("/contracts/clients", json=ACME)

    res = await client.post(
        "/contracts/tm/draft",
        json={"client_id": created.json()["id"], "engagement": {}},
    )
    assert res.status_code == 200

    rows = await pool.fetch(
        "SELECT actor, payload FROM audit_log "
        "WHERE event_type = 'contract.draft.generated'"
    )
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["contract_type"] == "time_and_materials"
    assert payload["legal_entity_name"] == "Acme Industries, LLC"
    assert payload["values"]["client_city"] == "Austin"
    assert payload["review_labels"] == [f.label for f in engagement_fields()]
    assert payload["filename"].endswith(".docx")
    assert rows[0]["actor"]


async def test_identity_comes_from_the_repository_not_the_request(client, template):
    """A tampered or stale identity block in the payload must not reach a contract.

    Same reasoning as the draw-invoice path recomputing rather than trusting
    what the browser had.
    """
    created = await client.post("/contracts/clients", json=ACME)

    res = await client.post(
        "/contracts/tm/draft",
        json={
            "client_id": created.json()["id"],
            # Ignored: `client_id` is authoritative, and sending both is 422
            # anyway. This asserts the values, not the validation.
            "engagement": _engagement(),
        },
    )
    text = _text(res.content)
    assert "Acme Industries, LLC" in text
    assert "78701" in text


async def test_inline_counterparty_needs_no_saved_record(client, template):
    """A prospect being contracted for the first time."""
    res = await client.post(
        "/contracts/tm/draft",
        json={"client": ACME, "engagement": _engagement()},
    )
    assert res.status_code == 200
    assert "Acme Industries, LLC" in _text(res.content)
    # Nothing was filed.
    assert (await client.get("/contracts/clients")).json() == []


async def test_save_client_files_the_counterparty_for_next_time(client, template):
    res = await client.post(
        "/contracts/tm/draft",
        json={"client": ACME, "save_client": True, "engagement": _engagement()},
    )
    assert res.status_code == 200

    saved = (await client.get("/contracts/clients")).json()
    assert [c["legal_entity_name"] for c in saved] == ["Acme Industries, LLC"]
    assert saved[0]["postal_code"] == "78701"


async def test_save_client_conflict_fails_before_generating(client, template):
    """A duplicate must 409, not hand back a file and then fail to file it."""
    await client.post("/contracts/clients", json=ACME)

    res = await client.post(
        "/contracts/tm/draft",
        json={"client": ACME, "save_client": True, "engagement": _engagement()},
    )
    assert res.status_code == 409

    pool = await get_pool()
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE event_type = 'contract.draft.generated'"
    ) == 0


async def test_missing_template_is_503_not_500(client, tmp_path, monkeypatch):
    """The request was valid and the code is fine — the deployment lost an asset."""
    monkeypatch.setattr(render_module, "TM_TEMPLATE", tmp_path / "absent.docx")
    res = await client.post(
        "/contracts/tm/draft", json={"client": ACME, "engagement": _engagement()}
    )
    assert res.status_code == 503


# ── Auth ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/contracts/clients", None),
        ("post", "/contracts/clients", ACME),
        ("get", "/contracts/tm/fields", None),
        ("post", "/contracts/tm/preview", {"client": ACME}),
        ("post", "/contracts/tm/draft", {"client": ACME}),
    ],
)
async def test_every_route_requires_auth(unauthed_client, method, path, body):
    """Unbreakable Rule 2 — only /healthz is open."""
    call = getattr(unauthed_client, method)
    res = await call(path) if body is None else await call(path, json=body)
    assert res.status_code in (401, 403), f"{method.upper()} {path} was reachable"


# ── CORS ────────────────────────────────────────────────────────────────────

def test_download_headers_are_exposed_to_the_browser():
    """Without `expose_headers` the browser hides these from `fetch`.

    No error, no log line — `res.headers` simply does not contain them, which
    is the same silent-in-the-browser class of failure that
    test_cors_allows_every_route_method.py exists to catch.
    """
    from fastapi.middleware.cors import CORSMiddleware

    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            exposed = set(middleware.kwargs.get("expose_headers", []))
            break
    else:
        raise AssertionError("CORSMiddleware is not installed")

    assert {"Content-Disposition", "X-Contract-Review-Fields"} <= exposed
