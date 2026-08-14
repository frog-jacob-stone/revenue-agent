-- Harvest project dates.
--
-- `/v2/projects` has always returned `starts_on` and `ends_on`, and
-- `list_projects_detailed` has always fetched the full object — the snapshot
-- upsert simply discarded both. Persisting them is what lets the Projects tab
-- read from Harvest instead of a fixture.
--
-- Both nullable: Harvest treats them as optional and plenty of projects leave
-- one or both blank. A missing date is not a zero date, so there is no default.
-- No backfill is possible — nothing local holds these values. They populate on
-- the next snapshot refresh.

alter table harvest_projects
    add column starts_on date,
    add column ends_on   date;

comment on column harvest_projects.starts_on is
    'Harvest project start. Optional in Harvest; null where unset.';
comment on column harvest_projects.ends_on is
    'Harvest project end. Editable in Harvest, so it moves when a project '
    'slips — this is the current end date, not a contractual commitment.';
