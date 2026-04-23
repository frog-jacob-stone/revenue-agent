import { useParams, useNavigate, NavLink } from 'react-router-dom';
import { Play, ToggleLeft, ToggleRight, Clock, CheckCircle2, XCircle } from 'lucide-react';
import { AGENTS, AUDIT_ENTRIES, APPROVAL_ITEMS } from '../../mocks';
import type { AgentId } from '../../mocks';
import StatusChip from '../../components/shared/StatusChip';
import ActionTypeChip from '../../components/shared/ActionTypeChip';
import StubBadge from '../../components/shared/StubBadge';
import SDRResearcherConfig from './config-panels/SDRResearcher';
import OutreachAgentConfig from './config-panels/OutreachAgent';
import ContentWriterConfig from './config-panels/ContentWriter';
import ProposalGeneratorConfig from './config-panels/ProposalGenerator';
import SlideDeckAgentConfig from './config-panels/SlideDeckAgent';
import RevenueRecognitionConfig from './config-panels/RevenueRecognition';

const CONFIG_PANELS: Record<AgentId, React.ComponentType> = {
  'sdr-researcher': SDRResearcherConfig,
  'outreach-agent': OutreachAgentConfig,
  'content-writer': ContentWriterConfig,
  'proposal-generator': ProposalGeneratorConfig,
  'slide-deck-agent': SlideDeckAgentConfig,
  'revenue-recognition': RevenueRecognitionConfig,
};

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();
  const agent = AGENTS.find((a) => a.id === agentId);

  if (!agent) {
    return (
      <div className="p-6">
        <p className="text-slate-400">Agent not found.</p>
      </div>
    );
  }

  const ConfigPanel = CONFIG_PANELS[agent.id as AgentId];
  const agentAudit = AUDIT_ENTRIES.filter((e) => e.agentId === agent.id).slice(0, 8);
  const agentPending = APPROVAL_ITEMS.filter((i) => i.agentId === agent.id && i.status === 'pending');

  return (
    <div className="flex h-full">
      {/* Agent sidebar nav */}
      <aside className="w-44 flex-shrink-0 border-r border-slate-800 py-4 px-2 space-y-0.5">
        {AGENTS.map((a) => (
          <NavLink
            key={a.id}
            to={`/agents/${a.id}`}
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
                isActive ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              }`
            }
          >
            <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: a.color }} />
            <span className="truncate">{a.name}</span>
          </NavLink>
        ))}
      </aside>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: agent.color }} />
              <h1 className="text-xl font-semibold text-slate-100">{agent.name}</h1>
              <StatusChip status={agent.status} size="md" />
            </div>
            <p className="text-sm text-slate-400">{agent.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              onClick={() => console.log('toggle', agent.id)}
            >
              {agent.status === 'disabled'
                ? <ToggleLeft className="w-4 h-4 text-slate-500" />
                : <ToggleRight className="w-4 h-4 text-cyan-400" />
              }
              {agent.status === 'disabled' ? 'Enable' : 'Disable'}
              <StubBadge />
            </button>
            <button
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/25 transition-colors"
              onClick={() => console.log('trigger', agent.id)}
            >
              <Play className="w-3.5 h-3.5" />
              Trigger
              <StubBadge />
            </button>
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Last run</p>
            <div className="flex items-center gap-1.5 text-slate-200 text-sm font-medium">
              <Clock className="w-3.5 h-3.5 text-slate-500" />
              {fmt(agent.lastRun)}
            </div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Pending approvals</p>
            <p className={`text-2xl font-bold ${agent.pendingCount > 0 ? 'text-amber-400' : 'text-slate-400'}`}>
              {agent.pendingCount}
            </p>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Actioned today</p>
            <p className="text-2xl font-bold text-slate-200">{agent.actionedToday}</p>
          </div>
        </div>

        {/* Pending approvals mini-list */}
        {agentPending.length > 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Pending Approvals</h2>
            {agentPending.map((item) => (
              <div
                key={item.id}
                className="flex items-center gap-3 cursor-pointer hover:bg-slate-800/40 -mx-2 px-2 py-1.5 rounded-lg transition-colors"
                onClick={() => navigate(`/inbox/${item.id}`)}
              >
                <ActionTypeChip type={item.actionType} />
                <span className="text-sm text-slate-300 flex-1 truncate">{item.target}</span>
                <div className="flex gap-1.5" onClick={(e) => e.stopPropagation()}>
                  <button className="text-emerald-400 hover:text-emerald-300" onClick={() => console.log('approve', item.id)}>
                    <CheckCircle2 className="w-4 h-4" />
                  </button>
                  <button className="text-red-400 hover:text-red-300" onClick={() => console.log('reject', item.id)}>
                    <XCircle className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Run history */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-800">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Run History</h2>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 uppercase tracking-wide border-b border-slate-800">
                <th className="text-left px-4 py-2.5 font-medium">Time</th>
                <th className="text-left px-4 py-2.5 font-medium">Action</th>
                <th className="text-left px-4 py-2.5 font-medium">Target</th>
                <th className="text-left px-4 py-2.5 font-medium">Outcome</th>
                <th className="text-left px-4 py-2.5 font-medium">Reason</th>
              </tr>
            </thead>
            <tbody>
              {agentAudit.map((entry, i) => (
                <tr key={entry.id} className={`border-slate-800 hover:bg-slate-800/30 ${i < agentAudit.length - 1 ? 'border-b' : ''}`}>
                  <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">{fmt(entry.timestamp)}</td>
                  <td className="px-4 py-2.5"><ActionTypeChip type={entry.actionType} /></td>
                  <td className="px-4 py-2.5 text-slate-300 text-xs max-w-[200px] truncate">{entry.target}</td>
                  <td className="px-4 py-2.5"><StatusChip status={entry.outcome} /></td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs max-w-[180px] truncate">{entry.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Config panel */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <ConfigPanel />
          <div className="mt-5 pt-4 border-t border-slate-800">
            <button
              className="px-4 py-2 rounded-lg text-sm font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/25 transition-colors"
              onClick={() => console.log('save config', agent.id)}
            >
              Save configuration
              <StubBadge />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
