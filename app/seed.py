import logging

from app.agents.registry import AGENTS
from app.db import get_pool

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Voice profile (consumed by the Voice Critic in the Outreach chain).
# Lives in `memories` so it can be edited at runtime without a deploy. Re-running
# the seed is safe: we look up by the `kind=voice_profile` metadata sentinel.
# -----------------------------------------------------------------------------

_VOICE_PROFILE_TEXT = """\
Frogslayer voice profile — outbound emails.

DO:
- Lead with a specific, recent observation about the company (funding round,
  product launch, hiring spike, leadership change). Be concrete; show you read.
- Tie that observation to a Frogslayer capability the recipient could plausibly
  use right now (product factory delivery model, ops/data systems, managed
  services tier). One sentence is enough.
- End with one low-friction ask. A 15-minute call this Thursday or Friday is
  ideal. Always offer a window, never an open-ended "let me know."

DO NOT:
- Open with "Hi <name>", "Hope this finds you well", "Just wanted to reach out".
- Use the word "synergy", "leverage", "unlock", or "circle back".
- Congratulate on the funding round directly ("Congrats on the round!"). It is
  fine to reference the round as a signal.
- Pitch the firm in more than one sentence. The reader knows what an agency is.
- Send anything longer than 90 words.

EXAMPLES (good):

Subject: Backend ramp at Acme
Body:
Saw the Series B and the 12 open backend roles. Frogslayer's product factory
model has helped a few B2B SaaS teams ship customer-facing platforms without
growing the in-house team — usually saves 6–9 months versus hiring first.
Worth 15 minutes Thursday or Friday to compare notes?

Subject: Following the Kestrel acquisition
Body:
Watched the Kestrel deal close last week — congrats to the M&A team, separately.
Post-close integration is exactly where the platform team historically gets
buried. Frogslayer runs delivery for two firms in similar spots; happy to share
what we've seen if there's 15 minutes Thursday or Friday.

Subject: Nora's piece on data observability
Body:
Read Nora's post on the OpenLineage rollout — the bit about reconciling
Snowflake and Databricks lineage was sharp. We see the same gap with most B2B
SaaS clients. If you're scoping a real rollout in Q3, 15 minutes Thursday or
Friday to compare notes?
"""


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


async def seed_voice_profile() -> None:
    """Insert the voice profile preference memory for the Voice Critic.

    Idempotent via the `metadata->>'kind' = 'voice_profile'` sentinel — only one
    such memory should ever exist per agent. Subsequent boots leave any
    user-edited content alone.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        agent_id = await conn.fetchval(
            "SELECT id FROM agents WHERE slug = 'voice-critic'"
        )
        if agent_id is None:
            logger.warning("voice-critic agent not found; skipping voice profile seed")
            return

        existing = await conn.fetchval(
            """
            SELECT id FROM memories
            WHERE agent_id = $1
              AND kind = 'preference'
              AND metadata->>'kind' = 'voice_profile'
            LIMIT 1
            """,
            agent_id,
        )
        if existing:
            return

        # Pass metadata as a dict; the asyncpg jsonb codec encodes it.
        await conn.execute(
            """
            INSERT INTO memories (agent_id, kind, scope, content, metadata)
            VALUES ($1, 'preference', 'global', $2, $3)
            """,
            agent_id,
            _VOICE_PROFILE_TEXT,
            {"kind": "voice_profile", "version": 1},
        )
        logger.info("seeded voice profile memory for voice-critic")
