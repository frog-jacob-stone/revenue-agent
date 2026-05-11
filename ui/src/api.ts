import type {
  AgentRecord,
  AgentTool,
  Approval,
  ChatPersistedMessage,
  ChatSession,
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

export type ChatStreamEvent =
  | { type: 'delta'; text: string }
  | { type: 'tool_call_started'; name: string; args: Record<string, unknown> }
  | { type: 'workflow_started'; workflow_id: string; kind: string }
  | {
      type: 'workflow_event';
      workflow_id: string;
      event_type: string;
      actor: string | null;
      payload: Record<string, unknown>;
    }
  | { type: 'tool_call_completed'; name: string; ok: boolean; result_summary: string }
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

export function getChatSession(sessionId: string): Promise<ChatSession> {
  return apiFetch<ChatSession>(`/chat/sessions/${sessionId}`);
}

export function getChatMessages(sessionId: string): Promise<ChatPersistedMessage[]> {
  return apiFetch<ChatPersistedMessage[]>(`/chat/sessions/${sessionId}/messages`);
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/chat/sessions/${sessionId}`, { method: 'DELETE' });
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
  agentSlug: string,
  sessionId: string,
  content: string,
  callbacks: ChatStreamCallbacks,
): Promise<void> {
  const res = await fetch(`${BASE}/chat/${encodeURIComponent(agentSlug)}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ session_id: sessionId, content }),
    signal: callbacks.signal,
  });
  await parseSseStream(res, callbacks);
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
