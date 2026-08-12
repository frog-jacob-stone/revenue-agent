-- Human-to-agent chat persistence.
--
-- chat_sessions  — one row per conversation; sidebar lists these per agent.
-- chat_messages  — turn-by-turn log. Assistant rows are inserted as
--                  status='streaming' placeholders and updated to 'complete'
--                  (or 'failed') by the detached turn runtime in
--                  app/services/chat_runtime.py.
--
-- A session is "in flight" iff it has any chat_messages row with
-- status='streaming'. The partial index keeps that check cheap.
--
-- Activity panel (the tool/workflow tree the UI renders under each
-- assistant turn) is persisted as JSONB matching the frontend's
-- ActivityLine[] shape. Built server-side by app/services/activity_builder.py.
begin;

create table if not exists chat_sessions (
    id              uuid primary key default gen_random_uuid(),
    agent_slug      text not null,
    title           text not null default 'New chat',
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    last_message_at timestamptz
);

create index if not exists chat_sessions_agent_idx
    on chat_sessions (agent_slug, last_message_at desc nulls last);

create table if not exists chat_messages (
    id            bigserial primary key,
    session_id    uuid not null references chat_sessions(id) on delete cascade,
    turn_id       uuid,
    role          text not null check (role in ('user', 'assistant')),
    content       text not null default '',
    activity      jsonb not null default '[]'::jsonb,
    status        text not null default 'complete'
                  check (status in ('streaming', 'complete', 'failed')),
    tool_used     text,
    error         text,
    created_at    timestamptz not null default now(),
    completed_at  timestamptz
);

create index if not exists chat_messages_session_idx
    on chat_messages (session_id, id);
create index if not exists chat_messages_streaming_idx
    on chat_messages (session_id) where status = 'streaming';

alter table chat_sessions enable row level security;
alter table chat_messages enable row level security;

create policy chat_sessions_service_all on chat_sessions
    for all to service_role
    using (true) with check (true);

create policy chat_messages_service_all on chat_messages
    for all to service_role
    using (true) with check (true);

commit;
