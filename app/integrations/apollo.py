"""Apollo.io integration stub — real implementation in the next sprint."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def search_people(filters: dict[str, Any]) -> list[dict[str, Any]]:
    raise NotImplementedError("Apollo integration not yet implemented")


async def enrich_contact(email: str) -> dict[str, Any]:
    raise NotImplementedError("Apollo integration not yet implemented")
