import { useState, useEffect } from 'react';
import { Filter, Download, ChevronDown, ChevronRight } from 'lucide-react';
import { getAuditLog, listAgents } from '../api';
import type { AuditLogEntry } from '../api';
import type { AgentRecord } from '../types';
import type { AgentId } from '../mocks';
import AgentBadge from '../components/shared/AgentBadge';
import ActionTypeChip from '../components/shared/ActionTypeChip';
import StatusChip from '../components/shared/StatusChip';
import StubBadge from '../components/shared/StubBadge';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function AuditLog() {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [agentSlug, setAgentSlug] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [outcome, setOutcome] = useState('');

  useEffect(() => {
    listAgents().then(setAgents).catch(console.error);
  }, []);

  useEffect(() => {
    setLoading(true);
    getAuditLog({
      agent_slug: agentSlug || undefined,
      from_date: fromDate || undefined,
      outcome: outcome || undefined,
    })
      .then(setEntries)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [agentSlug, fromDate, outcome]);

  return (
    <div className="p-6 space-y-4 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Audit Log</h1>
          <p className="text-sm text-slate-400 mt-0.5">{loading ? '…' : `${entries.length} entries`}</p>
        </div>
        <button
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
          onClick={() => console.log('export csv')}
        >
          <Download className="w-3.5 h-3.5" />
          Export CSV
          <StubBadge />
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-3 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3">
        <Filter className="w-4 h-4 text-slate-500" />
        <span className="text-xs text-slate-500 font-medium uppercase tracking-wide">Filters</span>
        <select
          className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1 ml-2"
          value={agentSlug}
          onChange={e => setAgentSlug(e.target.value)}
        >
          <option value="">All agents</option>
          {agents.map(a => (
            <option key={a.slug} value={a.slug}>{a.name}</option>
          ))}
        </select>
        <input
          type="date"
          className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1"
          value={fromDate}
          onChange={e => setFromDate(e.target.value)}
        />
        <select
          className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1"
          value={outcome}
          onChange={e => setOutcome(e.target.value)}
        >
          <option value="">All outcomes</option>
          <option value="pending">Pending</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
              <th className="w-6 px-3 py-3" />
              <th className="text-left px-4 py-3 font-medium">Timestamp</th>
              <th className="text-left px-4 py-3 font-medium">Agent</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-left px-4 py-3 font-medium">Target</th>
              <th className="text-left px-4 py-3 font-medium">Outcome</th>
              <th className="text-left px-4 py-3 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-xs text-slate-500 animate-pulse">Loading…</td>
              </tr>
            ) : entries.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-xs text-slate-500">No audit entries found.</td>
              </tr>
            ) : entries.map((entry, i) => (
              <>
                <tr
                  key={entry.id}
                  className={`border-slate-800 hover:bg-slate-800/40 cursor-pointer transition-colors ${i < entries.length - 1 || expanded === String(entry.id) ? 'border-b' : ''}`}
                  onClick={() => setExpanded(expanded === String(entry.id) ? null : String(entry.id))}
                >
                  <td className="px-3 py-2.5 text-slate-600">
                    {expanded === String(entry.id)
                      ? <ChevronDown className="w-3.5 h-3.5" />
                      : <ChevronRight className="w-3.5 h-3.5" />
                    }
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">{fmt(entry.timestamp)}</td>
                  <td className="px-4 py-2.5">
                    {entry.agent_slug
                      ? <AgentBadge agentId={entry.agent_slug as AgentId} />
                      : <span className="text-xs text-slate-600">—</span>
                    }
                  </td>
                  <td className="px-4 py-2.5"><ActionTypeChip type={entry.action_type ?? entry.event_type} /></td>
                  <td className="px-4 py-2.5 text-slate-300 text-xs max-w-[200px] truncate">{entry.target ?? entry.event_type}</td>
                  <td className="px-4 py-2.5"><StatusChip status={entry.outcome} /></td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs max-w-[200px] truncate">{entry.reason ?? '—'}</td>
                </tr>
                {expanded === String(entry.id) && (
                  <tr key={`${entry.id}-expand`} className="border-b border-slate-800 bg-slate-950/50">
                    <td colSpan={7} className="px-8 py-4">
                      <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold mb-2">Payload</p>
                      <pre className="text-xs text-emerald-400 bg-slate-950 rounded-lg p-3 overflow-x-auto font-mono leading-relaxed">
                        {JSON.stringify(entry.payload, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
