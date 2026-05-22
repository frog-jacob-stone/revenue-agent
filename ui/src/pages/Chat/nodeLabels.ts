/**
 * Labels for `tool_step_*` events emitted by tools that run inline (ADR-0002).
 * Keyed by `${tool_name}:${step}`. Mirror of
 * app/services/activity_builder.py::_TOOL_STEP_LABELS.
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

export function labelForToolStep(tool: string, step: string): string {
  return TOOL_STEP_LABELS[`${tool}:${step}`] ?? titleCase(step);
}
