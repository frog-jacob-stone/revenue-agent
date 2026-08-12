-- Approvals — lifecycle queue for human-in-the-loop pauses in v2 (LangGraph) workflows.
-- Replaces the role of `actions` rows that were filtered to step_kind IN
-- ('checkpoint','execution'). The `actions` table itself is kept untouched in
-- Phase 0 and removed in Phase 5 of the rearchitecture (see
-- .agent/plans/3.langgraph-multi-agent-rearchitecture.md).
begin;

create table if not exists approvals (
    id                uuid primary key default gen_random_uuid(),
    workflow_id       uuid not null references workflows(id) on delete cascade,
    node_name         text not null,
    agent_slug        text not null,
    action_type       text not null,
    status            text not null default 'pending'
                      check (status in ('pending','approved','rejected','executed','failed')),
    risk_level        text,
    summary           text,
    reasoning         text,
    proposed_payload  jsonb not null default '{}'::jsonb,
    executed_payload  jsonb,
    assigned_to       text,
    approved_by       text,
    approved_at       timestamptz,
    rejected_by       text,
    rejection_reason  text,
    executed_at       timestamptz,
    error             text,
    created_at        timestamptz not null default now()
);

create index if not exists approvals_workflow_id_idx on approvals(workflow_id);
create index if not exists approvals_status_idx on approvals(status);
create index if not exists approvals_pending_idx on approvals(status) where status = 'pending';

commit;
