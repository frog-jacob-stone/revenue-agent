import { useState } from 'react';
import { Filter, Download, ChevronDown, ChevronRight } from 'lucide-react';
import { AUDIT_ENTRIES } from '../mocks';
import AgentBadge from '../components/shared/AgentBadge';
import ActionTypeChip from '../components/shared/ActionTypeChip';
import StatusChip from '../components/shared/StatusChip';
import StubBadge from '../components/shared/StubBadge';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function AuditLog() {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <div className="p-6 space-y-4 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Audit Log</h1>
          <p className="text-sm text-slate-400 mt-0.5">{AUDIT_ENTRIES.length} entries</p>
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
        <div className="flex items-center gap-2 ml-2">
          <select className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1">
            <option>All agents</option>
          </select>
          <StubBadge />
        </div>
        <div className="flex items-center gap-2">
          <input type="date" className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1" />
          <StubBadge />
        </div>
        <div className="flex items-center gap-2">
          <select className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1">
            <option>All outcomes</option>
          </select>
          <StubBadge />
        </div>
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
            {AUDIT_ENTRIES.map((entry, i) => (
              <>
                <tr
                  key={entry.id}
                  className={`border-slate-800 hover:bg-slate-800/40 cursor-pointer transition-colors ${i < AUDIT_ENTRIES.length - 1 || expanded === entry.id ? 'border-b' : ''}`}
                  onClick={() => setExpanded(expanded === entry.id ? null : entry.id)}
                >
                  <td className="px-3 py-2.5 text-slate-600">
                    {expanded === entry.id
                      ? <ChevronDown className="w-3.5 h-3.5" />
                      : <ChevronRight className="w-3.5 h-3.5" />
                    }
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">{fmt(entry.timestamp)}</td>
                  <td className="px-4 py-2.5"><AgentBadge agentId={entry.agentId} /></td>
                  <td className="px-4 py-2.5"><ActionTypeChip type={entry.actionType} /></td>
                  <td className="px-4 py-2.5 text-slate-300 text-xs max-w-[200px] truncate">{entry.target}</td>
                  <td className="px-4 py-2.5"><StatusChip status={entry.outcome} /></td>
                  <td className="px-4 py-2.5 text-slate-500 text-xs max-w-[200px] truncate">{entry.reason}</td>
                </tr>
                {expanded === entry.id && (
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
