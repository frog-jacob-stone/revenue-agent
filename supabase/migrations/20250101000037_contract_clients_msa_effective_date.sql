-- When the client's MSA took effect.
--
-- A SOW executed under a master services agreement names that agreement's
-- effective date, and that date is a fact about the *relationship*, not about
-- the engagement — it is identical across every SOW written under the same MSA.
-- So it belongs on `contract_clients` alongside the legal name and address, and
-- for the same reason: it was being retyped.
--
-- A separate migration rather than an edit to `0036`, because `0036` has already
-- been applied. Additive, so it lands on an existing database without a reset.
--
-- `text`, not `date`. Every other template-backed column here is text, and for
-- one reason: the value is substituted verbatim into a Word document, so the
-- operator has to control its exact wording. A `date` column would force this
-- code to pick a rendering ("2026-01-01"? "January 1, 2026"? "1 January 2026")
-- and a contract's date format is the document's business, not the database's.
-- `sow_date` is free text on the same grounds. The cost is no validation, which
-- is real; the mitigation is that a wrong date is visible on the page the
-- operator reviews before sending, whereas a reformatted one would not be.
--
-- Nullable, like everything else but the legal name. Plenty of clients have no
-- MSA — a first engagement is exactly the case a T&M SOW gets written for — and
-- an empty value is not silent: it renders as a highlighted `[REVIEW: MSA
-- effective date]` marker, so a SOW that references an MSA the system knows
-- nothing about says so on its face.

alter table contract_clients
    add column msa_effective_date text;

comment on column contract_clients.msa_effective_date is
    'Effective date of the client''s MSA, as it should read in a document. '
    'Text rather than date so the operator controls the wording; null when '
    'there is no MSA.';
