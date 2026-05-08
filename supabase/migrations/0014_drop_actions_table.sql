-- 0014: Drop the v1 `actions` table and its dependents.
--
-- Phase 5 of the LangGraph migration: v1 is fully decommissioned. The
-- `audit_log.action_id` FK is dropped via CASCADE, but audit_log rows
-- themselves are preserved (the FK is ON DELETE NO ACTION; CASCADE on
-- DROP TABLE drops the constraint, not the rows).
begin;

drop table if exists actions cascade;

commit;
