from typing import Any

from app.config import settings
from app.integrations import harvest


async def list_active_projects_for_client(client_id: int) -> list[dict[str, Any]]:
    """Return the active Harvest projects belonging to a single client."""
    all_projects = await harvest.get_active_projects(settings)
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "bill_by": p.get("bill_by"),
            "budget": p.get("budget"),
        }
        for p in all_projects
        if (p.get("client") or {}).get("id") == client_id
    ]
