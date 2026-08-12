-- =============================================================================
-- Agentic Workflow Patterns — Phase A
-- =============================================================================
-- Adds first-class support for multi-step prompt chains with reflection loops,
-- retries, and human checkpoints. See docs/SCHEMA.md "Agentic Patterns".
--
-- Patterns supported:
--   supervised_automation   — deterministic pipeline, single human checkpoint
--   prompt_chain_action     — multi-step LLM chain ending in an external write
--   prompt_chain_artifact   — multi-step LLM chain ending in a file artifact
--                             (schema-ready; build deferred)
--
-- Step kinds:
--   tool_call    — auto-progresses; audit trail only
--   llm_step     — auto-progresses; audit trail only
--   critique     — auto-progresses; emits critique_result; may trigger retries
--   checkpoint   — pauses for human approval (appears in inbox)
--   execution    — pauses for human approval before external write (appears in inbox)
--
-- Idempotent: safe to re-run.
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- actions: step_kind, parent/retry tracking, critique_result
-- -----------------------------------------------------------------------------
alter table actions
  add column if not exists step_kind          text,
  add column if not exists parent_action_id   uuid references actions(id),
  add column if not exists retry_of_action_id uuid references actions(id),
  add column if not exists attempt_number     int  not null default 1,
  add column if not exists max_attempts       int,
  add column if not exists critique_result    jsonb;

do $$ begin
  alter table actions
    add constraint actions_step_kind_check
    check (
      step_kind is null
      or step_kind in ('tool_call', 'llm_step', 'critique', 'checkpoint', 'execution')
    );
exception when duplicate_object then null; end $$;

create index if not exists actions_parent_idx
  on actions (parent_action_id) where parent_action_id is not null;

create index if not exists actions_retry_of_idx
  on actions (retry_of_action_id) where retry_of_action_id is not null;

create index if not exists actions_step_kind_idx
  on actions (step_kind) where step_kind is not null;

-- -----------------------------------------------------------------------------
-- workflows: pattern, current_step
-- -----------------------------------------------------------------------------
alter table workflows
  add column if not exists pattern      text,
  add column if not exists current_step int;

do $$ begin
  alter table workflows
    add constraint workflows_pattern_check
    check (
      pattern is null
      or pattern in ('supervised_automation', 'prompt_chain_action', 'prompt_chain_artifact')
    );
exception when duplicate_object then null; end $$;

commit;
