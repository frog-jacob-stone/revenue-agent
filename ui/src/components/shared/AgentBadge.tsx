import { agentColor, agentDisplayName } from '../../agents';

interface Props {
  /** Agent slug, as it appears on an audit row or approval. */
  agentId: string;
  /** Backend-supplied name, when the caller has an `AgentRecord` to hand. */
  name?: string;
  size?: 'sm' | 'md';
}

export default function AgentBadge({ agentId, name, size = 'sm' }: Props) {
  const color = agentColor(agentId);
  const label = name ?? agentDisplayName(agentId);
  const base = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm';
  return (
    <span
      className={`inline-flex items-center rounded font-medium ${base}`}
      style={{ backgroundColor: `${color}22`, color, border: `1px solid ${color}44` }}
    >
      {label}
    </span>
  );
}
