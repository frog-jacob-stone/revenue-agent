-- Drop the LangGraph machinery (ADR-0002, plan 19).
--
-- After Phase 4 the system has no graph engine: all prescribed workflows are
-- tools that return Done | AwaitingApproval | Blocked, and approvals carry an
-- executor name rather than a graph workflow_id.
--
-- `workflow_id` columns on approvals / audit_log / agent_messages / memories /
-- llm_calls stay as plain UUID columns for historical audit lookups; the FKs
-- to `workflows` are dropped here. `workflows` itself is dropped, along with
-- the LangGraph checkpoint tables.
begin;

-- 1. Drop LangGraph checkpoint tables. These were created at runtime by
--    `AsyncPostgresSaver.setup()`; nothing now creates or reads them.
drop table if exists checkpoint_writes cascade;
drop table if exists checkpoint_blobs cascade;
drop table if exists checkpoints cascade;
drop table if exists checkpoint_migrations cascade;

-- 2. Drop foreign keys pointing at `workflows`. Columns stay; FKs go.
alter table approvals      drop constraint if exists approvals_workflow_id_fkey;
alter table audit_log      drop constraint if exists audit_log_workflow_id_fkey;
alter table memories       drop constraint if exists memories_source_workflow_id_fkey;
alter table llm_calls      drop constraint if exists llm_calls_workflow_id_fkey;
alter table agent_messages drop constraint if exists agent_messages_workflow_id_fkey;

-- 3. Drop the workflows table itself.
drop table if exists workflows cascade;

-- 4. Flip approvals.executor to NOT NULL. Every tool-driven approval has one;
--    legacy graph-driven approvals are gone. Stamp survivors before the
--    constraint flip so the migration is safe on databases that still hold
--    pre-ADR-0002 rows.
update approvals set executor = 'unknown_legacy' where executor is null;
alter table approvals alter column executor set not null;

comment on column approvals.executor is
  'Registered executor name (per app/executors/registry.py) to invoke on grant. NOT NULL since plan 19.';
comment on column approvals.workflow_id is
  'Historical only — FK dropped in 0022. NULL for tool-driven approvals.';

commit;
