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

  // rev_rec_monthly
  'rev_rec_monthly:validate_and_sync': 'Validating and syncing',
  'rev_rec_monthly:propose_configure': 'Proposing configuration',
  'rev_rec_monthly:apply_configure_or_loop': 'Applying configuration',
  'rev_rec_monthly:compute_entries': 'Computing entries',
  'rev_rec_monthly:propose_write_entries': 'Proposing entries',
  'rev_rec_monthly:write_entries': 'Writing entries',
};

/**
 * Labels for `tool_step_*` events emitted by tools that run inline (ADR-0002,
 * plan 16). Keyed by `${tool_name}:${step}`. Replaces the workflow-event
 * labels for content_creation as it migrated to a tool.
 */
const TOOL_STEP_LABELS: Record<string, string> = {
  'create_post:interpret_brief': 'Interpreting brief',
  'create_post:draft_post': 'Drafting post',
  'create_post:voice_review': 'Reviewing voice',
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
  rev_rec_monthly: 'Revenue recognition',
};

export function labelForKind(kind: string): string {
  return WORKFLOW_LABELS[kind] ?? titleCase(kind);
}
