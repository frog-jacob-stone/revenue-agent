"""Startup seeding.

Only `agents` needs seeding: the Python registry is the source of truth for agent
identity (see migration `0006`), and this reconciles the `agents` table to it so
foreign keys from `approvals`, `audit_log`, and `memories` resolve.

There used to be a `seed_voice_profile()` here that wrote the Frogslayer outbound
voice profile into `memories` for a `voice-critic` agent. That agent left the
registry with the LangGraph rip-out, so the function had been looking up a slug
that does not exist and logging a warning on every boot. It is gone; the voice
guidance it held now lives in `BDRAgent`'s system prompt, which is the only place
outreach gets drafted.
"""
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
                insert into agents (slug)
                values ($1)
                on conflict (slug) do update set
                    updated_at = now()
                """,
                cls.slug,
            )
            logger.debug("seeded agent: %s", cls.slug)

    logger.info("agent registry seeded (%d agents)", len(AGENTS))
