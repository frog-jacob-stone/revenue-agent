import logging

from app.agents.registry import AGENTS
from app.db import get_pool

logger = logging.getLogger(__name__)


async def seed_agents() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for cls in AGENTS:
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
                cls.slug,
                cls.name,
                cls.description,
                cls.requires_approval,
                list(cls.allowed_tools),
                dict(cls.default_config),
            )
            logger.debug("seeded agent: %s", cls.slug)

    logger.info("agent registry seeded (%d agents)", len(AGENTS))
