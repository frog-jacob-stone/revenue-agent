import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def execute(conn: asyncpg.Connection, action: dict[str, Any]) -> dict[str, Any]:
    """Legacy execution dispatcher for non-orchestrated workflows only.

    Orchestrated workflows (those with `workflows.pattern` set) bypass this
    function — see `app/services/approval.py`. After the rev rec migration and
    the invoice removal, no production agents emit non-orchestrated actions.
    The dispatcher remains as a no-op fallback so legacy approval flows still
    have somewhere to land if a future agent is added without using a chain.
    """
    action_type = action["action_type"]
    logger.info("execute (legacy) action_id=%s type=%r — no-op", action["id"], action_type)
    return {"stub": True, "note": "no legacy executor registered"}
