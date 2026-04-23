"""HubSpot integration stub — real implementation in the next sprint."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_company(company_id: str) -> dict[str, Any]:
    raise NotImplementedError("HubSpot integration not yet implemented")


async def create_contact(payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError("HubSpot integration not yet implemented")


async def update_deal(deal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError("HubSpot integration not yet implemented")
