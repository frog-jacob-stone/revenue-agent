"""Migration 0023 renames the chat_sessions.agent_slug default to 'chief-of-staff'.

(Migration 0019 first set the default to 'revenue-ops'; 0023 rewires both the
default and historical rows after the front-door rename.)
"""
import asyncpg


async def test_default_slug_is_chief_of_staff(_test_pool: asyncpg.Pool):
    """Inserting without agent_slug should pick up the migration's DEFAULT."""
    row = await _test_pool.fetchrow(
        "INSERT INTO chat_sessions DEFAULT VALUES RETURNING agent_slug"
    )
    assert row["agent_slug"] == "chief-of-staff"
