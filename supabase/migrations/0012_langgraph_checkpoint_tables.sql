-- Migration 0012 — LangGraph checkpoint tables (marker only)
--
-- The actual DDL for `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`,
-- and `checkpoint_migrations` is owned by `langgraph-checkpoint-postgres`.
-- The runner calls `AsyncPostgresSaver.setup()` at app startup, which creates
-- (and migrates) those tables idempotently against the same Postgres
-- instance the rest of the app uses.
--
-- This file exists so the migrations ledger is truthful — Phase 1 of the
-- LangGraph rearchitecture introduced LangGraph-managed schema into the
-- shared database. See:
--   .agent/plans/3.langgraph-multi-agent-rearchitecture.md  (master plan)
--   .agent/plans/5.path-b-phase-1-content-publish.md         (this phase)
--
-- DO NOT add custom DDL here. If LangGraph schema needs schema changes that
-- are NOT covered by `setup()`, add a new migration (0013+) that runs after
-- the LangGraph tables exist.

select 1;  -- noop; conftest tolerates empty migrations but not zero statements
