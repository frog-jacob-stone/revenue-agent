-- 0026 — persist per-group approval on the pre-flight ledger.
--
-- Approval was previously component state in the pre-flight screen: every
-- planned group started checked, and a reload wiped the operator's review.
-- That is exactly backwards for a screen whose whole job is deciding which
-- invoices are allowed to exist.
--
-- The `billing_run_item_status` enum already carries 'approved', so approval
-- is a status transition (`planned` ⇄ `approved`) rather than a parallel
-- boolean — one source of truth, and the existing "live row" predicates
-- (`status in ('planned','approved')`) already account for it.
--
-- `error_override` is separate and deliberately sticky: it records that a
-- human accepted an error-severity flag. Un-approving a group does not
-- withdraw that judgement, so toggling the checkbox twice does not force the
-- operator to re-override. Flags in `flags.NON_OVERRIDABLE` are refused at the
-- service layer regardless of this column.

alter table billing_run_items
    add column approved_at    timestamptz,
    add column approved_by    text,
    add column error_override boolean not null default false;

comment on column billing_run_items.approved_by is
    'Email of the human who approved this group. Approval is human-only.';
comment on column billing_run_items.error_override is
    'A human accepted this group despite an error-severity flag. Never set for
     non-overridable flags (UNRESOLVED_IN_FLIGHT).';
