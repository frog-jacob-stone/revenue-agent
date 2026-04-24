import { useEffect, useState } from 'react';
import { useParams, useNavigate, NavLink } from 'react-router-dom';
import { Play, ToggleLeft, ToggleRight, Clock, CheckCircle2, XCircle } from 'lucide-react';
import type { AgentId } from '../../mocks';
import {
  listAgents,
  getAgent,
  getAgentActions,
  getAgentWorkflows,
  setAgentActive,
  triggerAgent,
} from '../../api';
import type { AgentRecord, WorkflowRecord, Action } from '../../types';
import StatusChip from '../../components/shared/StatusChip';
import StubBadge from '../../components/shared/StubBadge';
import SDRResearcherConfig from './config-panels/SDRResearcher';
import OutreachAgentConfig from './config-panels/OutreachAgent';
import ContentWriterConfig from './config-panels/ContentWriter';
import ProposalGeneratorConfig from './config-panels/ProposalGenerator';
import SlideDeckAgentConfig from './config-panels/SlideDeckAgent';
import RevenueRecognitionConfig from './config-panels/RevenueRecognition';

const AGENT_COLORS: Record<string, string> = {
  'sdr-researcher': '#6366f1',
  'outreach-agent': '#06b6d4',
  'content-writer': '#10b981',
  'proposal-generator': '#f59e0b',
  'slide-deck-agent': '#ec4899',
  'revenue-recognition': '#8b5cf6',
};

const CONFIG_PANELS: Record<AgentId, React.ComponentType> = {
  'sdr-researcher': SDRResearcherConfig,
  'outreach-agent': OutreachAgentConfig,
  'content-writer': ContentWriterConfig,
  'proposal-generator': ProposalGeneratorConfig,
  'slide-deck-agent': SlideDeckAgentConfig,
  'revenue-recognition': RevenueRecognitionConfig,
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
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-mono text-slate-400 bg-slate-800 border border-slate-700">
      {type.replace(/_/g, ' ')}
    </span>
  );
}

function AgentSidebar({ agents }: { agents: AgentRecord[] }) {
  return (
    <aside className="w-44 flex-shrink-0 border-r border-slate-800 py-4 px-2 space-y-0.5">
      {agents.map((a) => (
        <NavLink
          key={a.id}
          to={`/agents/${a.slug}`}
          className={({ isActive }) =>
            `flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
              isActive
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`
          }
        >
          <span
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: AGENT_COLORS[a.slug] ?? '#64748b' }}
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
  const [actions, setActions] = useState<Action[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowRecord[]>([]);
  const [toggling, setToggling] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  useEffect(() => {
    listAgents().then(setSidebarAgents).catch(() => {});
  }, []);

  useEffect(() => {
    if (!agentId) return;
    setAgentRecord(null);
    setActions([]);
    setWorkflows([]);
    Promise.all([
      getAgent(agentId).then(setAgentRecord),
      getAgentActions(agentId, 'all').then(setActions),
      getAgentWorkflows(agentId).then(setWorkflows),
    ]).catch(() => {});
  }, [agentId]);

  const pendingActions = actions.filter((a) => a.status === 'proposed');
  const historyActions = actions.filter((a) => a.status !== 'proposed').slice(0, 8);
  const actionedToday = actions.filter(
    (a) =>
      (a.status === 'completed' || a.status === 'failed') &&
      isToday(a.executed_at ?? a.created_at),
  ).length;
  const lastRun = workflows[0]?.started_at ?? null;

  async function handleTrigger() {
    if (!agentId || triggering) return;
    setTriggering(true);
    setTriggerError(null);
    try {
      await triggerAgent(agentId);
    } catch (err) {
      setTriggerError((err as Error).message);
    } finally {
      setTriggering(false);
    }
  }

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

  const ConfigPanel = CONFIG_PANELS[agentId as AgentId];
  const agentStatus = agentRecord.is_active ? 'idle' : 'disabled';
  const color = AGENT_COLORS[agentId ?? ''] ?? '#64748b';

  return (
    <div className="flex h-full">
      <AgentSidebar agents={sidebarAgents} />

      <div className="flex-1 overflow-y-auto p-6 space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 mb-1">
              <span className="w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
              <h1 className="text-xl font-semibold text-slate-100">{agentRecord.name}</h1>
              <StatusChip status={agentStatus} size="md" />
            </div>
            <p className="text-sm text-slate-400">{agentRecord.description ?? ''}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={toggling}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-slate-300 transition-colors"
              onClick={handleToggleActive}
            >
              {agentRecord.is_active
                ? <ToggleRight className="w-4 h-4 text-cyan-400" />
                : <ToggleLeft className="w-4 h-4 text-slate-500" />}
              {agentRecord.is_active ? 'Disable' : 'Enable'}
            </button>
            <button
              disabled={triggering}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors"
              onClick={handleTrigger}
            >
              <Play className="w-3.5 h-3.5" />
              {triggering ? 'Triggering…' : 'Trigger'}
            </button>
            {triggerError && (
              <p className="text-xs text-red-400 mt-1">{triggerError}</p>
            )}
          </div>
        </div>

        {/* Stats row */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Last run</p>
            <div className="flex items-center gap-1.5 text-slate-200 text-sm font-medium">
              <Clock className="w-3.5 h-3.5 text-slate-500" />
              {lastRun ? fmt(lastRun) : <span className="text-slate-500">Never</span>}
            </div>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Pending approvals</p>
            <p className={`text-2xl font-bold ${pendingActions.length > 0 ? 'text-amber-400' : 'text-slate-400'}`}>
              {pendingActions.length}
            </p>
          </div>
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1">Actioned today</p>
            <p className="text-2xl font-bold text-slate-200">{actionedToday}</p>
          </div>
        </div>

        {/* Pending approvals mini-list */}
        {pendingActions.length > 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-3">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
              Pending Approvals
            </h2>
            {pendingActions.slice(0, 5).map((action) => (
              <div
                key={action.id}
                className="flex items-center gap-3 cursor-pointer hover:bg-slate-800/40 -mx-2 px-2 py-1.5 rounded-lg transition-colors"
                onClick={() => navigate(`/inbox/${action.id}`)}
              >
                <ActionTypePill type={action.action_type} />
                <span className="text-sm text-slate-300 flex-1 truncate">{action.summary}</span>
                <div className="flex gap-1.5" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="text-emerald-400 hover:text-emerald-300"
                    onClick={() => console.log('approve', action.id)}
                  >
                    <CheckCircle2 className="w-4 h-4" />
                  </button>
                  <button
                    className="text-red-400 hover:text-red-300"
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
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-800">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
              Approval History
            </h2>
          </div>
          {historyActions.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-600 text-center">No approvals yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 uppercase tracking-wide border-b border-slate-800">
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
                    className={`border-slate-800 hover:bg-slate-800/30 ${i < historyActions.length - 1 ? 'border-b' : ''}`}
                  >
                    <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">
                      {fmt(action.created_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <ActionTypePill type={action.action_type} />
                    </td>
                    <td className="px-4 py-2.5 text-slate-300 text-xs max-w-[200px] truncate">
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
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <ConfigPanel />
            <div className="mt-5 pt-4 border-t border-slate-800">
              <button
                className="px-4 py-2 rounded-lg text-sm font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/25 transition-colors"
                onClick={() => console.log('save config', agentId)}
              >
                Save configuration
                <StubBadge />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
