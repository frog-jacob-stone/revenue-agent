"""Did Harvest actually mark the time billed?

A T&M invoice is created with `line_items_import`, and that is the whole reason
this system cannot produce the failure it was most at risk of: billing a client
for time that Harvest still lists as uninvoiced, so it gets billed again next
month. Harvest generates the line items from the unbilled entries itself and
stamps each one `is_billed`, with a back-reference to the invoice. `is_billed`
is read-only on the API — there is no other way to set it, and no free-form
`line_items` body will ever set it.

That is Harvest's behaviour, not ours, and until now nothing looked. This
module looks: immediately after a successful create, count the billable time
still unbilled for the projects and period the invoice just covered.

**It is an observation, not a gate.** Zero is the expected reading. Non-zero
has legitimate causes — time Harvest's own approval workflow excluded, entries
with no resolvable rate — as well as one illegitimate one, which is the point.
So the caller records the number and shows it, and never lets it turn a
successfully created invoice into a failed run.
"""
from __future__ import annotations

import logging
from datetime import date

from app.config import Settings
from app.integrations import harvest
from app.services.billing import rates

logger = logging.getLogger(__name__)


async def count_unbilled_after(
    cfg: Settings,
    *,
    project_ids: list[int],
    period_start: date,
    period_end: date,
) -> tuple[float, int]:
    """`(hours, entry_count)` still billable-and-unbilled over the billed period.

    Uses `rates.is_uninvoiced_billable` — the same predicate the estimator
    counts with — so a leftover here means exactly what a straggler means, just
    measured after the fact instead of before it.
    """
    hours = 0.0
    entries = 0
    for project_id in project_ids:
        for entry in await harvest.list_time_entries(
            cfg,
            project_id=project_id,
            from_=period_start.isoformat(),
            to=period_end.isoformat(),
        ):
            if not rates.is_uninvoiced_billable(entry):
                continue
            hours += rates.effective_hours(entry)
            entries += 1
    return round(hours, 2), entries


def imported_project_ids(payload: dict) -> list[int]:
    """The projects a `line_items_import` body drew time from, or `[]`.

    Empty for a free-form `line_items` payload — a `recurring_monthly` invoice
    imports no time, so there is nothing for Harvest to have marked and nothing
    to check. Checking it anyway would report every hour on a retainer project
    as a leftover, every month.
    """
    import_block = payload.get("line_items_import") or {}
    if not import_block.get("time"):
        return []
    return [int(p) for p in import_block.get("project_ids") or []]
