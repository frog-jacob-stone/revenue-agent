"""Verify the RLS lock-down covers every table the Supabase advisor flagged."""

import asyncpg
import pytest

from app.db_security import LANGGRAPH_TABLES, lock_down_langgraph_tables


@pytest.fixture
async def _checkpoint_tables(_test_pool: asyncpg.Pool):
    """Stand in for LangGraph's AsyncPostgresSaver.setup(): create the four
    checkpoint tables so lock_down_langgraph_tables has something to operate on.

    Earlier tests in the session may have triggered `runner.init()` →
    `AsyncPostgresSaver.setup()`, which creates the real LangGraph schema.
    Only drop the tables this fixture actually created — otherwise teardown
    wipes the real schema and every subsequent runner-using test fails with
    `relation "checkpoints" does not exist`.
    """
    created: list[str] = []
    async with _test_pool.acquire() as conn:
        for t in LANGGRAPH_TABLES:
            exists = await conn.fetchval(
                "select to_regclass($1) is not null",
                f'public."{t}"',
            )
            if exists:
                continue
            await conn.execute(
                f'create table public."{t}" (id serial primary key)'
            )
            created.append(t)
    try:
        yield
    finally:
        async with _test_pool.acquire() as conn:
            for t in created:
                await conn.execute(f'drop table if exists public."{t}" cascade')


async def test_migration_tables_have_rls_enabled(_test_pool: asyncpg.Pool):
    """approvals + agent_messages must have RLS enabled by migration 0018."""
    rows = await _test_pool.fetch(
        """
        select tablename, rowsecurity
        from pg_tables
        where schemaname = 'public'
          and tablename = any($1::text[])
        """,
        ["approvals", "agent_messages"],
    )
    by_name = {r["tablename"]: r["rowsecurity"] for r in rows}
    assert by_name.get("approvals") is True, "approvals must have RLS on"
    assert by_name.get("agent_messages") is True, "agent_messages must have RLS on"


async def test_migration_tables_have_service_role_policy(_test_pool: asyncpg.Pool):
    rows = await _test_pool.fetch(
        """
        select tablename, policyname
        from pg_policies
        where schemaname = 'public'
          and tablename = any($1::text[])
        """,
        ["approvals", "agent_messages"],
    )
    table_to_policies = {}
    for r in rows:
        table_to_policies.setdefault(r["tablename"], []).append(r["policyname"])
    assert "approvals" in table_to_policies, "approvals missing policy"
    assert "agent_messages" in table_to_policies, "agent_messages missing policy"


async def test_lock_down_langgraph_tables_is_idempotent(
    _test_pool: asyncpg.Pool, _checkpoint_tables
):
    await lock_down_langgraph_tables(_test_pool)
    # Second call must not error.
    await lock_down_langgraph_tables(_test_pool)

    rows = await _test_pool.fetch(
        """
        select tablename, rowsecurity
        from pg_tables
        where schemaname = 'public'
          and tablename = any($1::text[])
        """,
        list(LANGGRAPH_TABLES),
    )
    by_name = {r["tablename"]: r["rowsecurity"] for r in rows}
    for t in LANGGRAPH_TABLES:
        assert by_name.get(t) is True, f"{t} must have RLS on after lock-down"

    policy_rows = await _test_pool.fetch(
        """
        select tablename, policyname
        from pg_policies
        where schemaname = 'public'
          and tablename = any($1::text[])
        """,
        list(LANGGRAPH_TABLES),
    )
    table_to_policies = {r["tablename"]: r["policyname"] for r in policy_rows}
    for t in LANGGRAPH_TABLES:
        assert table_to_policies.get(t) == f"{t}_service_all", (
            f"{t} missing service-role policy"
        )


async def test_lock_down_skips_missing_tables(_test_pool: asyncpg.Pool):
    """If the LangGraph tables don't exist yet, lock-down should warn and skip
    rather than raise."""
    await lock_down_langgraph_tables(_test_pool)
