"""Contract drafting models."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.common import ORMBase

# Long enough for a genuine legal entity name ("... Holdings International
# Limited Liability Company") without letting a paste accident become a
# paragraph in a contract.
_NAME_MAX = 200
_LINE_MAX = 200
_SHORT_MAX = 60


class ContractClient(ORMBase):
    """A saved counterparty: who they are, in the words a contract needs.

    Every field but the name is optional. A prospect known only by legal name is
    worth saving — the gaps render as `[REVIEW: …]` markers in the draft rather
    than disappearing.
    """

    id: UUID
    legal_entity_name: str
    address_line1: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    signatory_name: str | None
    msa_effective_date: str | None
    created_at: datetime
    updated_at: datetime
    created_by: str
    updated_by: str


class ContractClientInput(BaseModel):
    """Create a saved client. Only the legal entity name is required."""

    legal_entity_name: str = Field(
        min_length=1,
        max_length=_NAME_MAX,
        description=(
            "As it appears in the agreement — 'Acme Industries, LLC', not "
            "'Acme'. Also the label the operator picks from, so it is unique."
        ),
    )
    address_line1: str | None = Field(
        None,
        max_length=_LINE_MAX,
        description="Street address, suite included — the template has one line.",
    )
    city: str | None = Field(None, max_length=_SHORT_MAX)
    state: str | None = Field(None, max_length=_SHORT_MAX)
    postal_code: str | None = Field(None, max_length=_SHORT_MAX)
    signatory_name: str | None = Field(None, max_length=_NAME_MAX)
    msa_effective_date: str | None = Field(
        None,
        max_length=_SHORT_MAX,
        description=(
            "Effective date of the client's MSA, worded as it should read in "
            "the document. Free text, not a date, so the operator controls the "
            "format. Null when there is no MSA."
        ),
    )


class ContractClientPatch(BaseModel):
    """Edit a saved client.

    True PATCH semantics, matching the billing-settings convention: an omitted
    field is left alone, an empty string clears it. The distinction is not
    academic here — the edit form and a targeted "fix the ZIP" call are both
    legitimate callers, and a partial body must not blank the fields it does not
    mention.
    """

    legal_entity_name: str | None = Field(None, min_length=1, max_length=_NAME_MAX)
    address_line1: str | None = Field(None, max_length=_LINE_MAX)
    city: str | None = Field(None, max_length=_SHORT_MAX)
    state: str | None = Field(None, max_length=_SHORT_MAX)
    postal_code: str | None = Field(None, max_length=_SHORT_MAX)
    signatory_name: str | None = Field(None, max_length=_NAME_MAX)
    msa_effective_date: str | None = Field(None, max_length=_SHORT_MAX)


class ContractFieldSpec(BaseModel):
    """One blank the form has to ask about.

    Served so the UI renders labels and ordering from the same declaration the
    renderer uses, rather than a second copy that drifts.
    """

    tag: str
    label: str
    source: str
    client_field: str | None = None
    """The saved-client field backing this tag, for `source="client"` only.

    Served so the form can map a tag to the record field it reads without
    keeping its own copy of the mapping — a second copy is a second thing to
    update when a tag is renamed, and the one that would be forgotten.
    """


class TmDraftRequest(BaseModel):
    """Generate a T&M draft.

    The counterparty arrives one of two ways and exactly one must be used:

    `client_id`      a saved record, read server-side. Nothing about the
                     identity is taken from the request, so what goes into the
                     contract is what the repository holds.
    `client` inline  a counterparty being contracted for the first time.
                     `save_client` additionally files them for next time,
                     which is the whole point of the repository.

    `engagement` carries the per-contract fields by tag. Unknown tags are
    rejected rather than ignored — a silently dropped value is a blank in a
    contract, and the caller cannot tell it happened.
    """

    client_id: UUID | None = None
    client: ContractClientInput | None = None
    save_client: bool = False
    engagement: dict[str, str | None] = Field(default_factory=dict)


class TmDraftPreview(BaseModel):
    """What generating would produce, without producing it.

    Exists so the operator sees the review list *before* they click, per
    ADR-0004's "the exact payload is shown before the click". The download
    response can only report it afterwards, in a header.
    """

    values: dict[str, str | None]
    review_labels: list[str]
    filename: str
