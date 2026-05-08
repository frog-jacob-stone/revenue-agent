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

export type StepKind = 'task' | 'llm_step' | 'critique' | 'checkpoint' | 'execution';

export type WorkflowPattern =
  | 'supervised_automation'
  | 'prompt_chain_action'
  | 'prompt_chain_artifact';

export interface CritiqueResult {
  passed: boolean;
  score?: number;
  feedback?: string;
  issues?: string[];
}

export interface TraceAction {
  id: string;
  sequence: number;
  step_kind: StepKind | null;
  action_type: string;
  summary: string;
  status: ActionStatus;
  parent_action_id: string | null;
  retry_of_action_id: string | null;
  attempt_number: number;
  max_attempts: number | null;
  critique_result: CritiqueResult | null;
  duration_ms: number | null;
  created_at: string;
  executed_at: string | null;
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
  pattern: WorkflowPattern | null;
  status: string;
  current_step: number | null;
  actions: TraceAction[];
  events: TraceEvent[];
}

// ── v2 (LangGraph) approvals ────────────────────────────────────────────────
//
// Phase 1 of the LangGraph migration introduces a parallel inbox surface at
// /approvals. `content_publish` writes to /approvals; the other v1 chains
// still write to /actions. `InboxItem` is the union that lets the inbox UI
// dual-source from both surfaces until v1 is retired in Phase 5.

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

export type InboxItem = Action | Approval;

export function isApproval(item: InboxItem): item is Approval {
  return 'node_name' in item;
}
