import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def execute(conn: asyncpg.Connection, action: dict[str, Any]) -> dict[str, Any]:
    """Legacy execution dispatcher for non-orchestrated workflows only.

    Orchestrated workflows (those with `workflows.pattern` set) bypass this
    function entirely — see `app/services/approval.py`. Rev rec used to live
    here via `configure_rev_rec_projects` and `write_rev_rec`; it now runs
    through `app/orchestrator/chains/rev_rec.py`.
    """
    action_type = action["action_type"]
    logger.info("executing action_id=%s type=%s", action["id"], action_type)

    match action_type:
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
