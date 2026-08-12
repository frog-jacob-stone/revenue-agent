export type RiskLevel = 'low' | 'medium' | 'high';

export type NavTab = 'pending' | 'approved' | 'rejected' | 'all';

export interface AgentRecord {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  requires_approval: boolean;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentTool {
  name: string;
  description: string;
  input_schema: {
    properties?: Record<string, { description?: string; type?: string }>;
  };
}

export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'executed'
  | 'failed';

export interface Approval {
  id: string;
  workflow_id: string | null;
  node_name: string | null;
  executor: string | null;
  agent_slug: string | null;
  action_type: string;
  status: ApprovalStatus;
  risk_level: RiskLevel | null;
  summary: string | null;
  reasoning: string | null;
  proposed_payload: Record<string, unknown>;
  executed_payload: Record<string, unknown> | null;
  assigned_to: string | null;
  approved_by: string | null;
  approved_at: string | null;
  rejected_by: string | null;
  rejection_reason: string | null;
  executed_at: string | null;
  error: string | null;
  created_at: string;
}

export type InboxItem = Approval;

// ── Chat ────────────────────────────────────────────────────────────────────

export type ActivityKind =
  | 'tool'
  | 'node'
  | 'subagent'
  | 'error';

export interface ActivityLine {
  id: string;
  kind: ActivityKind;
  parentId: string | null;
  label: string;
  status: 'running' | 'ok' | 'fail';
  detail?: string;
}

export type ChatMessageStatus = 'streaming' | 'complete' | 'failed';

export interface ChatSession {
  id: string;
  agent_slug: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
}

export interface ChatPersistedMessage {
  id: number;
  session_id: string;
  turn_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  activity: ActivityLine[];
  status: ChatMessageStatus;
  tool_used: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}
