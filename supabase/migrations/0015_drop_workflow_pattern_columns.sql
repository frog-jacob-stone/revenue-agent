-- 0015: Drop the `pattern` and `current_step` columns from workflows.
--
-- These were the v1 prompt-chain orchestrator's progress markers. v2 keeps
-- workflow progress in LangGraph checkpoints, so the columns are unused.
-- `pattern` is plain text (not a Postgres enum) so no DROP TYPE is needed.
begin;

alter table workflows drop column if exists pattern;
alter table workflows drop column if exists current_step;

commit;
