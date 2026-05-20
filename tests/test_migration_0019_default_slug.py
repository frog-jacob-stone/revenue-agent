"""Migration 0019 must give chat_sessions.agent_slug a default of 'revenue-ops'."""
import asyncpg
import pytest


async def test_default_slug_is_revenue_ops(_test_pool: asyncpg.Pool):
    """Inserting without agent_slug should pick up the migration's DEFAULT."""
    row = await _test_pool.fetchrow(
        "INSERT INTO chat_sessions DEFAULT VALUES RETURNING agent_slug"
    )
    assert row["agent_slug"] == "revenue-ops"
