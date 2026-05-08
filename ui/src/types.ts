export type RiskLevel = 'low' | 'medium' | 'high';

export type NavTab = 'pending' | 'approved' | 'rejected' | 'all';

export interface AgentRecord {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  requires_approval: boolean;
  is_conversational: boolean;
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

export interface TriggerResult {
  workflow_id: string;
  proposals: number;
}

export interface WorkflowRecord {
  id: string;
  kind: string;
  status: string;
  trigger_source: string | null;
  initiated_by: string | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}

export interface TraceEvent {
  id: string;
  event_type: string;
  occurred_at: string;
  actor: string | null;
  payload: Record<string, unknown>;
}

export interface WorkflowTrace {
  workflow_id: string;
  kind: string;
  status: string;
  events: TraceEvent[];
}

export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'executed'
  | 'failed';

export interface Approval {
  id: string;
  workflow_id: string;
  node_name: string;
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
