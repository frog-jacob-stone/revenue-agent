-- 0029 — account-level billing settings, editable without a redeploy.
--
-- The first live draw invoice arrived at the client with blank notes. The cause
-- is in Harvest, not here: the account-level "default invoice notes" you
-- configure in Harvest's UI are applied only to invoices created *through* that
-- UI. The API neither applies them to a created invoice nor exposes them for
-- reading — `GET /v2/company` has no field for them. So the text a client needs
-- in order to pay (remit-to details, AR contact) must be sent by us on every
-- POST, which means we must store it.
--
-- Config, not env. An env var would work and was the smaller change, but this is
-- copy that a human edits and reads back — closer to `billing_groups.notes_template`
-- (which overrides it) than to a deploy secret. Putting it in the database keeps
-- it visible in the UI next to the rest of the billing config, editable without a
-- restart, and audited when it changes. `HARVEST_BASE_URI` and the Harvest
-- credentials stay in env deliberately: those are deployment identity, where a
-- wrong value is a broken deploy rather than a business decision.
--
-- Key/value rather than one column per setting. The alternative is a migration
-- every time a setting is added, and a table with one row and fifteen columns
-- that are mostly null. The cost is that values are text and callers parse —
-- acceptable while every setting is prose. Revisit if typed settings arrive.
--
-- Note the deliberate duplication with Harvest: this is a second copy of
-- something Harvest also stores, the two can drift, and nothing can detect it,
-- because there is no endpoint to read the original. Unavoidable, so it is
-- written down here rather than discovered later.

begin;

create table billing_settings (
    key        text primary key,
    value      text not null default '',
    updated_at timestamptz not null default now(),
    -- Email of the human who last changed it. Nothing else may write this table:
    -- no agent, no scheduler (ADR-0004 — operator-initiated, audited).
    updated_by text
);

comment on table billing_settings is
    'Account-level billing configuration a human edits in the UI. Deploy
     identity and secrets stay in environment variables.';
comment on column billing_settings.key is
    'Stable identifier. Known keys live in app/services/billing/settings_store.py;
     an unknown key is refused at the service layer rather than silently stored.';

-- Seeded empty so the row exists and a GET does not have to distinguish "never
-- configured" from "deliberately blank". Both mean: send no notes.
insert into billing_settings (key, value) values ('default_invoice_notes', '');

alter table billing_settings enable row level security;

create policy billing_settings_service_all on billing_settings
    for all to service_role using (true) with check (true);

commit;
