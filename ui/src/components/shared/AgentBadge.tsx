import { AGENT_MAP } from '../../mocks';
import type { AgentId } from '../../mocks';

interface Props {
  agentId: AgentId;
  size?: 'sm' | 'md';
}

export default function AgentBadge({ agentId, size = 'sm' }: Props) {
  const agent = AGENT_MAP[agentId];
  if (!agent) return null;
  const base = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm';
  return (
    <span
      className={`inline-flex items-center rounded font-medium ${base}`}
      style={{ backgroundColor: `${agent.color}22`, color: agent.color, border: `1px solid ${agent.color}44` }}
    >
      {agent.name}
    </span>
  );
}
