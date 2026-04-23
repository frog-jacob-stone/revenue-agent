import logging
from typing import Any

import asyncpg

from app.config import settings
from app.integrations import airtable

logger = logging.getLogger(__name__)


async def execute(conn: asyncpg.Connection, action: dict[str, Any]) -> dict[str, Any]:
    action_type = action["action_type"]
    logger.info("executing action_id=%s type=%s", action["id"], action_type)

    match action_type:
        case "write_rev_rec":
            payload = action.get("executed_payload") or action.get("proposed_payload", {})
            entries = payload.get("entries", [])
            # Strip internal fields that start with _ before writing to Airtable
            clean_entries = [
                {k: v for k, v in e.items() if not k.startswith("_")}
                for e in entries
            ]
            records = await airtable.create_revenue_records(settings, clean_entries)
            logger.info(
                "wrote %d revenue records to Airtable for %s",
                len(records),
                payload.get("date_recognized"),
            )
            return {
                "records_created": len(records),
                "airtable_ids": [r["id"] for r in records],
            }

        case "configure_rev_rec_projects":
            # Human has fixed the Airtable data — re-trigger recognition
            from app.services import agent_runner  # local import avoids circular dep

            payload = action.get("proposed_payload", {})
            ctx = payload.get("context", {})
            ctx["date_recognized"] = payload.get("date_recognized")
            result = await agent_runner.run_agent(
                slug="revenue-recognition",
                initiated_by=action.get("approved_by") or "system",
                context=ctx,
            )
            logger.info("re-triggered revenue-recognition: workflow=%s", result.get("workflow_id"))
            return {"re_triggered": True, "workflow_id": result.get("workflow_id")}

        case _:
            logger.info("no executor for action type %r — skipping", action_type)
            return {"stub": True}
