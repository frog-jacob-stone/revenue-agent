import type { Action, AgentRecord, TriggerResult, WorkflowRecord } from './types';

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

export function getActions(status: string): Promise<Action[]> {
  return apiFetch<Action[]>(`/actions?status=${encodeURIComponent(status)}`);
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
