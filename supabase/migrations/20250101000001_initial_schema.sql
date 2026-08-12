-- =============================================================================
-- Revenue Agent System — Initial Schema
-- =============================================================================
-- Creates all six core tables, enums, indexes, RLS policies, and the
-- append-only trigger for audit_log.
--
-- Safe to run in a single transaction. Idempotent where possible.
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- Extensions
-- -----------------------------------------------------------------------------
create extension if not exists pgcrypto;   -- gen_random_uuid()
create extension if not exists vector;     -- pgvector

-- -----------------------------------------------------------------------------
-- Enums
-- -----------------------------------------------------------------------------
do $$ begin
  create type workflow_status as enum (
    'pending', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type action_status as enum (
    'proposed', 'approved', 'rejected', 'executing', 'completed', 'failed'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type action_type as enum (
    'research',
    'send_email',
    'create_hubspot_record',
    'update_hubspot_record',
    'publish_content',
    'generate_document',
    'write_rev_rec',
    'other'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type memory_kind as enum ('fact', 'summary', 'embedding', 'preference');
exception when duplicate_object then null; end $$;

-- -----------------------------------------------------------------------------
-- Table: agents
-- -----------------------------------------------------------------------------
create table if not exists agents (
  id                 uuid primary key default gen_random_uuid(),
  slug               text unique not null,
  name               text not null,
  description        text,
  requires_approval  boolean not null default true,
  approval_scope     text[] not null default '{create,update,delete}',
  config             jsonb not null default '{}'::jsonb,
  system_prompt      text,
  is_active          boolean not null default true,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create index if not exists agents_active_slug_idx
  on agents (slug) where is_active;

-- -----------------------------------------------------------------------------
-- Table: workflows
-- -----------------------------------------------------------------------------
create table if not exists workflows (
  id               uuid primary key default gen_random_uuid(),
  kind             text not null,
  status           workflow_status not null default 'pending',
  trigger_source   text,
  trigger_payload  jsonb,
  subject_type     text,
  subject_id       text,
  subject_ref      jsonb,
  initiated_by     text,
  started_at       timestamptz default now(),
  completed_at     timestamptz,
  error            text,
  metadata         jsonb not null default '{}'::jsonb
);

create index if not exists workflows_active_status_idx
  on workflows (status)
  where status in ('pending', 'running', 'awaiting_approval');

create index if not exists workflows_kind_time_idx
  on workflows (kind, started_at desc);

create index if not exists workflows_subject_idx
  on workflows (subject_type, subject_id);

-- -----------------------------------------------------------------------------
-- Table: actions
-- -----------------------------------------------------------------------------
create table if not exists actions (
  id                uuid primary key default gen_random_uuid(),
  workflow_id       uuid not null references workflows(id) on delete cascade,
  agent_id          uuid not null references agents(id),
  sequence          int not null,
  action_type       action_type not null,
  status            action_status not null default 'proposed',
  summary           text not null,
  proposed_payload  jsonb not null,
  executed_payload  jsonb,
  result            jsonb,
  reasoning         text,
  risk_level        text check (risk_level in ('low', 'medium', 'high')),
  approved_by       text,
  approved_at       timestamptz,
  rejection_reason  text,
  executed_at       timestamptz,
  error             text,
  created_at        timestamptz not null default now(),
  unique (workflow_id, sequence)
);

create index if not exists actions_pending_idx
  on actions (status)
  where status in ('proposed', 'approved');

create index if not exists actions_workflow_seq_idx
  on actions (workflow_id, sequence);

create index if not exists actions_agent_time_idx
  on actions (agent_id, created_at desc);

-- -----------------------------------------------------------------------------
-- Table: memories
-- -----------------------------------------------------------------------------
create table if not exists memories (
  id                  uuid primary key default gen_random_uuid(),
  agent_id            uuid references agents(id) on delete cascade,
  kind                memory_kind not null,
  scope               text,
  content             text not null,
  embedding           vector(1536),
  source_workflow_id  uuid references workflows(id) on delete set null,
  source_action_id    uuid references actions(id) on delete set null,
  metadata            jsonb not null default '{}'::jsonb,
  expires_at          timestamptz,
  created_at          timestamptz not null default now()
);

create index if not exists memories_agent_kind_idx
  on memories (agent_id, kind);

create index if not exists memories_scope_idx
  on memories (scope) where scope is not null;

-- IVFFlat index for vector similarity search.
-- lists = 100 is a reasonable default for up to ~100k rows.
create index if not exists memories_embedding_idx
  on memories using ivfflat (embedding vector_cosine_ops)
  with (lists = 100)
  where embedding is not null;

-- -----------------------------------------------------------------------------
-- Table: audit_log
-- -----------------------------------------------------------------------------
create table if not exists audit_log (
  id            bigserial primary key,
  occurred_at   timestamptz not null default now(),
  event_type    text not null,
  agent_id      uuid references agents(id),
  workflow_id   uuid references workflows(id),
  action_id     uuid references actions(id),
  actor         text,
  payload       jsonb not null default '{}'::jsonb,
  ip_address    inet,
  user_agent    text
);

create index if not exists audit_log_time_idx
  on audit_log (occurred_at desc);

create index if not exists audit_log_event_time_idx
  on audit_log (event_type, occurred_at desc);

create index if not exists audit_log_workflow_idx
  on audit_log (workflow_id) where workflow_id is not null;

-- Append-only enforcement: no UPDATE or DELETE allowed.
create or replace function audit_log_block_mutations()
returns trigger language plpgsql as $$
begin
  raise exception 'audit_log is append-only: % not permitted', tg_op;
end $$;

drop trigger if exists audit_log_no_update on audit_log;
create trigger audit_log_no_update
  before update on audit_log
  for each row execute function audit_log_block_mutations();

drop trigger if exists audit_log_no_delete on audit_log;
create trigger audit_log_no_delete
  before delete on audit_log
  for each row execute function audit_log_block_mutations();

-- -----------------------------------------------------------------------------
-- Table: knowledge_base
-- -----------------------------------------------------------------------------
create table if not exists knowledge_base (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  content     text not null,
  kind        text not null,
  tags        text[] default '{}',
  embedding   vector(1536),
  source_url  text,
  version     int not null default 1,
  is_active   boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists knowledge_base_embedding_idx
  on knowledge_base using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

create index if not exists knowledge_base_kind_idx
  on knowledge_base (kind) where is_active;

create index if not exists knowledge_base_tags_idx
  on knowledge_base using gin (tags);

-- -----------------------------------------------------------------------------
-- updated_at trigger (shared)
-- -----------------------------------------------------------------------------
create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

drop trigger if exists agents_set_updated_at on agents;
create trigger agents_set_updated_at
  before update on agents
  for each row execute function set_updated_at();

drop trigger if exists knowledge_base_set_updated_at on knowledge_base;
create trigger knowledge_base_set_updated_at
  before update on knowledge_base
  for each row execute function set_updated_at();

-- -----------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------
-- RLS is enabled on every table from day one. Policies are permissive for v1
-- (single-user) and will be tightened when auth is introduced.

alter table agents          enable row level security;
alter table workflows       enable row level security;
alter table actions         enable row level security;
alter table memories        enable row level security;
alter table audit_log       enable row level security;
alter table knowledge_base  enable row level security;

-- Permissive policies for service_role
do $$
declare
  t text;
begin
  foreach t in array array['agents','workflows','actions','memories','audit_log','knowledge_base']
  loop
    execute format(
      'drop policy if exists %I_service_all on %I',
      t || '_service', t
    );
    execute format(
      'create policy %I on %I for all to service_role using (true) with check (true)',
      t || '_service_all', t
    );
  end loop;
end $$;

commit;
