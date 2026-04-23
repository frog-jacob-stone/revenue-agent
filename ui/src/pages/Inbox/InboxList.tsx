import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, XCircle, Filter, Loader2 } from 'lucide-react';
import { getActions, approveAction, rejectAction } from '../../api';
import type { Action, ActionType } from '../../types';
import StubBadge from '../../components/shared/StubBadge';
import EmptyState from '../../components/shared/EmptyState';

const ACTION_LABELS: Record<ActionType, string> = {
  research: 'research',
  send_email: 'send email',
  create_hubspot_record: 'hubspot create',
  update_hubspot_record: 'hubspot update',
  publish_content: 'publish',
  generate_document: 'gen doc',
  write_rev_rec: 'rev rec',
  configure_rev_rec_projects: 'rev rec setup',
  other: 'other',
};

function ActionTypeTag({ type }: { type: ActionType }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide bg-slate-700 text-slate-300 border border-slate-600">
      {ACTION_LABELS[type] ?? type}
    </span>
  );
}

function fmt(iso: string) {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const RISK_DOT: Record<string, string> = {
  high: 'bg-red-400',
  medium: 'bg-amber-400',
  low: 'bg-emerald-400',
};

/** Action-type-specific payload summary rendered below the main summary line. */
function PayloadContext({ action }: { action: Action }) {
  const p = action.proposed_payload;

  if (action.action_type === 'configure_rev_rec_projects') {
    const projects = (p.incomplete_projects as Array<{
      project_name: string;
      harvest_id: number | null;
      missing_fields: string[];
    }>) ?? [];
    return (
      <div className="mt-2 space-y-1">
        {projects.map((proj, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <span className="text-slate-400 font-medium min-w-0 truncate">{proj.project_name}</span>
            <span className="text-slate-600">—</span>
            <span className="text-amber-400">{proj.missing_fields.join(', ')}</span>
          </div>
        ))}
      </div>
    );
  }

  if (action.action_type === 'write_rev_rec') {
    const entries = (p.entries as Array<{
      'Project Name': string;
      'Billing Type': string;
      'Total Recognized Revenue': number;
      'Logged Hours': number;
    }>) ?? [];
    const total = p.total_recognized as number;
    const dateRec = p.date_recognized as string;
    return (
      <div className="mt-2 space-y-2">
        {dateRec && (
          <p className="text-xs text-slate-500">
            Period ending <span className="text-slate-300 font-medium">{dateRec}</span>
            {' · '}
            Total: <span className="text-emerald-400 font-medium">${Number(total ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}</span>
          </p>
        )}
        <div className="rounded border border-slate-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-slate-800/60 text-slate-500 text-left">
                <th className="px-3 py-1.5 font-medium">Project</th>
                <th className="px-3 py-1.5 font-medium">Type</th>
                <th className="px-3 py-1.5 font-medium text-right">Hours</th>
                <th className="px-3 py-1.5 font-medium text-right">Revenue</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i} className="border-t border-slate-800">
                  <td className="px-3 py-1.5 text-slate-300 truncate max-w-[180px]">{e['Project Name']}</td>
                  <td className="px-3 py-1.5 text-slate-500">{e['Billing Type']}</td>
                  <td className="px-3 py-1.5 text-slate-400 text-right">{(e['Logged Hours'] ?? 0).toFixed(1)}</td>
                  <td className="px-3 py-1.5 text-emerald-400 text-right font-medium">
                    ${(e['Total Recognized Revenue'] ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (action.reasoning) {
    return (
      <p className="mt-1.5 text-xs text-slate-500 line-clamp-2 border-l-2 border-slate-700 pl-2">
        {action.reasoning}
      </p>
    );
  }

  return null;
}

function ActionRow({ action, isLast }: { action: Action; isLast: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refetch = () => queryClient.invalidateQueries({ queryKey: ['actions', 'proposed'] });

  const handleApprove = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setLoading(true);
    setError(null);
    try {
      await approveAction(action.id, 'system', action.proposed_payload);
      await refetch();
    } catch (err) {
      setError((err as Error).message);
      setLoading(false);
    }
  };

  const handleRejectClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setShowReject((s) => !s);
    setRejectReason('');
    setError(null);
  };

  const handleRejectConfirm = async () => {
    if (!rejectReason.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await rejectAction(action.id, rejectReason.trim());
      await refetch();
    } catch (err) {
      setError((err as Error).message);
      setLoading(false);
    }
  };

  return (
    <div className={`border-slate-800 ${!isLast ? 'border-b' : ''}`}>
      <div
        className="px-5 py-4 cursor-pointer hover:bg-slate-800/50 transition-colors"
        onClick={() => navigate(`/inbox/${action.id}`)}
      >
        {/* Top row: risk dot + type tag + timestamp */}
        <div className="flex items-center gap-3 mb-2">
          <span
            className={`w-2 h-2 rounded-full shrink-0 ${RISK_DOT[action.risk_level ?? 'low'] ?? 'bg-slate-500'}`}
            title={`Risk: ${action.risk_level ?? 'unknown'}`}
          />
          <ActionTypeTag type={action.action_type} />
          <span className="text-xs text-slate-500 ml-auto">{fmt(action.created_at)}</span>
        </div>

        {/* Summary + action buttons */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-slate-200">{action.summary}</p>
            <PayloadContext action={action} />
          </div>

          <div
            className="flex items-center gap-2 flex-shrink-0 mt-0.5"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              disabled={loading}
              onClick={handleApprove}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/25 disabled:opacity-40 transition-colors"
            >
              {loading && !showReject ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <CheckCircle2 className="w-3 h-3" />
              )}
              Approve
            </button>
            <button
              disabled={loading}
              onClick={handleRejectClick}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:opacity-40 ${
                showReject
                  ? 'bg-red-500/20 text-red-300 border-red-500/30'
                  : 'bg-red-500/15 text-red-400 border-red-500/20 hover:bg-red-500/25'
              }`}
            >
              <XCircle className="w-3 h-3" />
              Reject
            </button>
          </div>
        </div>

        {error && <p className="text-xs text-red-400 mt-2">{error}</p>}
      </div>

      {showReject && (
        <div
          className="px-5 pb-4 space-y-2 bg-slate-900/60"
          onClick={(e) => e.stopPropagation()}
        >
          <textarea
            autoFocus
            className="w-full text-xs bg-slate-800 border border-slate-700 text-slate-300 rounded-lg p-2.5 h-16 resize-none focus:outline-none focus:ring-1 focus:ring-slate-500 placeholder:text-slate-600"
            placeholder="Reason for rejection…"
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              disabled={loading || !rejectReason.trim()}
              onClick={handleRejectConfirm}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 transition-colors"
            >
              {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
              Confirm reject
            </button>
            <button
              onClick={() => { setShowReject(false); setRejectReason(''); }}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-400 hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function InboxList() {
  const { data: actions = [], isLoading, isError } = useQuery({
    queryKey: ['actions', 'proposed'],
    queryFn: () => getActions('proposed'),
    refetchInterval: 15_000,
  });

  return (
    <div className="p-6 space-y-4 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Approval Inbox</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            {isLoading ? 'Loading…' : `${actions.length} item${actions.length === 1 ? '' : 's'} awaiting review`}
          </p>
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

      {isLoading && (
        <div className="flex items-center justify-center py-16 text-slate-500">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          <span className="text-sm">Loading actions…</span>
        </div>
      )}

      {isError && (
        <div className="bg-red-500/10 border border-red-500/20 rounded-xl px-5 py-4">
          <p className="text-sm text-red-400">Failed to load actions. Is the API running?</p>
        </div>
      )}

      {!isLoading && !isError && actions.length === 0 && (
        <EmptyState title="Inbox clear" description="No pending approvals. Nice work." />
      )}

      {!isLoading && !isError && actions.length > 0 && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          {actions.map((action, i) => (
            <ActionRow key={action.id} action={action} isLast={i === actions.length - 1} />
          ))}
        </div>
      )}
    </div>
  );
}
