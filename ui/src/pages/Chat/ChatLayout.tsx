import { useParams, useNavigate } from 'react-router-dom';
import { AGENTS } from '../../mocks';
import type { AgentId } from '../../mocks';
import ChatWindow from './ChatWindow';

export default function ChatLayout() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const activeId = (agentId ?? AGENTS[0].id) as AgentId;

  return (
    <div className="flex h-full">
      {/* Agent selector sidebar */}
      <aside className="w-52 flex-shrink-0 border-r border-slate-800 py-3 px-2 space-y-0.5 overflow-y-auto">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold px-3 mb-2">Agents</p>
        {AGENTS.map((agent) => (
          <button
            key={agent.id}
            className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-left transition-colors ${
              activeId === agent.id
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
            onClick={() => navigate(`/chat/${agent.id}`)}
          >
            <div
              className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 text-xs font-bold"
              style={{ backgroundColor: `${agent.color}22`, color: agent.color, border: `1px solid ${agent.color}44` }}
            >
              {agent.name[0]}
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium truncate">{agent.name}</p>
              <p className="text-[10px] text-slate-600 truncate">{agent.status}</p>
            </div>
          </button>
        ))}
      </aside>

      {/* Chat window */}
      <div className="flex-1 min-w-0">
        <ChatWindow agentId={activeId} />
      </div>
    </div>
  );
}
