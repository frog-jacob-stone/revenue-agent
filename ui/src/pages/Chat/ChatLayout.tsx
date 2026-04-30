import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { listAgents } from '../../api';
import type { AgentRecord } from '../../types';
import ChatWindow from './ChatWindow';

const AGENT_COLORS: Record<string, string> = {
  'sdr-researcher': '#6366f1',
  'outreach-agent': '#06b6d4',
  'content-writer': '#10b981',
  'proposal-generator': '#f59e0b',
  'slide-deck-agent': '#ec4899',
  'revenue-recognition': '#8b5cf6',
};

export default function ChatLayout() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listAgents()
      .then((all) => {
        const conversational = all.filter((a) => a.is_conversational);
        setAgents(conversational);
        if (!agentId && conversational.length > 0) {
          navigate(`/chat/${conversational[0].slug}`, { replace: true });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const activeAgent = agents.find((a) => a.slug === agentId) ?? null;

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-slate-500 text-sm">
        Loading agents…
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Agent selector sidebar */}
      <aside className="w-52 flex-shrink-0 border-r border-slate-800 py-3 px-2 space-y-0.5 overflow-y-auto">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold px-3 mb-2">Agents</p>
        {agents.map((agent) => {
          const color = AGENT_COLORS[agent.slug] ?? '#64748b';
          return (
            <button
              key={agent.slug}
              className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-left transition-colors ${
                agentId === agent.slug
                  ? 'bg-slate-800 text-slate-100'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              }`}
              onClick={() => navigate(`/chat/${agent.slug}`)}
            >
              <div
                className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 text-xs font-bold"
                style={{ backgroundColor: `${color}22`, color, border: `1px solid ${color}44` }}
              >
                {agent.name[0]}
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium truncate">{agent.name}</p>
                <p className="text-[10px] text-slate-600 truncate">
                  {agent.is_active ? 'active' : 'disabled'}
                </p>
              </div>
            </button>
          );
        })}
      </aside>

      {/* Chat window */}
      <div className="flex-1 min-w-0">
        {agentId ? (
          <ChatWindow agentId={agentId} agent={activeAgent} />
        ) : (
          <div className="flex h-full items-center justify-center text-slate-500 text-sm">
            Select an agent to start chatting.
          </div>
        )}
      </div>
    </div>
  );
}
