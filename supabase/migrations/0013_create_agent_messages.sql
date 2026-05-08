-- Agent messages — turn-by-turn record of every agent-to-agent exchange.
-- Phase 4 of the LangGraph multi-agent rearchitecture (see
-- .agent/plans/8.path-b-phase-4-multi-agent.md). Powers the `ask_agent` tool
-- and any future supervisor → specialist patterns. `thread_id` correlates
-- messages within one delegation; `workflow_id` (nullable) links to the
-- owning graph workflow when the call originates from a node, NULL when it
-- originates from chat.
begin;

create table if not exists agent_messages (
    id                bigserial primary key,
    thread_id         uuid not null,
    workflow_id       uuid null references workflows(id) on delete cascade,
    from_agent_slug   text not null,
    to_agent_slug     text not null,
    content           text not null,
    metadata          jsonb not null default '{}'::jsonb,
    created_at        timestamptz not null default now()
);

create index if not exists agent_messages_thread_idx
    on agent_messages(thread_id, created_at);
create index if not exists agent_messages_workflow_idx
    on agent_messages(workflow_id) where workflow_id is not null;
create index if not exists agent_messages_to_idx
    on agent_messages(to_agent_slug);

commit;
