# Contract templates

Word documents rendered by `app/services/contracts/render.py`. One file per
contract type. Today there is one: `tm_agreement.docx`.

These are versioned repo assets, not uploads. `git log` on this directory is the
history of what wording went out, which is the point.

## Tagging convention

Replace each blank in the document with a Jinja tag:

```
This Agreement is entered into by Frogslayer, LLC and {{ client_legal_name }},
with offices at {{ client_address_line1 }}, {{ client_city }}, {{ client_state }}
{{ client_postal_code }}.
```

Rules, all of them load-bearing:

- **Two braces, spaces inside, no `r` prefix.** `{{ client_legal_name }}`.
- **Never `{{r … }}`.** docxtpl's `RichText` replaces the tag's whole run with
  one it builds itself, which discards the formatting the blank sits in — a tag
  inside a bold Times-14pt clause would come back unbolded in the body font.
  A plain tag substitutes *within* the run and keeps bold, italic, font, and
  size exactly.
- **Type each tag in one pass, left to right.** Word splits a paragraph into
  runs as you edit, and a tag split across two runs is invisible to the renderer
  — it will simply never be filled. If a tag is not picked up, delete the whole
  thing and retype it rather than repairing it character by character.
- **Put the tag where the value belongs.** It inherits the surrounding
  formatting, so a value that should be bold goes inside the bold clause.
- Tags work in body text, tables, nested tables, headers, and footers. They do
  **not** work inside text boxes or shapes.
- Leave everything else alone — rate tables, role definitions, and all
  boilerplate are static template text by design and are not modelled as data.

## Adding or renaming a tag

The tag set in the .docx and `TM_FIELDS` in `../fields.py` must match exactly,
and `tests/test_contract_template_tags.py` fails the build if they drift. So a
tag change is two edits, in either order:

1. Edit the .docx in Word.
2. Add, rename, or remove the matching `ContractField` in `../fields.py`,
   giving it a human label — the label is what the operator sees on the form and
   what appears in the `[REVIEW: …]` marker when the field is left blank.

To see what the renderer currently finds in a template:

```bash
python3 -c "from docxtpl import DocxTemplate; \
  print(sorted(DocxTemplate('app/services/contracts/templates/tm_agreement.docx') \
  .get_undeclared_template_variables()))"
```

## Unfilled fields

A blank is a decision, not an error — generating a draft with open terms to
negotiate is the normal case. Any field left empty renders as a yellow
highlighted `[REVIEW: <label>]` in the document, so it is visible on the page
and findable with Ctrl+F. Generation never refuses because a field is empty.
