"""Billing / invoicing domain services.

Owns everything between the Harvest account and a reviewable pre-flight plan:

  harvest_snapshot — read-through cache of clients, projects, categories, rates
  groups           — billing-group configuration (CRUD)
  reconcile        — every billable project maps to exactly one active group
  dates            — run month + timing → service period, issue date, due date
  estimator        — T&M line-item estimates from uninvoiced time and expenses
  payload          — the exact POST /v2/invoices body
  flags            — the §7 flag catalog, table-driven
  planner          — orchestrates a run and writes the ledger
  invoices         — the ledger read back: what we actually created, both kinds
  inflight         — settling a write whose outcome is unknown (human-only)

Nothing in this package writes to Harvest. The single Harvest write lives
behind the approval chain and is not yet implemented (PRD Phase 3).
"""
