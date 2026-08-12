-- Remove static metadata columns from agents — these are now owned exclusively
-- by the Python class registry. The DB only stores runtime-mutable state.
alter table agents
  drop column if exists name,
  drop column if exists description,
  drop column if exists requires_approval,
  drop column if exists approval_scope,
  drop column if exists system_prompt,
  drop column if exists allowed_tools;
