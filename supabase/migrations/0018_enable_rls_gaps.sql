-- Close RLS gaps flagged by the Supabase security advisor.
-- Both tables were created without `enable row level security`, leaving them
-- readable/writable by the project's anon key via PostgREST. The FastAPI
-- backend uses service_role asyncpg (RLS-bypassing), so adding a
-- service-role-only policy keeps backend access unchanged while blocking
-- the anon/PostgREST path.
--
-- The four LangGraph checkpoint tables (checkpoints, checkpoint_blobs,
-- checkpoint_writes, checkpoint_migrations) are created at runtime by
-- AsyncPostgresSaver.setup() and are locked down in app/db_security.py
-- during the FastAPI lifespan — they cannot be patched in a migration
-- because they don't exist when migrations run on a fresh DB.
begin;

alter table public.approvals      enable row level security;
alter table public.agent_messages enable row level security;

create policy approvals_service_all on public.approvals
    for all to service_role using (true) with check (true);

create policy agent_messages_service_all on public.agent_messages
    for all to service_role using (true) with check (true);

commit;
