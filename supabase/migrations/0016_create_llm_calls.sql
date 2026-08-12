-- LLM calls — per-request audit log of every OpenAI chat completion.
-- Captures full request and response payloads alongside model, token usage,
-- latency, and originating agent/workflow for security review, debugging,
-- and token-usage tracking. Written by `app/services/llm_logging.py::write_llm_call`,
-- which the instrumented `call_openai_chat` wrapper invokes on every call
-- (and which the streaming path in `app/services/chat.py` invokes after each
-- streaming round-trip ends).
begin;

create table if not exists llm_calls (
    id                bigserial primary key,
    started_at        timestamptz not null,
    ended_at          timestamptz not null,
    latency_ms        integer     not null,
    provider          text        not null default 'openai',
    model             text        not null,
    agent_slug        text        null,
    workflow_id       uuid        null references workflows(id) on delete set null,
    thread_id         uuid        null,
    purpose           text        null,
    status            text        not null check (status in ('ok', 'error')),
    error             text        null,
    streamed          boolean     not null default false,
    request           jsonb       not null,
    response          jsonb       null,
    prompt_tokens     integer     null,
    completion_tokens integer     null,
    total_tokens      integer     null
);

create index if not exists llm_calls_started_idx  on llm_calls (started_at desc);
create index if not exists llm_calls_agent_idx    on llm_calls (agent_slug, started_at desc);
create index if not exists llm_calls_workflow_idx on llm_calls (workflow_id) where workflow_id is not null;
create index if not exists llm_calls_model_idx    on llm_calls (model, started_at desc);
create index if not exists llm_calls_status_idx   on llm_calls (status, started_at desc) where status <> 'ok';

alter table llm_calls enable row level security;

create policy llm_calls_service_all on llm_calls
    for all to service_role
    using (true) with check (true);

commit;
