begin;

alter table agents
  add column if not exists allowed_tools jsonb not null default '[]'::jsonb;

commit;
