-- Approvals for tool-driven proposals (per ADR-0002).
--
-- `executor` carries the registered executor name to invoke when the
-- approval is granted. NULL for legacy graph-driven approvals (being
-- phased out across plans 16–18; column becomes NOT NULL in plan 19).
--
-- `workflow_id` and `node_name` become nullable because tool-driven
-- approvals have no associated graph workflow. The FK on workflow_id
-- (with ON DELETE CASCADE) is preserved.
begin;

alter table approvals
  add column if not exists executor text;

alter table approvals
  alter column workflow_id drop not null;

alter table approvals
  alter column node_name drop not null;

comment on column approvals.executor is
  'Registered executor name (per app/executors/registry.py) to invoke on grant. NULL for legacy graph-driven approvals — to become NOT NULL after all graphs migrate (ADR-0002, plan 19).';

commit;
