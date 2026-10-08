import type {
  AgentRecord,
  AgentTool,
  Approval,
  ChatPersistedMessage,
  ChatSession,
} from './types';
import { supabase } from './lib/supabase';

// Falls back to localhost only in dev. A production build with VITE_API_URL
// unset used to ship a bundle that pointed every request at the visitor's own
// machine — which fails in a way that looks like the API being down rather than
// the build being wrong. vite.config.ts also asserts this at build time, so a
// misconfigured deploy is a red pipeline; this throw is the last line of defence
// and the one that covers local dev. Mirrors lib/supabase.ts.
const BASE = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000' : '');
if (!BASE) {
  throw new Error(
    'VITE_API_URL is required for production builds. The deploy workflow supplies ' +
      'it from the API hostname; for local dev, set it in ui/.env.',
  );
}

async function authedHeaders(extra?: HeadersInit): Promise<Headers> {
  const headers = new Headers(extra);
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return headers;
}

async function handleUnauthorized(res: Response): Promise<void> {
  if (res.status !== 401) return;
  await supabase.auth.signOut();
  if (window.location.pathname !== '/login') {
    window.location.assign('/login');
  }
}

export async function authedFetch(path: string, init?: RequestInit): Promise<Response> {
  const headers = await authedHeaders(init?.headers);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  await handleUnauthorized(res);
  return res;
}

/**
 * A failed request, with the status and the raw `detail` preserved.
 *
 * `message` alone is not always enough. FastAPI's `detail` may be an object —
 * the unknown-outcome response from the invoice write carries the ids needed to
 * resolve the stuck row plus the remedy to show the operator, and flattening
 * that to a string would have rendered it as "[object Object]" in the one place
 * the text matters most.
 *
 * Existing callers that read `.message` keep working unchanged.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(ApiError.messageFrom(status, detail));
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  private static messageFrom(status: number, detail: unknown): string {
    if (typeof detail === 'string' && detail) return detail;
    // FastAPI's 422: `detail` is a list of {loc, msg, type}, one per field that
    // failed. Unhandled, every validation error on every form in this app read
    // as "HTTP 422" — which is how a rejected override amount ended up being
    // diagnosed from the server log instead of the screen. The first message is
    // enough: these forms are small, and the first failure is the one to fix.
    if (Array.isArray(detail)) {
      const first = detail.find(
        (d) => typeof (d as { msg?: unknown })?.msg === 'string',
      ) as { msg: string } | undefined;
      // Pydantic prefixes a custom validator's message with "Value error, ".
      // Ours are written to be read by a person, so the prefix is noise.
      if (first) return first.msg.replace(/^Value error,\s*/, '');
    }
    if (detail && typeof detail === 'object') {
      const msg = (detail as { message?: unknown }).message;
      if (typeof msg === 'string' && msg) return msg;
    }
    return `HTTP ${status}`;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authedFetch(path, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (body as { detail?: unknown }).detail);
  }
  return res.json() as Promise<T>;
}

export interface ApprovalFilters {
  status?: string;
  agent_slug?: string;
  action_type?: string;
}

export function getApprovals(filters: ApprovalFilters = {}): Promise<Approval[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set('status', filters.status);
  if (filters.agent_slug) params.set('agent_slug', filters.agent_slug);
  if (filters.action_type) params.set('action_type', filters.action_type);
  const qs = params.toString();
  return apiFetch<Approval[]>(`/approvals${qs ? `?${qs}` : ''}`);
}

export function getApproval(id: string): Promise<Approval> {
  return apiFetch<Approval>(`/approvals/${id}`);
}

export function approveApproval(
  id: string,
  approvedBy: string,
  executedPayload?: Record<string, unknown>,
): Promise<Approval> {
  return apiFetch<Approval>(`/approvals/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved_by: approvedBy, executed_payload: executedPayload ?? null }),
  });
}

export function rejectApproval(id: string, rejectionReason: string): Promise<Approval> {
  return apiFetch<Approval>(`/approvals/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rejected_by: 'system', rejection_reason: rejectionReason }),
  });
}

export function listAgents(): Promise<AgentRecord[]> {
  return apiFetch<AgentRecord[]>('/agents');
}

export function getAgent(slug: string): Promise<AgentRecord> {
  return apiFetch<AgentRecord>(`/agents/${slug}`);
}

export function setAgentActive(slug: string, isActive: boolean): Promise<AgentRecord> {
  return apiFetch<AgentRecord>(`/agents/${slug}/active?is_active=${isActive}`, {
    method: 'PATCH',
  });
}

export function getAgentApprovals(slug: string, status = 'all'): Promise<Approval[]> {
  return apiFetch<Approval[]>(
    `/approvals?agent_slug=${encodeURIComponent(slug)}&status=${encodeURIComponent(status)}`,
  );
}

export function getAgentTools(slug: string): Promise<AgentTool[]> {
  return apiFetch<AgentTool[]>(`/agents/${encodeURIComponent(slug)}/tools`);
}

export type ChatStreamEvent =
  | { type: 'delta'; text: string }
  | { type: 'tool_call_started'; name: string; args: Record<string, unknown> }
  | { type: 'tool_call_completed'; name: string; ok: boolean; result_summary: string }
  | { type: 'tool_step_started'; tool: string; step: string; attempt?: number }
  | {
      type: 'tool_step_completed';
      tool: string;
      step: string;
      attempt?: number;
      /** Absent means the step succeeded — mirrors `event.get("ok", True)` server-side. */
      ok?: boolean;
      detail?: string;
    }
  | { type: 'agent_task_tool_started'; agent_slug: string; name: string; args: Record<string, unknown> }
  | { type: 'agent_task_tool_completed'; agent_slug: string; name: string; ok: boolean; result_summary: string }
  | { type: 'done'; answer: string; tool_used: string | null }
  | { type: 'error'; message: string; status?: number };

export interface ChatStreamCallbacks {
  onEvent: (evt: ChatStreamEvent) => void;
  signal?: AbortSignal;
}

async function parseSseStream(
  res: Response,
  { onEvent }: ChatStreamCallbacks,
): Promise<void> {
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (!frame.trim()) continue;

      let dataLine: string | null = null;
      for (const line of frame.split('\n')) {
        if (line.startsWith('data:')) {
          dataLine = (dataLine ?? '') + line.slice(5).trimStart();
        }
      }
      if (dataLine == null) continue;
      try {
        const evt = JSON.parse(dataLine) as ChatStreamEvent;
        onEvent(evt);
      } catch {
        // ignore malformed frame
      }
    }
  }
}

export function createChatSession(agentSlug: string): Promise<ChatSession> {
  return apiFetch<ChatSession>('/chat/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_slug: agentSlug }),
  });
}

export function listChatSessions(agentSlug: string): Promise<ChatSession[]> {
  return apiFetch<ChatSession[]>(
    `/chat/sessions?agent_slug=${encodeURIComponent(agentSlug)}`,
  );
}

export function getChatMessages(sessionId: string): Promise<ChatPersistedMessage[]> {
  return apiFetch<ChatPersistedMessage[]>(`/chat/sessions/${sessionId}/messages`);
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await authedFetch(`/chat/sessions/${sessionId}`, { method: 'DELETE' });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
}

/**
 * POST a message to a chat session and parse the SSE response.
 * The backend persists the user message, detaches the turn into a background
 * task, and streams events live. If the client disconnects, the turn keeps
 * running and the final state is persisted to chat_messages.
 */
export async function sendChatMessage(
  sessionId: string,
  content: string,
  callbacks: ChatStreamCallbacks,
): Promise<void> {
  const res = await authedFetch(`/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ content }),
    signal: callbacks.signal,
  });
  await parseSseStream(res, callbacks);
}

export interface AuditLogEntry {
  id: number;
  timestamp: string;
  agent_slug: string | null;
  event_type: string;
  outcome: 'success' | 'failed' | 'pending' | 'rejected';
  reason: string | null;
  payload: Record<string, unknown>;
}

export interface AuditLogFilters {
  agent_slug?: string;
  from_date?: string;
  to_date?: string;
  outcome?: string;
  limit?: number;
  offset?: number;
}

export function getAuditLog(filters: AuditLogFilters = {}): Promise<AuditLogEntry[]> {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== '') params.set(k, String(v));
  });
  const qs = params.toString();
  return apiFetch<AuditLogEntry[]>(`/audit-log${qs ? `?${qs}` : ''}`);
}

export interface LlmCallSummary {
  id: number;
  started_at: string;
  latency_ms: number;
  model: string;
  agent_slug: string | null;
  status: 'ok' | 'error';
  streamed: boolean;
  purpose: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
}

export interface LlmCallDetail extends LlmCallSummary {
  ended_at: string;
  provider: string;
  workflow_id: string | null;
  thread_id: string | null;
  error: string | null;
  request: unknown;
  response: unknown;
}

export interface LlmCallsModelAgg {
  model: string;
  calls: number;
  tokens: number;
}

export interface LlmCallsAgentAgg {
  agent_slug: string | null;
  calls: number;
  tokens: number;
}

export interface LlmCallsSummary {
  total_calls: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  avg_latency_ms: number;
  error_rate: number;
  by_model: LlmCallsModelAgg[];
  by_agent: LlmCallsAgentAgg[];
}

export interface LlmCallsFilters {
  agent_slug?: string;
  model?: string;
  status?: 'ok' | 'error';
  from?: string;
  to?: string;
  limit?: number;
  cursor?: number;
}

function buildLlmCallsQuery(filters: LlmCallsFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v !== undefined && v !== '') params.set(k, String(v));
  });
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export function listLlmCalls(filters: LlmCallsFilters = {}): Promise<LlmCallSummary[]> {
  return apiFetch<LlmCallSummary[]>(`/llm-calls${buildLlmCallsQuery(filters)}`);
}

export function getLlmCallsSummary(
  range: { from?: string; to?: string } = {},
): Promise<LlmCallsSummary> {
  return apiFetch<LlmCallsSummary>(`/llm-calls/summary${buildLlmCallsQuery(range)}`);
}

export function getLlmCall(id: number): Promise<LlmCallDetail> {
  return apiFetch<LlmCallDetail>(`/llm-calls/${id}`);
}

// ── Billing / invoicing ─────────────────────────────────────────────────────

import type {
  BillingGroup,
  BillingGroupInput,
  BillingHealth,
  BillingRunDetail,
  BillingRunSummary,
  BillingSettingsValues,
  CreatedInvoice,
  CreatedInvoiceTotals,
  Draw,
  DrawInvoiceResult,
  DrawPreview,
  InFlightItem,
  PlaceholderResolutionInput,
  ResolveInFlightRequest,
  ResolveInFlightResult,
  RunExecutionResult,
  SnapshotRefreshResult,
} from './invoicing';

export function getBillingGroups(
  filters: { billing_type?: string; include_inactive?: boolean } = {},
): Promise<BillingGroup[]> {
  const params = new URLSearchParams();
  if (filters.billing_type) params.set('billing_type', filters.billing_type);
  if (filters.include_inactive) params.set('include_inactive', 'true');
  const qs = params.toString();
  return apiFetch<BillingGroup[]>(`/billing/groups${qs ? `?${qs}` : ''}`);
}

export function getBillingGroup(id: string): Promise<BillingGroup> {
  return apiFetch<BillingGroup>(`/billing/groups/${id}`);
}

export function createBillingGroup(body: BillingGroupInput): Promise<BillingGroup> {
  return apiFetch<BillingGroup>('/billing/groups', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function updateBillingGroup(
  id: string,
  body: Partial<BillingGroupInput>,
): Promise<BillingGroup> {
  return apiFetch<BillingGroup>(`/billing/groups/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function deactivateBillingGroup(id: string): Promise<BillingGroup> {
  return apiFetch<BillingGroup>(`/billing/groups/${id}/deactivate`, { method: 'POST' });
}

export function getBillingHealth(includeTime = true): Promise<BillingHealth> {
  return apiFetch<BillingHealth>(`/billing/health?include_time=${includeTime}`);
}

export function refreshHarvestSnapshot(): Promise<SnapshotRefreshResult> {
  return apiFetch<SnapshotRefreshResult>('/billing/snapshot/refresh', { method: 'POST' });
}

export function getBillingRuns(kind: 'monthly' | 'draw' | 'all' = 'monthly'):
  Promise<BillingRunSummary[]> {
  // An empty `kind` means both. Draw runs are single-invoice and frequent, so
  // the monthly history would drown in them by default.
  return apiFetch<BillingRunSummary[]>(
    `/billing/runs?kind=${kind === 'all' ? '' : kind}`,
  );
}

// ── Draws ──────────────────────────────────────────────────────────────────

export function getDraws(
  filters: {
    group_id?: string;
    state?: 'pending' | 'ready' | 'in_flight' | 'invoiced';
  } = {},
): Promise<Draw[]> {
  const params = new URLSearchParams();
  if (filters.group_id) params.set('group_id', filters.group_id);
  if (filters.state) params.set('state', filters.state);
  const qs = params.toString();
  return apiFetch<Draw[]>(`/billing/draws${qs ? `?${qs}` : ''}`);
}

/** Confirm (or withdraw) delivery — the only thing that makes a draw billable. */
export function setDrawRelease(drawId: string, released: boolean): Promise<Draw> {
  return apiFetch<Draw>(`/billing/draws/${drawId}/release`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ released }),
  });
}

/** The exact invoice this draw would produce. Computed on read — nothing is
 *  persisted, so there is no staged copy to keep in sync or to unwind. */
export function getDrawPreview(
  drawId: string, issueDate?: string,
): Promise<DrawPreview> {
  const qs = issueDate ? `?issue_date=${issueDate}` : '';
  return apiFetch<DrawPreview>(`/billing/draws/${drawId}/preview${qs}`);
}

/**
 * Create the Harvest draft invoice for one released draw.
 *
 * The only call in this app that writes to Harvest. The payload is deliberately
 * NOT sent — the server recomputes it from the same pure function that produced
 * the preview just read, so what gets created is what was on screen.
 *
 * Status codes carry the §8 distinction and the caller must not flatten them:
 * 409 nothing happened · 422 Harvest refused · 502 outcome unknown, go look.
 */
export function invoiceDraw(
  drawId: string, issueDate?: string,
): Promise<DrawInvoiceResult> {
  return apiFetch<DrawInvoiceResult>(`/billing/draws/${drawId}/invoice`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(issueDate ? { issue_date: issueDate } : {}),
  });
}

/** Account-level billing config a human edits. Never contains secrets. */
export function getBillingSettings(): Promise<BillingSettingsValues> {
  return apiFetch<BillingSettingsValues>('/billing/settings');
}

/** PATCH semantics: an omitted field is left alone, an empty string clears it. */
export function updateBillingSettings(
  values: Partial<BillingSettingsValues>,
): Promise<BillingSettingsValues> {
  return apiFetch<BillingSettingsValues>('/billing/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(values),
  });
}

/**
 * Every invoice this system created — draws and monthly runs together.
 *
 * Reads the ledger, so it survives reloads and shows months of history. `status`
 * defaults to `created`, the only value that means an invoice exists in Harvest.
 */
export function getCreatedInvoices(
  filters: {
    kind?: 'draw' | 'monthly';
    status?: 'created' | 'failed' | 'in_flight';
    group_id?: string;
    since?: string;
    limit?: number;
  } = {},
): Promise<CreatedInvoice[]> {
  const params = new URLSearchParams();
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.status) params.set('status', filters.status);
  if (filters.group_id) params.set('group_id', filters.group_id);
  if (filters.since) params.set('since', filters.since);
  if (filters.limit) params.set('limit', String(filters.limit));
  const qs = params.toString();
  return apiFetch<CreatedInvoice[]>(`/billing/invoices${qs ? `?${qs}` : ''}`);
}

export function getCreatedInvoiceTotals(
  since?: string,
): Promise<CreatedInvoiceTotals> {
  const qs = since ? `?since=${since}` : '';
  return apiFetch<CreatedInvoiceTotals>(`/billing/invoices/totals${qs}`);
}

/** Ledger rows whose Harvest write never returned. Should always be empty. */
export function getInFlightItems(): Promise<InFlightItem[]> {
  return apiFetch<InFlightItem[]>('/billing/in-flight');
}

/** A human's statement about what actually happened in Harvest. */
export function resolveInFlight(
  runId: string,
  itemId: string,
  body: ResolveInFlightRequest,
): Promise<ResolveInFlightResult> {
  return apiFetch<ResolveInFlightResult>(
    `/billing/runs/${runId}/items/${itemId}/resolve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

export function getBillingRun(id: string): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(`/billing/runs/${id}`);
}

export function planBillingRun(runMonth?: string): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>('/billing/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_month: runMonth ?? null }),
  });
}

export function abandonBillingRun(id: string): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(`/billing/runs/${id}/abandon`, { method: 'POST' });
}

/** Finish a run that drafted some groups and will not draft the rest. The
 *  remaining groups are recorded as decided against, with this reason. */
export function closeBillingRun(
  id: string,
  reason?: string,
): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(`/billing/runs/${id}/close`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: reason?.trim() || null }),
  });
}

/** Approve / un-approve one group, and/or record an error override. Both
 *  fields are independent; the override is sticky. */
export function setItemApproval(
  runId: string,
  itemId: string,
  body: { approved?: boolean; override?: boolean },
): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(`/billing/runs/${runId}/items/${itemId}/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Decide against invoicing one group this run, or undo that.
 *
 * Not the same as un-approving, which only returns the group to undecided.
 * Rejecting says the invoice is not going out and records why — the case it
 * exists for is a group already invoiced by hand, where drafting again would
 * put a duplicate in front of a real client. A reason is required.
 */
export function setItemRejection(
  runId: string,
  itemId: string,
  body: { rejected: boolean; reason?: string },
): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(`/billing/runs/${runId}/items/${itemId}/rejection`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Create a Harvest draft invoice for every approved group in a run.
 *
 * The payload is deliberately NOT sent — the server uses the bodies the
 * pre-flight just displayed, so what gets created is what was on screen.
 *
 * Resolves 200 even when invoices failed, and even when the run halted: a run
 * is many independent writes and the body is the report. Read `halted` and
 * `unknown_item` before telling anyone it worked.
 */
export function executeRun(runId: string): Promise<RunExecutionResult> {
  return apiFetch<RunExecutionResult>(`/billing/runs/${runId}/execute`, {
    method: 'POST',
  });
}

/** Price one placeholder line, or omit it for this run month.
 *
 *  Rebuilds the planned payload server-side, so the response is the whole run
 *  and becomes the new cache entry. If the group was already approved, that
 *  approval is withdrawn — the payload it described has changed.
 */
export function setPlaceholderResolution(
  runId: string,
  itemId: string,
  lineItemId: string,
  body: PlaceholderResolutionInput,
): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(
    `/billing/runs/${runId}/items/${itemId}/placeholders/${lineItemId}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
}

/** Withdraw a decision, returning the line to undecided at $0 — and to
 *  blocking approval, which is the point. */
export function clearPlaceholderResolution(
  runId: string, itemId: string, lineItemId: string,
): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(
    `/billing/runs/${runId}/items/${itemId}/placeholders/${lineItemId}`,
    { method: 'DELETE' },
  );
}

/** Approve every already-approvable group, or clear every approval. */
export function setRunApproval(
  runId: string, approved: boolean,
): Promise<BillingRunDetail> {
  return apiFetch<BillingRunDetail>(`/billing/runs/${runId}/approval`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved }),
  });
}

export interface HarvestClientOption {
  harvest_id: number;
  name: string;
  currency: string | null;
  is_active: boolean;
  billable_project_count: number;
}

export interface HarvestProjectOption {
  harvest_id: number;
  name: string;
  client_id: number;
  client_name: string | null;
  client_currency: string | null;
  is_active: boolean;
  is_fixed_fee: boolean;
  hourly_rate: number | null;
  /** Null when free to map; set when another active group already claims it. */
  billing_group_id: string | null;
  billing_group_name: string | null;
}

export interface InvoiceItemCategory {
  harvest_id: number;
  name: string;
}

export function getInvoiceItemCategories(): Promise<InvoiceItemCategory[]> {
  return apiFetch<InvoiceItemCategory[]>('/billing/harvest/item-categories');
}

/** Excluded clients are omitted unless asked for. The group *edit* form asks,
 *  so a group whose client was excluded after it was built stays editable. */
export function getHarvestClients(
  opts: { include_excluded?: boolean } = {},
): Promise<HarvestClientOption[]> {
  const qs = opts.include_excluded ? '?include_excluded=true' : '';
  return apiFetch<HarvestClientOption[]>(`/billing/harvest/clients${qs}`);
}

export function getHarvestProjects(
  opts: {
    client_id?: number;
    exclude_group_id?: string;
    include_excluded?: boolean;
  } = {},
): Promise<HarvestProjectOption[]> {
  const params = new URLSearchParams();
  if (opts.client_id !== undefined) params.set('client_id', String(opts.client_id));
  if (opts.exclude_group_id) params.set('exclude_group_id', opts.exclude_group_id);
  if (opts.include_excluded) params.set('include_excluded', 'true');
  const qs = params.toString();
  return apiFetch<HarvestProjectOption[]>(`/billing/harvest/projects${qs ? `?${qs}` : ''}`);
}

// ── Projects ────────────────────────────────────────────────────────────────

/**
 * One engagement on the Projects tab.
 *
 * Distinct from `HarvestProjectOption`, which exists to populate the billing
 * group form and carries the group assignment. This is the delivery view: who
 * the work is for and when it runs.
 */
export interface ProjectSummary {
  harvest_id: number;
  name: string;
  client_name: string | null;
  /** Harvest's own dates, both optional there — null means no date was set,
   *  not a date of zero. `ends_on` is editable in Harvest, so it tracks the
   *  current end rather than what was committed. */
  starts_on: string | null;
  ends_on: string | null;
  /** From Forecast: the last day a person is booked. Null when nobody is
   *  scheduled (hosting, retainers) or Forecast has not been synced. */
  projected_end_date: string | null;
  is_active: boolean;
  /** When the Harvest snapshot last ran. This reads a cache, not Harvest. */
  synced_at: string;
}

export interface ForecastRefreshResponse {
  projects: number;
  with_schedule: number;
  without_schedule: number;
  pruned: number;
}

export interface ProjectRefreshResponse {
  harvest: {
    clients: number;
    projects: number;
    invoice_item_categories: number;
    task_assignments: number;
  };
  /** Null when Forecast did not run — `forecast_error` says why. The Harvest
   *  half still committed, so this is a partial result, not a failure. */
  forecast: ForecastRefreshResponse | null;
  forecast_error: string | null;
}

/**
 * Re-read both sources behind the Projects tab: Harvest (name, client, start,
 * end) then Forecast (projected end). Read-only against both.
 *
 * Takes a few seconds — the Harvest half costs one request per billable active
 * project — so the caller should show a pending state.
 */
export function refreshProjects(): Promise<ProjectRefreshResponse> {
  return apiFetch<ProjectRefreshResponse>('/projects/refresh', { method: 'POST' });
}

/** Running engagements, or closed ones. The two are disjoint — `archived`
 *  swaps the list rather than extending it. */
export function getProjects(archived = false): Promise<ProjectSummary[]> {
  return apiFetch<ProjectSummary[]>(`/projects${archived ? '?archived=true' : ''}`);
}

// ── Client exclusions ───────────────────────────────────────────────────────

/**
 * A Harvest client this system treats as not-a-client — our own company, a
 * defunct test account. Account-wide: hidden from the Projects roster and
 * skipped by billing config reconciliation alike.
 */
export interface ExcludedClient {
  harvest_client_id: number;
  /** Null when the Harvest snapshot has no row for this id. The exclusion
   *  still stands — it outlives the cache it names. */
  client_name: string | null;
  reason: string | null;
  project_count: number;
  excluded_at: string;
  excluded_by: string;
}

export function getClientExclusions(): Promise<ExcludedClient[]> {
  return apiFetch<ExcludedClient[]>('/client-exclusions');
}

/** Idempotent — re-posting an already-excluded client updates its reason.
 *  All three return the whole list, so callers re-render from the server. */
export function excludeClient(
  harvest_client_id: number,
  reason: string | null,
): Promise<ExcludedClient[]> {
  return apiFetch<ExcludedClient[]>('/client-exclusions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ harvest_client_id, reason }),
  });
}

export function unexcludeClient(harvest_client_id: number): Promise<ExcludedClient[]> {
  return apiFetch<ExcludedClient[]>(`/client-exclusions/${harvest_client_id}`, {
    method: 'DELETE',
  });
}

// ── Contracts ───────────────────────────────────────────────────────────────

/**
 * A saved counterparty: who they are, in the words a contract needs.
 *
 * Unrelated to `ExcludedClient` and `HarvestClientOption` above, both of which
 * are about Harvest ids. This record deliberately has no Harvest link — the
 * primary case is a prospect being sent a contract *because* they are not a
 * client yet.
 */
export interface ContractClient {
  id: string;
  legal_entity_name: string;
  /** Street address, suite included — the template's address block is one line. */
  address_line1: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  signatory_name: string | null;
  /** The client's MSA effective date, worded as it reads in the document.
   *  Free text, not a date — the operator controls the format. Null when the
   *  client has no MSA, which a first engagement usually means. */
  msa_effective_date: string | null;
  created_at: string;
  updated_at: string;
  created_by: string;
  updated_by: string;
}

/** Only the legal entity name is required — a name-only prospect is valid. */
export interface ContractClientInput {
  legal_entity_name: string;
  address_line1?: string | null;
  city?: string | null;
  state?: string | null;
  postal_code?: string | null;
  signatory_name?: string | null;
  msa_effective_date?: string | null;
}

/**
 * One blank a draft asks about.
 *
 * Served by the API rather than hardcoded here, so labels and ordering come
 * from the same declaration the renderer uses — see
 * `app/services/contracts/fields.py`. A field added to the Word template shows
 * up on this form without a UI change.
 */
export interface ContractFieldSpec {
  tag: string;
  label: string;
  /** `client` comes from the saved record; `engagement` is typed each time. */
  source: 'client' | 'engagement';
  /**
   * Which `ContractClient` field a `source: 'client'` tag reads. Null for
   * engagement fields.
   *
   * Served rather than mapped here on purpose: a local copy of the mapping is
   * a second place to update when a template tag is renamed, and the one that
   * gets forgotten.
   */
  client_field: keyof ContractClientInput | null;
}

export interface TmDraftRequest {
  /** Exactly one of `client_id` or `client`. */
  client_id?: string | null;
  client?: ContractClientInput | null;
  /** File an inline counterparty for next time. */
  save_client?: boolean;
  /** Per-contract values, keyed by tag. */
  engagement?: Record<string, string | null>;
}

/** What generating would produce. Shown before the click, per ADR-0004. */
export interface TmDraftPreview {
  values: Record<string, string | null>;
  /** Labels that will render as highlighted `[REVIEW: …]` markers. */
  review_labels: string[];
  filename: string;
}

export function getContractClients(): Promise<ContractClient[]> {
  return apiFetch<ContractClient[]>('/contracts/clients');
}

export function createContractClient(body: ContractClientInput): Promise<ContractClient> {
  return apiFetch<ContractClient>('/contracts/clients', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** PATCH semantics: an omitted field is left alone, an empty string clears it. */
export function updateContractClient(
  id: string,
  body: Partial<ContractClientInput>,
): Promise<ContractClient> {
  return apiFetch<ContractClient>(`/contracts/clients/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function deleteContractClient(id: string): Promise<void> {
  const res = await authedFetch(`/contracts/clients/${id}`, { method: 'DELETE' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (body as { detail?: unknown }).detail);
  }
}

export function getTmFields(): Promise<ContractFieldSpec[]> {
  return apiFetch<ContractFieldSpec[]>('/contracts/tm/fields');
}

export function previewTmDraft(body: TmDraftRequest): Promise<TmDraftPreview> {
  return apiFetch<TmDraftPreview>('/contracts/tm/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── Revenue recognition ─────────────────────────────────────────────────────

export type RevenueType =
  | 'fixed_fee'
  | 'time_and_materials'
  | 'msf'
  | 'hosting'
  | 'retainer';

export type RevenueRunStatus = 'draft' | 'recognized' | 'abandoned';

/**
 * One project's revenue for one month.
 *
 * Amounts arrive as decimal **strings**, not numbers — the API serializes
 * `numeric(12,2)` rather than routing money through a binary float. Parse with
 * `num()` at the point of use; do not change the type to `number` here, which
 * would only move the precision loss one layer up.
 *
 * `recognized_amount` is the revenue for that month and is what every rollup
 * uses. `cumulative_recognized` is since project inception and cannot be summed
 * across rows without counting every earlier month again.
 */
export interface RevenueEntry {
  id: string;
  /** ISO first-of-month. Periods are months, not dates. */
  period_month: string;
  harvest_project_id: number;
  /** Snapshotted when recognized, so a renamed project reads as it did then. */
  harvest_project_name: string;
  /** Joined live from the Harvest cache; both null if the project has left it. */
  client_id: number | null;
  client_name: string | null;
  /** Still active in Harvest? Also read live, and deliberately so. An archived
   *  project appears in a run only while it still has revenue to recognize, and
   *  clearing that balance assumes the work completed — which the system cannot
   *  know, since a cancelled project and a finished one both stop being booked
   *  in Forecast. Null if the project has left the snapshot. */
  project_is_active: boolean | null;
  revenue_type: RevenueType;
  recognized_amount: string;
  /** What the system computed before any override. */
  computed_amount: string;
  logged_hours: string | null;
  /** Hours from inception through this period — this month's plus every
   *  recognized month before it. The denominator of `percent_complete` is this
   *  plus `scheduled_hours`, so the row can be checked against itself. Summed
   *  at read time and only by the run detail endpoint; null elsewhere. */
  cumulative_hours: string | null;
  scheduled_hours: string | null;
  /** 0–1, fixed fee only. */
  percent_complete: string | null;
  contracted_fees: string | null;
  invoiced_to_date: string | null;
  notes: string | null;
  override_reason: string | null;
  overridden_by: string | null;
  overridden_at: string | null;
}

/** A ledger row, carrying the project's running total at that month. */
export interface LedgerEntry extends RevenueEntry {
  cumulative_recognized: string;
}

/** A client with activity in the window — one option in the client filter.
 *
 *  Carries both measures so the list is a summary as well as a control, and so
 *  the number beside each name can follow the Overview's metric selector
 *  without the option list itself having to be re-fetched. Revenue *or* hours
 *  qualifies, so the same names are offered in every view. */
export interface RevenueClient {
  client_id: number;
  client_name: string | null;
  recognized_amount: string;
  logged_hours: string | null;
  project_count: number;
}

/** One month of the trend. Revenue per hour is deliberately not a field —
 *  across several months it has to be blended, which the caller decides. */
export interface RevenueMonth {
  period_month: string;
  recognized_amount: string;
  logged_hours: string | null;
  entry_count: number;
}

export interface RevenueRunSummary {
  id: string;
  period_month: string;
  status: RevenueRunStatus;
  created_at: string;
  created_by: string;
  finalized_at: string | null;
  finalized_by: string | null;
  abandoned_at: string | null;
  abandoned_by: string | null;
  entry_count: number;
  /** For a draft, what it currently proposes rather than what was booked. */
  total_recognized: string;
}

export interface RevenueRunDetail extends Omit<RevenueRunSummary, 'entry_count'> {
  entries: RevenueEntry[];
}

/** Decimal string -> number, for arithmetic and formatting. Null and blank
 *  collapse to 0 so callers can sum without guarding every field. */
export function num(value: string | null | undefined): number {
  return value == null || value === '' ? 0 : Number(value);
}

/**
 * Ledger rows, newest month first, each with its cumulative total.
 *
 * Only entries of finalized runs — a draft is a proposal, not history. Bounds
 * are inclusive and month-granular, so any date within a month selects it.
 */
export function getRevenueEntries(
  opts: {
    date_from?: string;
    date_to?: string;
    harvest_project_id?: number;
    /** Restrict to these Harvest clients. Empty or omitted means all. */
    client_ids?: number[];
    /** Drop projects with nothing to show across the whole window: `revenue`
     *  drops those that recognized nothing, `hours` those that logged nothing.
     *  Which one is right depends on the metric being viewed. Project-level —
     *  one that qualifies keeps every entry, zeros included, so the
     *  revenue-per-hour blend is unaffected. Omit to keep every project. */
    non_empty?: 'revenue' | 'hours';
  } = {},
): Promise<LedgerEntry[]> {
  const params = new URLSearchParams();
  if (opts.date_from) params.set('date_from', opts.date_from);
  if (opts.date_to) params.set('date_to', opts.date_to);
  if (opts.harvest_project_id !== undefined) {
    params.set('harvest_project_id', String(opts.harvest_project_id));
  }
  for (const id of opts.client_ids ?? []) params.append('client_ids', String(id));
  if (opts.non_empty) params.set('non_empty', opts.non_empty);
  const qs = params.toString();
  return apiFetch<LedgerEntry[]>(`/revenue/entries${qs ? `?${qs}` : ''}`);
}

/**
 * Recognized revenue and hours per month, **oldest first** — it is a chart,
 * unlike the entry and run lists, which are lookups and read newest first.
 *
 * Bounds match `getRevenueEntries`: inclusive and month-granular. Unbounded
 * returns every month the ledger covers, which is what the Overview tab wants
 * so it can offer the years the data actually spans.
 */
export function getRevenueSummary(
  opts: { date_from?: string; date_to?: string; client_ids?: number[] } = {},
): Promise<RevenueMonth[]> {
  const params = new URLSearchParams();
  if (opts.date_from) params.set('date_from', opts.date_from);
  if (opts.date_to) params.set('date_to', opts.date_to);
  for (const id of opts.client_ids ?? []) params.append('client_ids', String(id));
  const qs = params.toString();
  return apiFetch<RevenueMonth[]>(`/revenue/summary${qs ? `?${qs}` : ''}`);
}

/**
 * Clients with activity in the window — the options for the client filter.
 *
 * Deliberately not derived from `getRevenueEntries`: a faceted filter's
 * options must come from the unfiltered set, or each selection would remove
 * the others from the list and leave no way back.
 *
 * Revenue **or** hours qualifies, and both come back, so the list neither
 * changes nor needs re-fetching when the Overview's metric does.
 */
export function getRevenueClients(
  opts: { date_from?: string; date_to?: string } = {},
): Promise<RevenueClient[]> {
  const params = new URLSearchParams();
  if (opts.date_from) params.set('date_from', opts.date_from);
  if (opts.date_to) params.set('date_to', opts.date_to);
  const qs = params.toString();
  return apiFetch<RevenueClient[]>(`/revenue/clients${qs ? `?${qs}` : ''}`);
}

/** Run history, newest month first. Every status, including drafts. */
export function getRevenueRuns(limit = 24): Promise<RevenueRunSummary[]> {
  return apiFetch<RevenueRunSummary[]>(`/revenue/runs?limit=${limit}`);
}

/** One run and its entries, whatever its status. */
export function getRevenueRun(runId: string): Promise<RevenueRunDetail> {
  return apiFetch<RevenueRunDetail>(`/revenue/runs/${runId}`);
}

/** One project's recognition setup. `revenue_type` null means unconfigured —
 *  the list is a LEFT JOIN from the project, because the useful question is
 *  what still needs setting up. */
export interface RevenueProjectConfig {
  harvest_project_id: number;
  harvest_project_name: string;
  client_name: string | null;
  revenue_type: RevenueType | null;
  contracted_fees: string | null;
  notes: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

/** Shape of the 409 body when a run is planned with projects still
 *  unconfigured. The list rides along so the UI can name them and link. */
export interface UnconfiguredProjectsDetail {
  message: string;
  unconfigured_projects: { harvest_project_id: number; harvest_project_name: string }[];
}

export function getRevenueConfig(): Promise<RevenueProjectConfig[]> {
  return apiFetch<RevenueProjectConfig[]>('/revenue/config');
}

/** Configure a project. Idempotent — creates or replaces. */
export function setRevenueConfig(
  harvestProjectId: number,
  body: { revenue_type: RevenueType; contracted_fees?: string | null; notes?: string | null },
): Promise<RevenueProjectConfig> {
  return apiFetch<RevenueProjectConfig>(`/revenue/config/${harvestProjectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function removeRevenueConfig(harvestProjectId: number): Promise<void> {
  const res = await authedFetch(`/revenue/config/${harvestProjectId}`, { method: 'DELETE' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, (body as { detail?: unknown }).detail);
  }
}

/**
 * Draft a month. Read-only against Harvest and Forecast; nothing counts until
 * it is finalized.
 *
 * Throws `ApiError` with status 409 on two distinct conditions: a live run
 * already owns the month (detail is a string), or projects are unconfigured
 * (detail is `UnconfiguredProjectsDetail`). The caller distinguishes them by
 * checking whether `detail` is an object — which is why `ApiError` carries the
 * raw detail rather than flattening it to a string.
 */
export function planRevenueRun(periodMonth: string): Promise<RevenueRunDetail> {
  return apiFetch<RevenueRunDetail>('/revenue/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ period_month: periodMonth }),
  });
}

/** Replace a computed figure with a human's. Draft runs only; the reason is
 *  required and becomes the only record of why the two differ. */
export function overrideRevenueEntry(
  runId: string,
  entryId: string,
  body: { recognized_amount: string; override_reason: string },
): Promise<RevenueEntry> {
  return apiFetch<RevenueEntry>(`/revenue/runs/${runId}/entries/${entryId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Make a draft the ledger. The authorizing click (ADR-0004) — the operator
 *  has just read this exact run. Writes nothing outside Postgres. */
export function finalizeRevenueRun(runId: string): Promise<RevenueRunDetail> {
  return apiFetch<RevenueRunDetail>(`/revenue/runs/${runId}/finalize`, { method: 'POST' });
}

/** Discard a draft, freeing its month to be planned again. */
export function abandonRevenueRun(runId: string): Promise<RevenueRunDetail> {
  return apiFetch<RevenueRunDetail>(`/revenue/runs/${runId}/abandon`, { method: 'POST' });
}

/** What a delete removed. Returned rather than a bare 204, so the screen can
 *  say which month and how many entries went. */
export interface DeletedRun {
  id: string;
  period_month: string;
  entry_count: number;
}

/** Remove an abandoned run and its entries for good.
 *
 *  Abandoned only — the server returns 409 for a draft (abandon it first) and
 *  for a finalized one (deleting the ledger would restate history). The audit
 *  row carries what the run was, since its id stops resolving afterwards. */
export function deleteRevenueRun(runId: string): Promise<DeletedRun> {
  return apiFetch<DeletedRun>(`/revenue/runs/${runId}`, { method: 'DELETE' });
}
