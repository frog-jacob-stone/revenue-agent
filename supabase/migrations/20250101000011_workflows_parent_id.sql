-- Allow workflows to track a parent workflow when one is spawned as a sub-workflow
-- by another workflow's node. Phase 0 of the LangGraph rearchitecture
-- (.agent/plans/3.langgraph-multi-agent-rearchitecture.md). Used by
-- app/orchestrator_v2/spawn.py and surfaced in the trace UI later.
begin;

alter table workflows
    add column if not exists parent_workflow_id uuid null
    references workflows(id) on delete set null;

create index if not exists workflows_parent_id_idx on workflows(parent_workflow_id)
    where parent_workflow_id is not null;

commit;
