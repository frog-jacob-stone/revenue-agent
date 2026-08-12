/**
 * Presentation metadata for the agent roster.
 *
 * The backend (`GET /agents`, `AgentRecord`) owns identity — slug, name,
 * description, active state. It has no opinion about colour, so that lives here.
 * Keep the keys in sync with `app/agents/registry.py`.
 *
 * This file replaced three separate copies of the same colour map (AgentDetail,
 * ChatWindow, and one derived inside the since-removed Analytics page), each
 * keyed on the slugs of five prototype agents that were never in the registry.
 *
 * Unknown slugs are not an error: an audit row can reference an agent that has
 * since been renamed or removed, and a grey badge reads better than a blank
 * cell. Both helpers below fall back rather than returning undefined.
 */

export const AGENT_COLORS: Record<string, string> = {
  'chief-of-staff': '#4f46e5',
  bdr: '#0891b2',
  linkedin: '#059669',
  'revenue-ops': '#7c3aed',
};

export const FALLBACK_AGENT_COLOR = '#475569';

/** Names the backend would give us; used where we only have a slug in hand. */
const AGENT_NAMES: Record<string, string> = {
  'chief-of-staff': 'Chief of Staff',
  bdr: 'BDR',
  linkedin: 'LinkedIn',
  'revenue-ops': 'Revenue Ops',
};

export function agentColor(slug: string | null | undefined): string {
  if (!slug) return FALLBACK_AGENT_COLOR;
  return AGENT_COLORS[slug] ?? FALLBACK_AGENT_COLOR;
}

export function agentDisplayName(slug: string | null | undefined): string {
  if (!slug) return 'Unknown';
  const known = AGENT_NAMES[slug];
  if (known) return known;
  // Title-case the slug so a new or retired agent still reads as a name.
  return slug
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}
