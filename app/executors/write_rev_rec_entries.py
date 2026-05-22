"""write_rev_rec_entries executor (ADR-0002, plan 18).

Runs after a human approves a `trigger_revenue_recognition` proposal.
Writes the (possibly human-edited) entries to Airtable, stripping
underscore-prefixed scratch fields the inbox surfaces for context.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.executors.base import ExecutorContext, ExecutorDefinition
from app.integrations import airtable

logger = logging.getLogger(__name__)


async def _write_rev_rec_entries(
    ctx: ExecutorContext, payload: dict[str, Any]
) -> dict[str, Any]:
    raw_entries = payload.get("entries") or []
    clean_entries = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in raw_entries
    ]
    records = await airtable.create_revenue_records(settings, clean_entries)
    logger.info(
        "write_rev_rec_entries: wrote %d records to Airtable for %s",
        len(records), payload.get("date_recognized"),
    )
    return {
        "records_created": len(records),
        "airtable_ids": [r["id"] for r in records],
    }


WRITE_REV_REC_ENTRIES = ExecutorDefinition(
    name="write_rev_rec_entries",
    description=(
        "Write the approved revenue recognition entries to Airtable after "
        "human review."
    ),
    execute=_write_rev_rec_entries,
)
