import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, CheckCircle2, XCircle, Edit2 } from 'lucide-react';
import { APPROVAL_ITEMS } from '../../mocks';
import AgentBadge from '../../components/shared/AgentBadge';
import ActionTypeChip from '../../components/shared/ActionTypeChip';
import StubBadge from '../../components/shared/StubBadge';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

export default function InboxDetail() {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();
  const item = APPROVAL_ITEMS.find((i) => i.id === itemId);

  if (!item) {
    return (
      <div className="p-6">
        <p className="text-slate-400">Item not found.</p>
        <button className="mt-4 text-cyan-400 text-sm hover:underline" onClick={() => navigate('/inbox')}>
          ← Back to Inbox
        </button>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <button
        className="flex items-center gap-2 text-sm text-slate-400 hover:text-slate-200 transition-colors"
        onClick={() => navigate('/inbox')}
      >
        <ArrowLeft className="w-4 h-4" />
        Back to Inbox
      </button>

      {/* Header */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <AgentBadge agentId={item.agentId} size="md" />
          <ActionTypeChip type={item.actionType} />
        </div>
        <h1 className="text-lg font-semibold text-slate-100">{item.target}</h1>
        <p className="text-sm text-slate-400 leading-relaxed">{item.summary}</p>
        <p className="text-xs text-slate-600">{fmt(item.timestamp)}</p>
      </div>

      {/* Payload */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">Action Payload</h2>
        <pre className="text-xs text-emerald-400 bg-slate-950 rounded-lg p-4 overflow-x-auto leading-relaxed font-mono">
          {JSON.stringify(item.payload, null, 2)}
        </pre>
      </div>

      {/* Actions */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-3">
          <button
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/25 transition-colors"
            onClick={() => console.log('edit & approve', item.id)}
          >
            <Edit2 className="w-4 h-4" />
            Edit & Approve
            <StubBadge />
          </button>
          <button
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/25 transition-colors"
            onClick={() => console.log('approve', item.id)}
          >
            <CheckCircle2 className="w-4 h-4" />
            Approve
            <StubBadge />
          </button>
        </div>

        <div className="border-t border-slate-800 pt-4">
          <label className="block text-xs font-medium text-slate-400 mb-2">Reject with reason</label>
          <textarea
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 resize-none focus:outline-none focus:border-slate-600"
            rows={3}
            placeholder="Explain why this action should not proceed…"
          />
          <button
            className="mt-2 flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-red-500/15 text-red-400 border border-red-500/20 hover:bg-red-500/25 transition-colors"
            onClick={() => console.log('reject', item.id)}
          >
            <XCircle className="w-4 h-4" />
            Submit Rejection
            <StubBadge />
          </button>
        </div>
      </div>
    </div>
  );
}
