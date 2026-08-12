// Types and formatting helpers for the Invoices module.
//
// The types mirror app/models/billing.py; the helpers are the shared display
// vocabulary for the Invoices screens (money, dates, draw state, labels).
//
// This lived at `src/mocks/invoicing.ts` until the mock fixture beside it was
// deleted. It never held mock data — the static fixtures came out once the
// screens moved to the real API — so the path was misleading about production
// code that `api.ts` and every Invoices screen depend on.

export type BillingType =
  | 'time_and_materials'
  | 'fixed_fee_schedule'
  | 'recurring_monthly'
  | 'manual';

export type BillingTiming = 'arrears' | 'advance';

export type PaymentTerm =
  | 'upon receipt'
  | 'net 15'
  | 'net 30'
  | 'net 45'
  | 'net 60'
  | 'custom';

// Harvest's two summary vocabularies differ: time has `task`, expenses have
// `category`. Kept as separate types so an invalid pairing fails to compile.
export type SummaryType = 'project' | 'task' | 'people' | 'detailed';
export type ExpenseSummaryType = 'project' | 'category' | 'people' | 'detailed';

export type RunStatus =
  | 'planning'
  | 'awaiting_approval'
  | 'executing'
  | 'completed'
  | 'failed'
  | 'abandoned';

export type RunItemStatus =
  | 'planned'
  | 'approved'
  | 'skipped'
  | 'in_flight'
  | 'created'
  | 'failed'
  | 'abandoned';

export type FlagSeverity = 'error' | 'warning' | 'info';

export interface Flag {
  code: string;
  severity: FlagSeverity;
  message: string;
  context?: Record<string, unknown>;
}

export interface BillingGroupProject {
  harvest_project_id: number;
  harvest_project_name: string | null;
  sort_order: number;
}

/**
 * One draw on a fixed-fee contract's payment schedule.
 *
 * `scheduled_date` is what the contract commits to and what the team works
 * against — but it never authorises a bill on its own. A draw becomes billable
 * only when a human confirms delivery (`released_at`). When a date slips you
 * edit the schedule; the draw waits either way.
 */
export interface ScheduleItem {
  id: string;
  harvest_project_id: number;
  harvest_project_name?: string | null;
  sequence: number;
  description: string;
  amount: number;
  kind: string;
  scheduled_date: string;
  released_at: string | null;
  released_by: string | null;
  invoiced_run_id: string | null;
  /** The run holding this draw's live ledger row, once execution writes one. */
  live_run_id?: string | null;
  /** What this draw produced. A drafted draw leaves the queue, so these are the
   *  only record of what it became. */
  harvest_invoice_number?: string | null;
  harvest_invoice_id?: number | null;
  invoice_issue_date?: string | null;
  invoice_due_date?: string | null;
  invoiced_amount?: number | null;
  invoiced_at?: string | null;
}

/** A draw as it appears in the Ready to draft queue — group context included. */
export interface Draw extends ScheduleItem {
  billing_group_id: string;
  billing_group_name: string;
  harvest_client_name: string | null;
}

/**
 * The exact invoice a draw would produce, computed on read.
 *
 * Nothing behind this is persisted. A draw's invoice is a pure function of the
 * group config and the draw, so it is recomputed every time the row is opened
 * — which also means it always reflects the config as it stands now, not as it
 * stood when someone last looked at it.
 */
export interface DrawPreview {
  draw_id: string;
  billing_group_id: string;
  billing_group_name: string | null;
  harvest_client_name: string | null;
  description: string;
  state: DrawState;
  amount: number;
  issue_date: string;
  due_date: string | null;
  payment_term: string | null;
  subject: string;
  notes: string | null;
  estimated_line_items: EstimatedLineItem[];
  planned_payload: Record<string, unknown>;
  flags: Flag[];
  billable: boolean;
}

/** What Harvest created, plus the ledger row recording it. */
export interface DrawInvoiceResult {
  draw_id: string;
  billing_run_id: string;
  billing_run_item_id: string;
  harvest_invoice_id: number;
  harvest_invoice_number: string | null;
  planned_amount: number;
  /** The amount **at creation**. Drafts are edited in Harvest before sending,
   *  so this is not necessarily what the client was billed. */
  actual_amount: number;
  variance: number;
  /** The dates actually used. Both anchor to the moment of creation, so these
   *  may be later than whatever a preview showed. */
  issue_date: string;
  due_date: string | null;
  payment_term: string | null;
}

/**
 * Account-level billing config, editable in Settings → Billing.
 *
 * Deliberately does not include Harvest credentials or the account web address:
 * those are deployment identity, they live in environment variables, and this
 * endpoint never serves them.
 */
export interface BillingSettingsValues {
  default_invoice_notes: string;
}

/**
 * One invoice this system created, from either kind of run.
 *
 * Everything was captured at creation time. `actual_amount` is what Harvest
 * returned *then* — a draft edited afterwards (overages, added lines) will not
 * match what the client was finally sent.
 */
export interface CreatedInvoice {
  billing_run_item_id: string;
  billing_run_id: string;
  billing_group_id: string | null;
  billing_group_name: string | null;
  harvest_client_id: number | null;
  harvest_client_name: string | null;
  billing_type: string | null;
  status: string;
  kind: 'draw' | 'monthly';
  run_month: string;
  harvest_invoice_id: number | null;
  harvest_invoice_number: string | null;
  planned_amount: number;
  actual_amount: number | null;
  variance: number | null;
  issue_date: string | null;
  due_date: string | null;
  /** Null for a draw — it covers no service period. */
  period_start: string | null;
  period_end: string | null;
  /** Set for a draw only: the milestone's name. */
  draw_description: string | null;
  draw_sequence: number | null;
  error_message: string | null;
  created_at: string;
}

export interface CreatedInvoiceTotals {
  count: number;
  draw_count: number;
  monthly_count: number;
  total_amount: number;
  /** Linked by hand with no amount recorded, so counted at planned. */
  unverified_count: number;
}

/** A Harvest write whose outcome is unknown. This list should always be empty. */
export interface InFlightItem {
  billing_run_item_id: string;
  billing_run_id: string;
  billing_group_id: string;
  billing_group_name: string | null;
  harvest_client_name: string | null;
  fixed_fee_schedule_item_id: string | null;
  draw_description: string | null;
  planned_amount: number;
  issue_date: string | null;
  created_at: string;
}

/**
 * The `detail` object on a 502 from the invoice write.
 *
 * Carries what the operator needs to recover: what happened, where to look, and
 * the ids that identify the row to resolve.
 */
export interface UnknownWriteDetail {
  message: string;
  billing_run_id: string;
  billing_run_item_id: string;
  remedy: string;
}

export interface ResolveInFlightRequest {
  resolution: 'link' | 'failed';
  harvest_invoice_id?: number;
  harvest_invoice_number?: string;
  actual_amount?: number;
}

export interface ResolveInFlightResult {
  billing_run_id: string;
  billing_run_item_id: string;
  resolution: string;
  status: string;
  fixed_fee_schedule_item_id: string | null;
  harvest_invoice_id: number | null;
}

/** A draw as submitted by the group form. `id` is null for a new row. */
export interface DrawInput {
  id: string | null;
  harvest_project_id: number;
  description: string;
  amount: number;
  kind: string;
  scheduled_date: string;
  sequence: number;
  /** Present when editing an existing draw; read-only, drives locking. */
  released_at?: string | null;
  invoiced_run_id?: string | null;
  live_run_id?: string | null;
}

export type DrawState = 'pending' | 'ready' | 'in_flight' | 'invoiced';

export const DRAW_STATE_LABEL: Record<DrawState, string> = {
  pending: 'Awaiting delivery',
  ready: 'Ready to draft',
  in_flight: 'Creating',
  invoiced: 'Drafted',
};

export interface RecurringLineItem {
  id: string;
  harvest_project_id: number;
  description: string;
  quantity: number;
  unit_price: number;
  /** Harvest invoice item category — "Service", "Billable Expense", … */
  kind: string;
  /** Created at $0 for you to complete in the Harvest draft. */
  is_placeholder: boolean;
  sort_order: number;
  effective_from: string | null;
  effective_to: string | null;
}

/** One recurring line item as submitted by the group form. */
export interface RecurringItemInput {
  harvest_project_id: number;
  description: string;
  quantity: number;
  unit_price: number;
  kind: string;
  is_placeholder: boolean;
  sort_order: number;
  effective_from: string | null;
  effective_to: string | null;
}

export interface BillingGroup {
  id: string;
  name: string;
  harvest_client_id: number;
  harvest_client_name: string | null;
  billing_type: BillingType;
  billing_timing: BillingTiming;
  payment_term: PaymentTerm;
  custom_net_days: number | null;
  time_summary_type: SummaryType | null;
  include_expenses: boolean;
  expense_summary_type: ExpenseSummaryType | null;
  attach_receipts: boolean;
  subject_template: string;
  notes_template: string | null;
  purchase_order: string | null;
  requires_purchase_order: boolean;
  currency: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  projects: BillingGroupProject[];
  schedule_items?: ScheduleItem[];
  recurring_items?: RecurringLineItem[];
}

/** Request body for create/update. */
export interface BillingGroupInput {
  name: string;
  harvest_client_id: number;
  harvest_client_name?: string | null;
  billing_type: BillingType;
  billing_timing?: BillingTiming;
  payment_term?: PaymentTerm;
  custom_net_days?: number | null;
  time_summary_type?: SummaryType | null;
  include_expenses?: boolean;
  expense_summary_type?: ExpenseSummaryType | null;
  attach_receipts?: boolean;
  subject_template?: string;
  notes_template?: string | null;
  purchase_order?: string | null;
  requires_purchase_order?: boolean;
  currency?: string | null;
  projects?: { harvest_project_id: number; sort_order?: number }[];
  recurring_items?: RecurringItemInput[];
  schedule_items?: DrawInput[];
}

export interface EstimatedLineItem {
  label: string;
  detail: string | null;
  quantity: number;
  unit: string;
  unit_price: number;
  amount: number;
}

export interface RunItem {
  id: string;
  billing_group_id: string;
  billing_group_name: string | null;
  harvest_client_name: string | null;
  billing_type: BillingType | null;
  billing_timing: BillingTiming | null;
  status: RunItemStatus;
  run_month: string;
  period_start: string | null;
  period_end: string | null;
  issue_date: string | null;
  due_date: string | null;
  planned_amount: number;
  prior_amount: number | null;
  actual_amount: number | null;
  variance: number | null;
  harvest_invoice_id: number | null;
  harvest_invoice_number: string | null;
  error_message: string | null;
  skip_reason: string | null;
  /** Review state. `status === 'approved'` is the source of truth for
   *  approval; `error_override` is sticky and survives un-approving. */
  approved_at: string | null;
  approved_by: string | null;
  error_override: boolean;
  estimated_line_items: EstimatedLineItem[];
  planned_payload: Record<string, unknown>;
  flags: Flag[];
}

export type RunKind = 'monthly' | 'draw';

export interface BillingRunSummary {
  id: string;
  run_month: string;
  label: string;
  status: RunStatus;
  /** A `draw` run is a single fixed-fee draw billed off-cycle — one item, no
   *  period. */
  kind: RunKind;
  created_at: string;
  approved_at: string | null;
  completed_at: string | null;
  planned_count: number;
  skipped_count: number;
  planned_total: number;
  flag_counts: Record<string, number>;
}

export interface BillingRunDetail extends BillingRunSummary {
  run_flags: Flag[];
  items: RunItem[];
}

export interface UnmappedProject {
  harvest_project_id: number;
  harvest_project_name: string;
  harvest_client_name: string | null;
  uninvoiced_hours: number;
  estimated_value: number;
  is_active: boolean;
}

export interface SnapshotInfo {
  clients: number;
  projects: number;
  invoice_item_categories: string[];
  fetched_at: string | null;
  /** Account web address, from config — no Harvest endpoint exposes it. Empty
   *  means show no link rather than guessing a subdomain. */
  harvest_base_uri: string;
}

/** A link to an invoice in Harvest, or null when the base URI is unconfigured.
 *  Never fabricate this: a wrong link sends someone hunting for an invoice they
 *  are already unsure exists. */
export function harvestInvoiceUrl(
  baseUri: string | undefined,
  invoiceId?: number | null,
): string | null {
  if (!baseUri) return null;
  const root = baseUri.replace(/\/+$/, '');
  return invoiceId ? `${root}/invoices/${invoiceId}` : `${root}/invoices`;
}

export interface BillingHealth {
  unmapped_projects: UnmappedProject[];
  flags: Flag[];
  snapshot: SnapshotInfo;
  counts: Record<string, number>;
}

export interface SnapshotRefreshResult {
  clients: number;
  projects: number;
  invoice_item_categories: number;
  task_assignments: number;
}

// ── Labels ─────────────────────────────────────────────────────────────────

export const BILLING_TYPE_LABEL: Record<BillingType, string> = {
  time_and_materials: 'T&M',
  fixed_fee_schedule: 'Fixed fee',
  recurring_monthly: 'Recurring',
  manual: 'Manual',
};

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  planning: 'Planning',
  awaiting_approval: 'Awaiting approval',
  executing: 'Executing',
  completed: 'Completed',
  failed: 'Failed',
  abandoned: 'Abandoned',
};

// ── Derived helpers ────────────────────────────────────────────────────────

/**
 * The one error that must never be overridable: approving past an unresolved
 * in-flight row risks creating the duplicate invoice the whole protocol exists
 * to prevent.
 */
export const NON_OVERRIDABLE_FLAGS = new Set(['UNRESOLVED_IN_FLIGHT']);

export function hasError(item: RunItem) {
  return item.flags.some((f) => f.severity === 'error');
}

export function blockingFlag(item: RunItem): Flag | undefined {
  return item.flags.find((f) => NON_OVERRIDABLE_FLAGS.has(f.code));
}

export function money(n: number | null | undefined) {
  return (n ?? 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

export function shortDate(iso: string | null | undefined) {
  if (!iso) return '—';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
}

export function dateTime(iso: string | null | undefined) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** `2026-08-01` → `2026-08`, for the month input. */
export function monthInputValue(iso: string) {
  return iso.slice(0, 7);
}

export function monthLabel(iso: string) {
  const [y, m] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString('en-US', {
    month: 'short',
    year: 'numeric',
  });
}

// ── Draws ──────────────────────────────────────────────────────────────────

/** Derived rather than stored — there is no state column to drift out of sync
 *  with `released_at`, `invoiced_run_id`, and the live ledger row.
 *
 *  `in_flight` is checked before `ready` on purpose: once execution has written
 *  a ledger row, offering to draft the draw again is an action the index refuses. */
export function drawState(
  d: Pick<ScheduleItem, 'released_at' | 'invoiced_run_id' | 'live_run_id'>,
): DrawState {
  if (d.invoiced_run_id) return 'invoiced';
  if (d.live_run_id) return 'in_flight';
  return d.released_at ? 'ready' : 'pending';
}

/**
 * How many draws are asking for something right now.
 *
 * Ready to draft plus past its scheduled date — money sitting uncollected, and
 * a commitment that has slipped. Both are things only a human can move, which
 * is what makes them worth a counter; everything else in the queue is waiting
 * on delivery and will get there on its own.
 */
export function drawsNeedingAttention(
  draws: Pick<
    ScheduleItem,
    'released_at' | 'invoiced_run_id' | 'live_run_id' | 'scheduled_date'
  >[],
  today = new Date(),
): number {
  return draws.filter(
    (d) => drawState(d) === 'ready' || drawIsOverdue(d, today),
  ).length;
}

/** `2026-09-15` for a Date, in local time — `toISOString` would shift the day
 *  across a timezone boundary. */
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** The scheduled date has passed and delivery still isn't confirmed. Compared
 *  by day: a draw due today isn't late yet. */
export function drawIsOverdue(
  d: Pick<ScheduleItem, 'scheduled_date' | 'released_at' | 'invoiced_run_id'>,
  today = new Date(),
): boolean {
  if (drawState(d) !== 'pending') return false;
  return d.scheduled_date.slice(0, 10) < isoDate(today);
}
