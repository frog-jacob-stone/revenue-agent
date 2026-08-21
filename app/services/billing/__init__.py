"""Billing / invoicing domain services.

Owns everything between the Harvest account and a reviewable pre-flight plan:

  harvest_snapshot — read-through cache of clients, projects, categories, rates
  groups           — billing-group configuration (CRUD)
  reconcile        — every billable project maps to exactly one active group
  dates            — run month + timing → service period, issue date, due date
  estimator        — T&M line-item estimates from uninvoiced time and expenses
  recurring        — the literal monthly lines, with placeholder state applied
  payload          — the exact POST /v2/invoices body
  flags            — the §7 flag catalog, table-driven
  planner          — orchestrates a run and writes the ledger
  review           — per-group approval of a planned run (human-only)
  placeholders     — pricing or omitting a placeholder line for one month
  draws            — fixed-fee draws, and `invoice_draw`, the one Harvest write
  invoices         — the ledger read back: what we actually created, both kinds
  inflight         — settling a write whose outcome is unknown (human-only)

Exactly one function here writes to Harvest: `draws.invoice_draw`, creating a
single draft invoice for one released draw. It carries no `approvals` row — per
[ADR-0004](../../../docs/adr/0004-operator-initiated-writes.md) the operator's
click on a screen showing the exact payload is the authorization. Monthly-run
execution is still unbuilt, and gated on reconciling a full month by hand.
"""
