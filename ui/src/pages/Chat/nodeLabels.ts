/**
 * Human-readable labels for LangGraph nodes, keyed by `${workflow_kind}:${node}`.
 * The backend emits raw `node.entered/exited` audit events using the node's
 * code name (e.g. `interpret_brief`); this maps them to UI-friendly strings.
 *
 * Unknown nodes fall back to a title-cased version of the node name.
 */
const LABELS: Record<string, string> = {
  // outreach_chain
  'outreach_chain:pull_hubspot': 'Pulling HubSpot contact',
  'outreach_chain:web_search': 'Searching the web',
  'outreach_chain:consolidate': 'Consolidating research',
  'outreach_chain:retrieve_kb': 'Retrieving knowledge base',
  'outreach_chain:compose_email': 'Composing email',
  'outreach_chain:voice_critique': 'Voice critique',
  'outreach_chain:accuracy_critique': 'Accuracy critique',
  'outreach_chain:propose_send': 'Proposing send',
  'outreach_chain:gmail_send': 'Sending via Gmail',
  'outreach_chain:failed_terminal': 'Critique attempts exhausted',
};

/**
 * Labels for `tool_step_*` events emitted by tools that run inline (ADR-0002).
 * Keyed by `${tool_name}:${step}`. Replaces the workflow-event labels for
 * tools that have migrated away from LangGraph.
 */
const TOOL_STEP_LABELS: Record<string, string> = {
  'create_post:interpret_brief': 'Interpreting brief',
  'create_post:draft_post': 'Drafting post',
  'create_post:voice_review': 'Reviewing voice',
  'trigger_revenue_recognition:validate_and_sync': 'Validating and syncing',
  'trigger_revenue_recognition:compute_entries': 'Computing entries',
};

function titleCase(node: string): string {
  return node
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function labelForNode(kind: string, node: string): string {
  return LABELS[`${kind}:${node}`] ?? titleCase(node);
}

export function labelForToolStep(tool: string, step: string): string {
  return TOOL_STEP_LABELS[`${tool}:${step}`] ?? titleCase(step);
}

const WORKFLOW_LABELS: Record<string, string> = {
  outreach_chain: 'Outreach',
};

export function labelForKind(kind: string): string {
  return WORKFLOW_LABELS[kind] ?? titleCase(kind);
}
