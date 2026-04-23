import { useNavigate } from 'react-router-dom';
import { CheckCircle2, XCircle, Filter } from 'lucide-react';
import { APPROVAL_ITEMS } from '../../mocks';
import AgentBadge from '../../components/shared/AgentBadge';
import ActionTypeChip from '../../components/shared/ActionTypeChip';
import StubBadge from '../../components/shared/StubBadge';
import EmptyState from '../../components/shared/EmptyState';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function InboxList() {
  const navigate = useNavigate();
  const pending = APPROVAL_ITEMS.filter((i) => i.status === 'pending');

  return (
    <div className="p-6 space-y-4 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Approval Inbox</h1>
          <p className="text-sm text-slate-400 mt-0.5">{pending.length} items awaiting review</p>
        </div>
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
          <select className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1">
            <option>All types</option>
          </select>
          <StubBadge />
        </div>
        <div className="flex items-center gap-2">
          <select className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1">
            <option>Pending</option>
          </select>
          <StubBadge />
        </div>
      </div>

      {/* List */}
      {pending.length === 0 ? (
        <EmptyState title="Inbox clear" description="No pending approvals. Nice work." />
      ) : (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          {pending.map((item, i) => (
            <div
              key={item.id}
              className={`px-5 py-4 cursor-pointer hover:bg-slate-800/50 transition-colors border-slate-800 ${i < pending.length - 1 ? 'border-b' : ''}`}
              onClick={() => navigate(`/inbox/${item.id}`)}
            >
              <div className="flex items-center gap-3 mb-2">
                <AgentBadge agentId={item.agentId} />
                <ActionTypeChip type={item.actionType} />
                <span className="text-xs text-slate-500 ml-auto">{fmt(item.timestamp)}</span>
              </div>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-200 truncate">{item.target}</p>
                  <p className="text-xs text-slate-400 mt-0.5 line-clamp-1">{item.summary}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/25 transition-colors"
                    onClick={() => console.log('approve', item.id)}
                  >
                    <CheckCircle2 className="w-3 h-3" />
                    Approve
                    <StubBadge />
                  </button>
                  <button
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-500/15 text-red-400 border border-red-500/20 hover:bg-red-500/25 transition-colors"
                    onClick={() => console.log('reject', item.id)}
                  >
                    <XCircle className="w-3 h-3" />
                    Reject
                    <StubBadge />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
