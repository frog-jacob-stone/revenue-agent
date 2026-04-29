import logging
from typing import Any

import asyncpg

from app.config import settings
from app.integrations import airtable, harvest

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
            from app.agents.revenue_recognition import RevenueRecognitionAgent

            payload = action.get("proposed_payload", {})
            ctx = payload.get("context", {})
            ctx["date_recognized"] = payload.get("date_recognized")
            result = await RevenueRecognitionAgent.trigger(
                context=ctx,
                initiated_by=action.get("approved_by") or "system",
            )
            logger.info(
                "re-triggered %s: workflow=%s",
                RevenueRecognitionAgent.slug,
                result.get("workflow_id"),
            )
            return {"re_triggered": True, "workflow_id": result.get("workflow_id")}

        case "generate_invoice":
            # v1 stub — enable by implementing harvest.generate_invoice()
            payload = action.get("proposed_payload", {})
            raise NotImplementedError(
                f"generate_invoice is not active in v1 (invoice_id={payload.get('invoice_id')}). "
                "Enable by removing the NotImplementedError in harvest.generate_invoice()."
            )

        case "send_invoice":
            # v1 stub — enable by implementing harvest.send_invoice()
            payload = action.get("proposed_payload", {})
            raise NotImplementedError(
                f"send_invoice is not active in v1 (invoice_id={payload.get('invoice_id')}). "
                "Enable by removing the NotImplementedError in harvest.send_invoice()."
            )

        case "delete_invoice":
            # v1 stub — enable by implementing harvest.delete_invoice()
            payload = action.get("proposed_payload", {})
            raise NotImplementedError(
                f"delete_invoice is not active in v1 (invoice_id={payload.get('invoice_id')}). "
                "Enable by removing the NotImplementedError in harvest.delete_invoice()."
            )

        case _:
            logger.info("no executor for action type %r — skipping", action_type)
            return {"stub": True}
