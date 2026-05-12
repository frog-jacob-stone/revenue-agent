"""Runtime DB lock-down for tables that migrations can't touch.

The LangGraph checkpoint tables (checkpoints, checkpoint_blobs,
checkpoint_writes, checkpoint_migrations) are created by
AsyncPostgresSaver.setup() during app startup, so a static migration can't
enable RLS on them — they don't exist when migrations run on a fresh DB.

This module enables RLS and adds a service-role-only policy on each, after
runner.init() has run. The operations are idempotent so reboots are safe.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

LANGGRAPH_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


async def lock_down_langgraph_tables(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for table in LANGGRAPH_TABLES:
            exists = await conn.fetchval(
                "select 1 from pg_tables where schemaname = 'public' and tablename = $1",
                table,
            )
            if not exists:
                logger.warning(
                    "LangGraph table public.%s missing — skipping RLS lock-down", table
                )
                continue
            await conn.execute(
                f'alter table public."{table}" enable row level security'
            )
            policy_name = f"{table}_service_all"
            await conn.execute(
                f"""
                do $$
                begin
                    if not exists (
                        select 1 from pg_policies
                        where schemaname = 'public'
                          and tablename = '{table}'
                          and policyname = '{policy_name}'
                    ) then
                        create policy {policy_name} on public."{table}"
                            for all to service_role
                            using (true) with check (true);
                    end if;
                end$$;
                """
            )
        logger.info("LangGraph checkpoint tables RLS verified")
