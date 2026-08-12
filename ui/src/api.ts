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
      passed?: boolean;
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
  action_type: string | null;
  target: string | null;
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
  ResolveInFlightRequest,
  ResolveInFlightResult,
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

export function getHarvestClients(): Promise<HarvestClientOption[]> {
  return apiFetch<HarvestClientOption[]>('/billing/harvest/clients');
}

export function getHarvestProjects(
  opts: { client_id?: number; exclude_group_id?: string } = {},
): Promise<HarvestProjectOption[]> {
  const params = new URLSearchParams();
  if (opts.client_id !== undefined) params.set('client_id', String(opts.client_id));
  if (opts.exclude_group_id) params.set('exclude_group_id', opts.exclude_group_id);
  const qs = params.toString();
  return apiFetch<HarvestProjectOption[]>(`/billing/harvest/projects${qs ? `?${qs}` : ''}`);
}
