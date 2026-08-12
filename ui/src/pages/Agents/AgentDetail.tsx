import { useEffect, useState } from 'react';
import { useParams, useNavigate, NavLink } from 'react-router-dom';
import { ToggleLeft, ToggleRight, Clock, CheckCircle2, XCircle } from 'lucide-react';
import {
  listAgents,
  getAgent,
  getAgentApprovals,
  getAgentTools,
  setAgentActive,
} from '../../api';
import type { AgentRecord, AgentTool, Approval } from '../../types';
import { agentColor } from '../../agents';
import StatusChip from '../../components/shared/StatusChip';
import RevenueRecognitionConfig from './config-panels/RevenueRecognition';

/**
 * Only agents with a panel appear here; the rest render without one. The five
 * panels that used to sit alongside this (SDR Researcher, Outreach Agent,
 * Content Writer, Proposal Generator, Slide Deck Agent) were keyed to prototype
 * slugs that never existed in `app/agents/registry.py`, so they were
 * unreachable. They were deleted with the mock fixture.
 */
const CONFIG_PANELS: Record<string, React.ComponentType> = {
  'revenue-ops': RevenueRecognitionConfig,
};

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function isToday(iso: string): boolean {
  const d = new Date(iso);
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

function ActionTypePill({ type }: { type: string }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono text-slate-600 bg-slate-100 border border-slate-300">
      {type.replace(/_/g, ' ')}
    </span>
  );
}

function AgentSidebar({ agents }: { agents: AgentRecord[] }) {
  return (
    <aside className="w-44 flex-shrink-0 border-r border-slate-200 py-4 px-2 space-y-0.5">
      {agents.map((a) => (
        <NavLink
          key={a.id}
          to={`/settings/agents/${a.slug}`}
          className={({ isActive }) =>
            `flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
              isActive
                ? 'bg-slate-100 text-slate-900'
                : 'text-slate-600 hover:text-slate-800 hover:bg-slate-50'
            }`
          }
        >
          <span
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: agentColor(a.slug) }}
          />
          <span className="truncate">{a.name}</span>
        </NavLink>
      ))}
    </aside>
  );
}

export default function AgentDetail() {
  const { agentId } = useParams<{ agentId: string }>();
  const navigate = useNavigate();

  const [sidebarAgents, setSidebarAgents] = useState<AgentRecord[]>([]);
  const [agentRecord, setAgentRecord] = useState<AgentRecord | null>(null);
  const [actions, setActions] = useState<Approval[]>([]);
  const [tools, setTools] = useState<AgentTool[]>([]);
  const [toggling, setToggling] = useState(false);

  useEffect(() => {
    listAgents().then(setSidebarAgents).catch(() => {});
  }, []);

  useEffect(() => {
    if (!agentId) return;
    setAgentRecord(null);
    setActions([]);
    setTools([]);
    Promise.all([
      getAgent(agentId).then(setAgentRecord),
      getAgentApprovals(agentId, 'all').then(setActions),
      getAgentTools(agentId).then(setTools).catch(() => {}),
    ]).catch(() => {});
  }, [agentId]);

  const pendingActions = actions.filter((a) => a.status === 'pending');
  const historyActions = actions.filter((a) => a.status !== 'pending').slice(0, 8);
  const actionedToday = actions.filter(
    (a) =>
      (a.status === 'executed' || a.status === 'failed') &&
      isToday(a.executed_at ?? a.created_at),
  ).length;
  const lastAction = actions
    .map((a) => a.approved_at ?? a.executed_at ?? a.created_at)
    .filter((v): v is string => !!v)
    .sort()
    .reverse()[0] ?? null;


  async function handleToggleActive() {
    if (!agentRecord || !agentId || toggling) return;
    setToggling(true);
    try {
      const updated = await setAgentActive(agentId, !agentRecord.is_active);
      setAgentRecord(updated);
      setSidebarAgents((prev) =>
        prev.map((a) => (a.slug === agentId ? { ...a, is_active: updated.is_active } : a)),
      );
    } catch (_) {
      // silent — surface errors in a later iteration
    } finally {
      setToggling(false);
    }
  }

  if (!agentRecord) {
    return (
      <div className="flex h-full">
        <AgentSidebar agents={sidebarAgents} />
        <div className="flex-1 p-6">
          <p className="text-sm text-slate-500">Loading…</p>
        </div>
      </div>
    );
  }

  const ConfigPanel = agentId ? CONFIG_PANELS[agentId] : undefined;
  const agentStatus = agentRecord.is_active ? 'idle' : 'disabled';
  const color = agentColor(agentId);

  return (
    <div className="flex h-full">
      <AgentSidebar agents={sidebarAgents} />

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
              <h1 className="text-xl font-semibold text-slate-900">{agentRecord.name}</h1>
              <StatusChip status={agentStatus} size="md" />
            </div>
            <p className="text-sm text-slate-600">{agentRecord.description ?? ''}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={toggling}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 disabled:opacity-50 text-slate-700 transition-colors"
              onClick={handleToggleActive}
            >
              {agentRecord.is_active
                ? <ToggleRight className="w-4 h-4 text-cyan-600" />
                : <ToggleLeft className="w-4 h-4 text-slate-500" />}
              {agentRecord.is_active ? 'Disable' : 'Enable'}
            </button>
            {/* A "Trigger" button lived here. It POSTed /agents/{slug}/trigger,
                which the agents router has never defined, so it 404'd on every
                click. Agents are reached through Chat; there is no manual-run
                entry point to expose. */}
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-white border border-slate-200 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Last action</p>
            <div className="flex items-center gap-1.5 text-slate-800 text-sm font-medium">
              <Clock className="w-3.5 h-3.5 text-slate-500" />
              {lastAction ? fmt(lastAction) : <span className="text-slate-500">Never</span>}
            </div>
          </div>
          <div className="bg-white border border-slate-200 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Pending approvals</p>
            <p className={`text-2xl font-bold ${pendingActions.length > 0 ? 'text-amber-600' : 'text-slate-600'}`}>
              {pendingActions.length}
            </p>
          </div>
          <div className="bg-white border border-slate-200 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Actioned today</p>
            <p className="text-2xl font-bold text-slate-800">{actionedToday}</p>
          </div>
        </div>

        {/* Pending approvals mini-list */}
        {pendingActions.length > 0 && (
          <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
            <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Pending Approvals
            </h2>
            {pendingActions.slice(0, 5).map((action) => (
              <div
                key={action.id}
                className="flex items-center gap-3 cursor-pointer hover:bg-slate-50 -mx-2 px-2 py-1.5 rounded-lg transition-colors"
                onClick={() => navigate(`/inbox/${action.id}`)}
              >
                <ActionTypePill type={action.action_type} />
                <span className="text-sm text-slate-700 flex-1 truncate">{action.summary}</span>
                <div className="flex gap-1.5" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="text-emerald-600 hover:text-emerald-700"
                    onClick={() => console.log('approve', action.id)}
                  >
                    <CheckCircle2 className="w-4 h-4" />
                  </button>
                  <button
                    className="text-red-600 hover:text-red-700"
                    onClick={() => console.log('reject', action.id)}
                  >
                    <XCircle className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Approval history */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-200">
            <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Approval History
            </h2>
          </div>
          {historyActions.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-400 text-center">No approvals yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 uppercase tracking-wide border-b border-slate-200">
                  <th className="text-left px-4 py-2.5 font-medium">Time</th>
                  <th className="text-left px-4 py-2.5 font-medium">Action</th>
                  <th className="text-left px-4 py-2.5 font-medium">Summary</th>
                  <th className="text-left px-4 py-2.5 font-medium">Outcome</th>
                  <th className="text-left px-4 py-2.5 font-medium">Reasoning</th>
                </tr>
              </thead>
              <tbody>
                {historyActions.map((action, i) => (
                  <tr
                    key={action.id}
                    className={`border-slate-200 hover:bg-slate-50 ${i < historyActions.length - 1 ? 'border-b' : ''}`}
                  >
                    <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">
                      {fmt(action.created_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <ActionTypePill type={action.action_type} />
                    </td>
                    <td className="px-4 py-2.5 text-slate-700 text-xs max-w-[200px] truncate">
                      {action.summary}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusChip status={action.status} />
                    </td>
                    <td className="px-4 py-2.5 text-slate-500 text-xs max-w-[180px] truncate">
                      {action.reasoning ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Config panel */}
        {ConfigPanel && (
          <div className="bg-white border border-slate-200 rounded-xl p-5">
            <ConfigPanel />
          </div>
        )}

        {/* Tools */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-200">
            <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Tools</h2>
          </div>
          {tools.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-400 text-center">No tools registered.</p>
          ) : (
            <div className="divide-y divide-slate-200">
              {tools.map((tool) => (
                <div key={tool.name} className="px-4 py-3">
                  <p className="text-xs font-mono text-cyan-600 mb-0.5">{tool.name}</p>
                  <p className="text-sm text-slate-600">{tool.description}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
