-- Who the other side of a contract is, in the words a contract needs.
--
-- Drafting a T&M agreement means retyping the same identity block every time:
-- the legal entity name, the address it operates from, who signs. For a client
-- we have contracted with before, that is transcription, not judgment — and it
-- is the slowest part of producing a draft.
--
-- Deliberately NOT a link to `harvest_clients`. The primary case is a prospect:
-- someone we are sending a contract to *because* they are not a client yet, who
-- by definition has no Harvest id and may never get one. Keying on Harvest
-- would make this table useless for exactly the people it exists to serve. When
-- they do become a client, the two records coexist and neither needs the other
-- — nothing downstream joins them.
--
-- Deliberately NOT a CRM. There is no contact log, no owner, no stage, no
-- opportunity value. The scope test for a column here is "does a contract
-- preamble or signature block need it?" — and `harvest_clients` plus
-- `billing_groups` already hold the commercial terms for clients who bill.
--
-- Rate cards and role definitions are absent for the same reason: they live in
-- the .docx template as static text, are identical across engagements today,
-- and modelling them would buy nothing.
--
-- Only the legal entity name is required. A half-known prospect is still worth
-- saving, and the gaps are not silent — every empty field renders in the draft
-- as a highlighted `[REVIEW: …]` marker.

create table contract_clients (
    id                 uuid primary key default gen_random_uuid(),
    -- The name as it appears in the agreement: "Acme Industries, LLC", not
    -- "Acme". Unique, because it is also the label the operator picks from —
    -- two rows reading the same thing would be an unresolvable choice on the
    -- form. Doubles as the natural key; there is no separate display name, so
    -- "Acme" and "Acme Industries, LLC" cannot drift apart.
    legal_entity_name  text not null unique,
    -- One line, because the template's address block is one line
    -- (`{{ client_street }}`). A suite or floor goes here with the street. A
    -- second column would have nowhere to render and would be a field the form
    -- asked for and the document dropped.
    address_line1      text,
    city               text,
    state              text,
    postal_code        text,
    -- Who signs, by name. Not their title or email: the template's signature
    -- block has a name line and nothing else today. Add columns when it does.
    signatory_name     text,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    created_by         text not null,
    updated_by         text not null
);

comment on table contract_clients is
    'Counterparty legal identity for contract drafting: entity name, address, '
    'signatory. Owned by this system, not synced from anywhere. Prospects are '
    'the primary case, so there is deliberately no Harvest client link.';

alter table contract_clients enable row level security;

create policy contract_clients_service_all on contract_clients
    for all to service_role using (true) with check (true);
