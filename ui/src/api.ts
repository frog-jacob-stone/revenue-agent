import type {
  Action,
  AgentRecord,
  AgentTool,
  Approval,
  InboxItem,
  TriggerResult,
  WorkflowRecord,
  WorkflowTrace,
} from './types';

export interface SummaryStats {
  accountsResearched: number;
  outreachSent: number;
  proposalsGenerated: number;
  approvalRate: number;
  avgTimeToApprove: string;
  mostActiveAgent: string;
}

export interface DailyRunRow {
  date: string;
  [agentName: string]: number | string;
}

export interface ApprovalRateRow {
  agent: string;
  rate: number;
}

export interface AnalyticsData {
  summaryStats: SummaryStats;
  dailyRuns: DailyRunRow[];
  approvalRates: ApprovalRateRow[];
}

const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export interface ActionFilters {
  status?: string;
  agent_slug?: string;
  action_type?: string;
}

export function getActions(filters: ActionFilters = {}): Promise<Action[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set('status', filters.status);
  if (filters.agent_slug) params.set('agent_slug', filters.agent_slug);
  if (filters.action_type) params.set('action_type', filters.action_type);
  const qs = params.toString();
  return apiFetch<Action[]>(`/actions${qs ? `?${qs}` : ''}`);
}

export function getAction(id: string): Promise<Action> {
  return apiFetch<Action>(`/actions/${id}`);
}

export function getWorkflowTrace(workflowId: string): Promise<WorkflowTrace> {
  return apiFetch<WorkflowTrace>(`/workflows/${workflowId}/trace`);
}

export interface OutreachTriggerResponse {
  workflow_id: string;
  kind: string;
  status: string;
}

export function triggerOutreach(hubspotContactId: string): Promise<OutreachTriggerResponse> {
  return apiFetch<OutreachTriggerResponse>('/workflows/outreach', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hubspot_contact_id: hubspotContactId, initiated_by: 'system' }),
  });
}

export function approveAction(
  id: string,
  approvedBy: string,
  executedPayload: Record<string, unknown>,
): Promise<Action> {
  return apiFetch<Action>(`/actions/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved_by: approvedBy, executed_payload: executedPayload }),
  });
}

export function rejectAction(id: string, rejectionReason: string): Promise<Action> {
  return apiFetch<Action>(`/actions/${id}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rejected_by: 'system', rejection_reason: rejectionReason }),
  });
}

// ── v2 (LangGraph) approvals ────────────────────────────────────────────────

export interface ApprovalFilters {
  status?: string;
  agent_slug?: string;
  action_type?: string;
}

// v1 status "proposed" maps to v2 status "pending"; everything else passes through.
function mapV2Status(status: string | undefined): string | undefined {
  if (!status) return undefined;
  if (status === 'proposed') return 'pending';
  return status;
}

export function getApprovals(filters: ApprovalFilters = {}): Promise<Approval[]> {
  const params = new URLSearchParams();
  const status = mapV2Status(filters.status);
  if (status) params.set('status', status);
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

// Dual-source the inbox by querying both /actions and /approvals in parallel
// and merging by created_at desc. Use isApproval(item) at render time to
// distinguish v1 and v2 rows.
export async function getInboxItems(filters: ActionFilters = {}): Promise<InboxItem[]> {
  const [actions, approvals] = await Promise.all([
    getActions(filters),
    getApprovals(filters),
  ]);
  const merged: InboxItem[] = [...actions, ...approvals];
  merged.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  return merged;
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

export function triggerAgent(slug: string): Promise<TriggerResult> {
  return apiFetch<TriggerResult>(`/agents/${slug}/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initiated_by: 'system', context: {} }),
  });
}

export function getAgentActions(slug: string, status = 'all'): Promise<Action[]> {
  return apiFetch<Action[]>(
    `/actions?agent_slug=${encodeURIComponent(slug)}&status=${encodeURIComponent(status)}`,
  );
}

export function getAgentWorkflows(slug: string): Promise<WorkflowRecord[]> {
  return apiFetch<WorkflowRecord[]>(`/workflows?kind=${encodeURIComponent(slug)}`);
}

export function getAgentTools(slug: string): Promise<AgentTool[]> {
  return apiFetch<AgentTool[]>(`/agents/${encodeURIComponent(slug)}/tools`);
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatResponse {
  answer: string;
  tool_used: string | null;
}

export function agentChat(agentSlug: string, messages: ChatMessage[]): Promise<ChatResponse> {
  return apiFetch<ChatResponse>(`/chat/${encodeURIComponent(agentSlug)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
}

export function getAnalytics(days = 30): Promise<AnalyticsData> {
  return apiFetch<AnalyticsData>(`/analytics?days=${days}`);
}

export interface ChainSummary {
  kind: string;
  pattern: string;
  agent_slug: string;
  step_count: number;
}

export interface ChainStep {
  index: number;
  kind: 'task' | 'llm_step' | 'critique' | 'checkpoint' | 'execution';
  summary: string;
  agent_slug: string;
  action_type: string;
  risk_level: string | null;
  has_skip_if: boolean;
  skip_if_label: string | null;
  on_approve_label: string | null;
  has_on_approve_callback: boolean;
  critiques_step_index: number | null;
  max_attempts: number | null;
}

export interface ChainStructure extends ChainSummary {
  steps: ChainStep[];
}

export function getChains(agentSlug?: string): Promise<ChainSummary[]> {
  const qs = agentSlug ? `?agent_slug=${encodeURIComponent(agentSlug)}` : '';
  return apiFetch<ChainSummary[]>(`/chains${qs}`);
}

export function getChainStructure(kind: string): Promise<ChainStructure> {
  return apiFetch<ChainStructure>(`/chains/${encodeURIComponent(kind)}`);
}

export async function getChainDiagram(kind: string): Promise<string> {
  const res = await fetch(`${BASE}/chains/${encodeURIComponent(kind)}/diagram`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return res.text();
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
