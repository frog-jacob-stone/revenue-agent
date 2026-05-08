import type {
  AgentRecord,
  AgentTool,
  Approval,
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

export function triggerAgent(slug: string): Promise<TriggerResult> {
  return apiFetch<TriggerResult>(`/agents/${slug}/trigger`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initiated_by: 'system', context: {} }),
  });
}

export function getAgentApprovals(slug: string, status = 'all'): Promise<Approval[]> {
  return apiFetch<Approval[]>(
    `/approvals?agent_slug=${encodeURIComponent(slug)}&status=${encodeURIComponent(status)}`,
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
