import json
import logging

from app.agents.registry import AGENT_REGISTRY
from app.db import get_pool

logger = logging.getLogger(__name__)


async def seed_agents() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for slug, fields in AGENT_REGISTRY.items():
            await conn.execute(
                """
                insert into agents (slug, name, description, requires_approval, allowed_tools, config)
                values ($1, $2, $3, $4, $5, $6)
                on conflict (slug) do update set
                    name              = excluded.name,
                    description       = excluded.description,
                    requires_approval = excluded.requires_approval,
                    allowed_tools     = excluded.allowed_tools,
                    config            = excluded.config,
                    updated_at        = now()
                """,
                slug,
                fields["name"],
                fields["description"],
                fields["requires_approval"],
                json.dumps(fields["allowed_tools"]),
                json.dumps(fields["config"]),
            )
            logger.debug("seeded agent: %s", slug)

    logger.info("agent registry seeded (%d agents)", len(AGENT_REGISTRY))
