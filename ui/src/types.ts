export type ActionStatus =
  | 'proposed'
  | 'approved'
  | 'rejected'
  | 'executing'
  | 'completed'
  | 'failed';

export type ActionType =
  | 'research'
  | 'send_email'
  | 'create_hubspot_record'
  | 'update_hubspot_record'
  | 'publish_content'
  | 'generate_document'
  | 'write_rev_rec'
  | 'configure_rev_rec_projects'
  | 'other';

export type RiskLevel = 'low' | 'medium' | 'high';

export interface Action {
  id: string;
  workflow_id: string;
  agent_id: string;
  sequence: number;
  action_type: ActionType;
  status: ActionStatus;
  summary: string;
  proposed_payload: Record<string, unknown>;
  executed_payload: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  reasoning: string | null;
  risk_level: RiskLevel | null;
  approved_by: string | null;
  approved_at: string | null;
  rejection_reason: string | null;
  executed_at: string | null;
  error: string | null;
  created_at: string;
}

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
