import { useNavigate } from 'react-router-dom';
import { Play, AlertTriangle } from 'lucide-react';
import { AGENTS, AUDIT_ENTRIES } from '../mocks';
import StatusChip from '../components/shared/StatusChip';
import ActionTypeChip from '../components/shared/ActionTypeChip';
import AgentBadge from '../components/shared/AgentBadge';
import StubBadge from '../components/shared/StubBadge';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function Dashboard() {
  const navigate = useNavigate();
  const errorAgents = AGENTS.filter((a) => a.status === 'error');

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Banner */}
      {errorAgents.length > 0 && (
        <div className="flex items-center gap-3 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-3">
          <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
          <p className="text-red-300 text-sm font-medium">
            {errorAgents.map((a) => a.name).join(', ')} {errorAgents.length === 1 ? 'is' : 'are'} in an error state and require attention.
          </p>
        </div>
      )}

      {/* Heading */}
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Dashboard</h1>
        <p className="text-sm text-slate-400 mt-0.5">Revenue agent status overview</p>
      </div>

      {/* Agent Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {AGENTS.map((agent) => (
          <div
            key={agent.id}
            className="bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-colors cursor-pointer"
            onClick={() => navigate(`/agents/${agent.id}`)}
          >
            <div className="flex items-start justify-between mb-3">
              <div>
                <p className="font-medium text-slate-100 text-sm">{agent.name}</p>
                <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{agent.description}</p>
              </div>
              <StatusChip status={agent.status} />
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs mb-4">
              <div>
                <p className="text-slate-500">Last run</p>
                <p className="text-slate-300 font-medium mt-0.5">{fmt(agent.lastRun)}</p>
              </div>
              <div>
                <p className="text-slate-500">Pending approvals</p>
                <p className={`font-semibold mt-0.5 ${agent.pendingCount > 0 ? 'text-amber-400' : 'text-slate-400'}`}>
                  {agent.pendingCount}
                </p>
              </div>
              <div>
                <p className="text-slate-500">Actioned today</p>
                <p className="text-slate-300 font-medium mt-0.5">{agent.actionedToday}</p>
              </div>
              <div>
                <p className="text-slate-500">Agent color</p>
                <div className="w-4 h-4 rounded mt-0.5" style={{ backgroundColor: agent.color }} />
              </div>
            </div>

            <button
              className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              onClick={(e) => { e.stopPropagation(); console.log('trigger agent', agent.id); }}
            >
              <Play className="w-3 h-3" />
              Trigger manually
              <StubBadge />
            </button>
          </div>
        ))}
      </div>

      {/* Recent Activity */}
      <div>
        <h2 className="text-sm font-semibold text-slate-300 mb-3">Recent Activity</h2>
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
                <th className="text-left px-4 py-3 font-medium">Time</th>
                <th className="text-left px-4 py-3 font-medium">Agent</th>
                <th className="text-left px-4 py-3 font-medium">Type</th>
                <th className="text-left px-4 py-3 font-medium">Target</th>
                <th className="text-left px-4 py-3 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {AUDIT_ENTRIES.slice(0, 10).map((entry, i) => (
                <tr
                  key={entry.id}
                  className={`border-b border-slate-800/60 hover:bg-slate-800/40 transition-colors ${i === 9 ? 'border-b-0' : ''}`}
                >
                  <td className="px-4 py-2.5 text-slate-500 text-xs whitespace-nowrap">{fmt(entry.timestamp)}</td>
                  <td className="px-4 py-2.5"><AgentBadge agentId={entry.agentId} /></td>
                  <td className="px-4 py-2.5"><ActionTypeChip type={entry.actionType} /></td>
                  <td className="px-4 py-2.5 text-slate-300 text-xs max-w-xs truncate">{entry.target}</td>
                  <td className="px-4 py-2.5">
                    <StatusChip status={entry.outcome} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
