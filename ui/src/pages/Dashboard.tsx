import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Play } from 'lucide-react';
import { listAgents, getAuditLog } from '../api';
import type { AuditLogEntry } from '../api';
import type { AgentRecord } from '../types';
import { agentColor } from '../agents';
import StatusChip from '../components/shared/StatusChip';
import ActionTypeChip from '../components/shared/ActionTypeChip';
import AgentBadge from '../components/shared/AgentBadge';
import StubBadge from '../components/shared/StubBadge';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * The cards show what `AgentRecord` actually carries: name, description, and
 * whether the agent is active. The previous version also showed last-run time,
 * actions-taken-today, and an idle/running/error status — all invented in the
 * mock fixture, none of them recorded anywhere. They are gone rather than
 * faked; the audit feed below is the real answer to "is anything happening".
 */
export default function Dashboard() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [activity, setActivity] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAgents(), getAuditLog({ limit: 10 })])
      .then(([agentRows, auditRows]) => {
        if (cancelled) return;
        setAgents(agentRows);
        setActivity(auditRows.slice(0, 10));
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Heading */}
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Dashboard</h1>
        <p className="text-sm text-slate-600 mt-0.5">Revenue agent status overview</p>
      </div>

      {/* Agent Cards */}
      {loading ? (
        <p className="text-xs text-slate-500 animate-pulse">Loading agents…</p>
      ) : agents.length === 0 ? (
        <p className="text-sm text-slate-500">No agents registered.</p>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {agents.map((agent) => (
            <div
              key={agent.slug}
              className="bg-white border border-slate-200 rounded-xl p-4 hover:border-slate-300 transition-colors cursor-pointer"
              onClick={() => navigate(`/settings/agents/${agent.slug}`)}
            >
              <div className="flex items-start justify-between mb-3 gap-2">
                <div className="flex items-start gap-2 min-w-0">
                  <span
                    className="w-2.5 h-2.5 rounded-full mt-1 flex-shrink-0"
                    style={{ backgroundColor: agentColor(agent.slug) }}
                  />
                  <div className="min-w-0">
                    <p className="font-medium text-slate-900 text-sm">{agent.name}</p>
                    <p className="text-xs text-slate-500 mt-0.5 line-clamp-2">{agent.description ?? '—'}</p>
                  </div>
                </div>
                <StatusChip status={agent.is_active ? 'idle' : 'disabled'} />
              </div>

              <button
                className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors"
                onClick={(e) => { e.stopPropagation(); console.log('trigger agent', agent.slug); }}
              >
                <Play className="w-3 h-3" />
                Trigger manually
                <StubBadge />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Recent Activity */}
      <div>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Recent Activity</h2>
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
                <th className="text-left px-4 py-3 font-medium">Time</th>
                <th className="text-left px-4 py-3 font-medium">Agent</th>
                <th className="text-left px-4 py-3 font-medium">Type</th>
                <th className="text-left px-4 py-3 font-medium">Target</th>
                <th className="text-left px-4 py-3 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-xs text-slate-500 animate-pulse">Loading…</td></tr>
              ) : activity.length === 0 ? (
                <tr><td colSpan={5} className="px-4 py-8 text-center text-xs text-slate-500">No activity recorded yet.</td></tr>
              ) : activity.map((entry, i) => (
                <tr
                  key={entry.id}
                  className={`border-b border-slate-200 hover:bg-slate-50 transition-colors ${i === activity.length - 1 ? 'border-b-0' : ''}`}
                >
                  <td className="px-4 py-2.5 text-slate-500 text-xs whitespace-nowrap">{fmt(entry.timestamp)}</td>
                  <td className="px-4 py-2.5">
                    {entry.agent_slug
                      ? <AgentBadge agentId={entry.agent_slug} />
                      : <span className="text-xs text-slate-400">—</span>
                    }
                  </td>
                  <td className="px-4 py-2.5"><ActionTypeChip type={entry.action_type ?? entry.event_type} /></td>
                  <td className="px-4 py-2.5 text-slate-700 text-xs max-w-xs truncate">{entry.target ?? entry.event_type}</td>
                  <td className="px-4 py-2.5"><StatusChip status={entry.outcome} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
