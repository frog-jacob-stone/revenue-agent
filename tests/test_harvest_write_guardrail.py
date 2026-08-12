"""Constraints C1 and C4, enforced in CI rather than by comment.

The system creates Harvest *draft* invoices and stops. It never sends, never
deletes, and never modifies an existing invoice. Those are the operations that
would put wrong money in front of a client, and no code path may reach them.

This scans source rather than mocking a client because the guarantee we want is
"the call does not exist anywhere", not "the call wasn't made on this path".
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_APP = Path(__file__).parent.parent / "app"

# Lines matching these are a build failure. Each maps to a PRD §4.9 prohibition.
_FORBIDDEN: list[tuple[str, str]] = [
    (r"/invoices/[^\"']*/messages",
     "POST /v2/invoices/{id}/messages — sending an invoice to a client (C1)"),
    (r"\.delete\s*\([^)]*invoices",
     "DELETE /v2/invoices/{id} — deleting an invoice (C4)"),
    (r"\.patch\s*\([^)]*invoices",
     "PATCH /v2/invoices/{id} — modifying an existing invoice (C4)"),
    (r"/invoices/[^\"']*/payments",
     "POST /v2/invoices/{id}/payments — recording a payment"),
    (r"\bretainer_id\b",
     "retainer_id — Harvest's first-class retainer object must not be touched"),
]


def _source_files() -> list[Path]:
    return [p for p in _APP.rglob("*.py") if "__pycache__" not in p.parts]


def _strip_comments(line: str) -> str:
    """Drop trailing comments so the guardrail docs in harvest.py don't self-trip."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:i]
    return line


@pytest.mark.parametrize("pattern,description", _FORBIDDEN)
def test_forbidden_harvest_endpoint_absent(pattern: str, description: str) -> None:
    regex = re.compile(pattern, re.IGNORECASE)
    offenders: list[str] = []

    for path in _source_files():
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            line = _strip_comments(raw)
            if not line.strip() or line.lstrip().startswith(("-", "*")):
                continue
            if regex.search(line):
                rel = path.relative_to(_APP.parent)
                offenders.append(f"  {rel}:{lineno}: {raw.strip()}")

    assert not offenders, (
        f"Forbidden Harvest operation found — {description}\n"
        + "\n".join(offenders)
        + "\n\nSee docs/prd/harvest-invoicing-requirements.md §4.9."
    )


def test_guardrail_actually_scans_something() -> None:
    """Guard the guard: a bad path glob would make every test above pass."""
    files = _source_files()
    assert len(files) > 20, f"expected to scan the app package, found {len(files)} files"
    assert any(p.name == "harvest.py" for p in files)
