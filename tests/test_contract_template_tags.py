"""The .docx and `TM_FIELDS` must describe the same set of blanks.

This is the guardrail the contract feature rests on, and it is the same kind of
structural test as `test_no_agent_approval_tools.py`: it asserts a property
nothing else can observe.

The failure it prevents is silent and expensive. A tag added in Word but not
declared here is never put in the render context, so Jinja resolves it to
nothing and the draft goes out with that clause blank — no error, no log line,
and nobody notices until a client reads it. A field declared here but no longer
in the document is the milder inverse: the form asks for a value that goes
nowhere.

Skipped rather than failed when the template is absent. It is a versioned repo
asset supplied by the operator, and until it lands there is nothing to compare
against — a red suite would say "this code is broken" when the truth is "the
document has not been provided yet".
"""
from __future__ import annotations

import pytest

from app.services.contracts.fields import TM_FIELDS
from app.services.contracts.render import TM_TEMPLATE, template_tags


def test_declared_fields_match_template_tags():
    if not TM_TEMPLATE.exists():
        pytest.skip(
            f"No contract template at {TM_TEMPLATE} yet — see "
            f"app/services/contracts/templates/README.md for the tagging "
            f"convention. This test becomes authoritative the moment it lands."
        )

    in_document = template_tags()
    declared = {f.tag for f in TM_FIELDS}

    undeclared = sorted(in_document - declared)
    missing = sorted(declared - in_document)

    assert not undeclared and not missing, "\n".join(
        part
        for part in (
            "The .docx template and TM_FIELDS disagree about which blanks exist.",
            (
                f"In the .docx but not declared in fields.py (these render "
                f"BLANK in the contract, silently): {', '.join(undeclared)}"
                if undeclared
                else ""
            ),
            (
                f"Declared in fields.py but not in the .docx (the form asks for "
                f"a value that goes nowhere): {', '.join(missing)}"
                if missing
                else ""
            ),
            "Fix by editing the template in Word, fields.py, or both.",
        )
        if part
    )


def test_every_tag_is_a_bare_field_name():
    """No filters, no conditionals, no spaces — one field name per tag.

    The normalizing loader raises on anything else, so this asserts the
    template as shipped actually loads. Kept separate from the set-equality
    test above because the failure is different in kind: that one means the two
    declarations drifted, this one means the document cannot be rendered at all.
    """
    if not TM_TEMPLATE.exists():
        pytest.skip("No contract template yet")

    # Raises TemplateTagInvalid with the offending tag if the document has one.
    assert template_tags(), "the template declares no tags at all"
