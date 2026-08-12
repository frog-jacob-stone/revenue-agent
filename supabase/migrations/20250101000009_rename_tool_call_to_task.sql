-- =============================================================================
-- Rename step_kind value: 'tool_call' → 'task'
-- =============================================================================
-- The framework's "tool_call" step kind was named when the codebase still
-- conflated chain-framework "tool calls" with LLM tool-use protocol calls.
-- They're different concepts. Chain steps with this kind do deterministic,
-- code-driven work (data fetch, computation, validation) — no LLM, no tool
-- selection. Renaming to "task" removes the LLM-tool-call connotation.
--
-- Idempotent: safe to re-run.
-- =============================================================================

begin;

-- 1. Drop the existing CHECK constraint so we can update rows + redefine it.
alter table actions drop constraint if exists actions_step_kind_check;

-- 2. Migrate existing rows.
update actions set step_kind = 'task' where step_kind = 'tool_call';

-- 3. Recreate the constraint with the new value list.
alter table actions
  add constraint actions_step_kind_check
  check (
    step_kind is null
    or step_kind in ('task', 'llm_step', 'critique', 'checkpoint', 'execution')
  );

commit;
