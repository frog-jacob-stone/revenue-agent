"""Gmail / Google Workspace integration stub — real implementation in a future sprint."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body: str, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Gmail integration not yet implemented")


async def get_thread(thread_id: str) -> dict[str, Any]:
    raise NotImplementedError("Gmail integration not yet implemented")
