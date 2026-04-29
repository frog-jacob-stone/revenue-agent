from typing import Any

from app.config import settings
from app.integrations import harvest


async def list_active_clients() -> list[dict[str, Any]]:
    """Return active Harvest clients as `{id, name}` rows."""
    clients = await harvest.get_clients(settings)
    return [{"id": c["id"], "name": c["name"]} for c in clients]
