import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def execute(conn: asyncpg.Connection, action: dict[str, Any]) -> dict[str, Any]:
    logger.info(
        "stub execution: action_id=%s type=%s",
        action["id"],
        action["action_type"],
    )
    return {"stub": True}
