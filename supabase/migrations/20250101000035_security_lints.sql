-- =============================================================================
-- Security linter remediation
-- =============================================================================
-- Clears the two Supabase database-linter warnings reported for this project:
--
--   0011_function_search_path_mutable — public.audit_log_block_mutations,
--       public.set_updated_at, public.billing_group_projects_sync_active,
--       public.billing_group_projects_set_active
--   0014_extension_in_public — extension `vector` installed in `public`
--
-- Both are search-path hygiene: a function with a role-mutable search_path can
-- be made to resolve `billing_groups` (or any unqualified name) to an
-- attacker-planted object, and an extension in `public` puts its types and
-- operators on the same mutable resolution path.
--
-- Fix pattern for the functions: pin `search_path = ''` and schema-qualify
-- every reference in the body. `pg_catalog` is always implicitly searched, so
-- `now()` and `raise` keep working. Idempotent — safe to re-run.
-- =============================================================================

begin;

-- -----------------------------------------------------------------------------
-- 0011 — pin search_path on trigger functions
-- -----------------------------------------------------------------------------

-- No table references in the body; nothing to qualify.
create or replace function public.audit_log_block_mutations()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_log is append-only: % not permitted', tg_op;
end $$;

-- Body touches only NEW and now() (pg_catalog).
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end $$;

-- Body reads/writes public tables — qualified below.
create or replace function public.billing_group_projects_sync_active()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.is_active is distinct from old.is_active then
        update public.billing_group_projects
           set group_is_active = new.is_active
         where billing_group_id = new.id;
    end if;
    return new;
end;
$$;

create or replace function public.billing_group_projects_set_active()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    select is_active into new.group_is_active
      from public.billing_groups where id = new.billing_group_id;
    return new;
end;
$$;

-- -----------------------------------------------------------------------------
-- 0014 — move pgvector out of `public`
-- -----------------------------------------------------------------------------
-- Supabase provisions the `extensions` schema; a bare postgres cluster (CI,
-- the pytest test DB) does not, hence the create.
create schema if not exists extensions;
grant usage on schema extensions to public;

-- Existing `vector` columns and ivfflat indexes reference the type by OID, so
-- relocating the extension does not rewrite or invalidate them. Any *new* SQL
-- naming the type must write `extensions.vector` unless `extensions` is on the
-- search_path — `supabase/config.toml` already sets `extra_search_path =
-- ["public", "extensions"]` for API requests.
do $$
begin
  if exists (
    select 1
      from pg_extension e
      join pg_namespace n on n.oid = e.extnamespace
     where e.extname = 'vector' and n.nspname <> 'extensions'
  ) then
    execute 'alter extension vector set schema extensions';
  end if;
end $$;

commit;
