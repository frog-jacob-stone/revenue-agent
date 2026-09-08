"""What a T&M contract asks for, declared once.

Two facts have to agree and are maintained in different places, so this module
exists to be the seam between them:

  the .docx   knows which tags physically appear in the document. That is a
              fact about the file, discoverable by reading it.
  this module knows what each tag is *called* in front of a human, what order
              the form asks for them in, and whether the value comes from a
              saved client record or is typed per contract. That is judgment,
              and judgment belongs in code.

`tests/test_contract_template_tags.py` asserts the two sets are equal, so
adding a tag in Word without declaring it here fails the build, and so does
declaring a field the document no longer contains. Without that test the failure
mode is silent and bad: a tag nobody declared is simply never filled, and the
draft goes out with a raw `{{ … }}` in it.

Adding a field is therefore always two edits — the .docx and `TM_FIELDS`. See
`templates/README.md`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldSource = Literal["client", "engagement"]


@dataclass(frozen=True)
class ContractField:
    """One blank in a contract template."""

    tag: str
    """The Jinja tag in the .docx, without braces: `{{ tag }}`."""

    label: str
    """What a human calls it.

    Shown on the form, and — because it is what appears inside the
    `[REVIEW: …]` marker when the field is left empty — it also has to read
    sensibly on the page of a contract someone is about to negotiate. "Effective
    date" rather than "eff_dt".
    """

    source: FieldSource
    """Where the value comes from.

    `client`     from the saved `contract_clients` record, so an existing client
                 is picked rather than retyped.
    `engagement` typed for this contract. Nothing durable to remember it by —
                 an effective date belongs to one agreement, not to a client.
    """

    client_column: str | None = None
    """The `contract_clients` column backing this field.

    Set for every `source="client"` field and for no other. A column rather than
    a naming convention, because the tag is namespaced for the template author's
    benefit (`client_legal_name` reads better in a document than
    `legal_entity_name`) and the two vocabularies should be free to differ.
    """

    def __post_init__(self) -> None:
        # A `client` field with no column silently renders blank forever, which
        # looks exactly like "the operator left it empty" and would be found by
        # nobody. Cheap to assert at import.
        if (self.source == "client") != (self.client_column is not None):
            raise ValueError(
                f"contract field {self.tag!r}: source={self.source!r} and "
                f"client_column={self.client_column!r} disagree — every "
                f"'client' field needs a column and no other field may have one"
            )


# Declared in the order the form asks for them. Tag names are the template's,
# not this system's — `client_street` and `client_zip` are what the .docx says,
# and `client_column` bridges to the repository's own vocabulary rather than
# forcing either side to rename.
#
# The business sponsor and product owner are `engagement`, not `client`: they
# are the client's people on *this* project and they change between
# engagements, so remembering them against the company would be wrong more
# often than it saved a keystroke.
TM_FIELDS: tuple[ContractField, ...] = (
    # ── The counterparty, from the saved record ──────────────────────────────
    ContractField(
        "client_legal_name", "Client legal entity name", "client",
        client_column="legal_entity_name",
    ),
    ContractField(
        "client_street", "Client street address", "client",
        client_column="address_line1",
    ),
    ContractField("client_city", "Client city", "client", client_column="city"),
    ContractField("client_state", "Client state", "client", client_column="state"),
    ContractField(
        "client_zip", "Client ZIP", "client", client_column="postal_code",
    ),
    ContractField(
        "client_signatory", "Client signatory", "client",
        client_column="signatory_name",
    ),
    # A fact about the relationship, not the engagement: every SOW written under
    # the same MSA names the same date, so it is saved against the client rather
    # than typed each time. Null for a client with no MSA, which a first
    # engagement usually is.
    ContractField(
        "msa_effective_date", "MSA effective date", "client",
        client_column="msa_effective_date",
    ),
    # ── This engagement, typed each time ─────────────────────────────────────
    ContractField("sow_date", "SOW date", "engagement"),
    ContractField(
        "client_business_sponsor", "Client business sponsor", "engagement",
    ),
    ContractField("client_product_owner", "Client product owner", "engagement"),
)

TM_FIELDS_BY_TAG: dict[str, ContractField] = {f.tag: f for f in TM_FIELDS}

if len(TM_FIELDS_BY_TAG) != len(TM_FIELDS):
    raise ValueError("duplicate tag in TM_FIELDS")


def client_fields() -> tuple[ContractField, ...]:
    """Fields populated from a saved `contract_clients` row."""
    return tuple(f for f in TM_FIELDS if f.source == "client")


def engagement_fields() -> tuple[ContractField, ...]:
    """Fields typed per contract."""
    return tuple(f for f in TM_FIELDS if f.source == "engagement")


def review_marker(label: str) -> str:
    """How an unfilled field appears in the document.

    Square-bracketed and prefixed, so it is greppable in the rendered file and
    findable with Ctrl+F in Word. `render` additionally highlights it yellow;
    this is the half that survives even if the highlighting does not.
    """
    return f"[REVIEW: {label}]"
