"""Contract drafting.

Produces a draft T&M agreement as a Word document by filling the blanks in a
checked-in .docx template. Deterministic — there is no LLM in this path, and no
agent: generation is operator-initiated (ADR-0004), the operator is the one
clicking, and the payload they authorize is the form they just filled.

Four modules, in dependency order:

  `fields`   what a T&M contract asks for, declared once. The single source of
             truth for labels, ordering, and which fields come from a saved
             client record rather than being typed per contract.
  `clients`  the saved-client repository — legal identity only, so an existing
             client does not have to be retyped. Not a CRM, not Harvest.
  `render`   template in, .docx bytes out. Pure; touches no database.
  `drafts`   resolves a request into field values, calls `render`, and writes
             the audit row. The only module here that writes anything.

The template itself is a versioned repo asset under `templates/`. It is not
uploadable and not stored per-tenant: there is one, legal owns its wording, and
changing it is a commit. See `templates/README.md` for the tagging convention
and `fields.py` for why the tag set is enforced against the .docx by test.
"""
