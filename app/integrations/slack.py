"""Slack integration stub — used for approval-inbox fallback notifications."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def post_approval_request(action_id: str, summary: str, channel: str | None = None) -> dict[str, Any]:
    """Notify a Slack channel that an action is waiting for approval."""
    raise NotImplementedError("Slack integration not yet implemented")


async def post_message(channel: str, text: str, **kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Slack integration not yet implemented")
