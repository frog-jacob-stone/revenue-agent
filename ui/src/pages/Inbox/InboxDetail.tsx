import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { ArrowLeft, CheckCircle2, XCircle, Loader2, Pencil } from 'lucide-react';
import ActionTypeChip from '../../components/shared/ActionTypeChip';
import WorkflowTrace from '../../components/WorkflowTrace';
import EditBodyModal from '../../components/EditBodyModal';
import {
  approveApproval,
  getApproval,
  rejectApproval,
} from '../../api';
import type { InboxItem } from '../../types';

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function ActionDetail({ action }: { action: InboxItem }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [rejectReason, setRejectReason] = useState('');
  const [showReject, setShowReject] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [editedPayload, setEditedPayload] = useState<Record<string, unknown>>(action.proposed_payload);
  const [showEdit, setShowEdit] = useState(false);
  const isModified = JSON.stringify(editedPayload) !== JSON.stringify(action.proposed_payload);

  const refetch = () => {
    queryClient.invalidateQueries({ queryKey: ['inbox-item', action.id] });
    queryClient.invalidateQueries({ queryKey: ['workflow-trace', action.workflow_id] });
    queryClient.invalidateQueries({ queryKey: ['inbox'] });
    queryClient.invalidateQueries({ queryKey: ['inbox-pending-count'] });
  };

  const handleApprove = async () => {
    setBusy(true);
    setErr(null);
    try {
      await approveApproval(action.id, 'system', editedPayload);
      refetch();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (!rejectReason.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await rejectApproval(action.id, rejectReason.trim());
      refetch();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const isPending = action.status === 'pending';

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
          <ActionTypeChip type={action.action_type} />
          <span className="text-xs text-slate-500">
            node {action.node_name}
          </span>
          <span
            className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${
              isPending
                ? 'bg-amber-400/15 text-amber-400 border border-amber-400/30'
                : action.status === 'executed'
                ? 'bg-emerald-400/15 text-emerald-400 border border-emerald-400/30'
                : action.status === 'failed' || action.status === 'rejected'
                ? 'bg-red-400/15 text-red-400 border border-red-400/30'
                : 'bg-slate-700 text-slate-300 border border-slate-600'
            }`}
          >
            {action.status}
          </span>
        </div>
        <h1 className="text-lg font-semibold text-slate-100">{action.summary}</h1>
        {action.reasoning && (
          <p className="text-sm text-slate-400 leading-relaxed border-l-2 border-slate-700 pl-3">
            {action.reasoning}
          </p>
        )}
        <p className="text-xs text-slate-600">{fmt(action.created_at)}</p>
      </div>

      <WorkflowTrace workflowId={action.workflow_id} />

      {/* Payload */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <div className="flex items-center gap-2 mb-3">
          <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
            Action Payload
          </h2>
          {isModified && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-amber-400/15 text-amber-400 border border-amber-400/30">
              Modified
            </span>
          )}
          <button
            onClick={() => setShowEdit(true)}
            className="ml-auto text-slate-500 hover:text-slate-300 transition-colors"
            title="Edit payload"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
        </div>
        <pre className="text-xs text-emerald-400 bg-slate-950 rounded-lg p-4 overflow-x-auto leading-relaxed font-mono">
          {JSON.stringify(editedPayload, null, 2)}
        </pre>
      </div>

      {showEdit && (
        <EditBodyModal
          title="Edit payload"
          initialValue={editedPayload}
          onSave={(v) => setEditedPayload(v as Record<string, unknown>)}
          onClose={() => setShowEdit(false)}
        />
      )}

      {/* Actions */}
      {isPending && (
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center gap-3">
            <button
              disabled={busy}
              onClick={handleApprove}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/25 disabled:opacity-40 transition-colors"
            >
              {busy ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <CheckCircle2 className="w-4 h-4" />
              )}
              Approve
            </button>
            <button
              disabled={busy}
              onClick={() => setShowReject((s) => !s)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-red-500/15 text-red-400 border border-red-500/20 hover:bg-red-500/25 disabled:opacity-40 transition-colors"
            >
              <XCircle className="w-4 h-4" />
              Reject
            </button>
          </div>

          {showReject && (
            <div className="border-t border-slate-800 pt-4">
              <label className="block text-xs font-medium text-slate-400 mb-2">
                Reason for rejection
              </label>
              <textarea
                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 placeholder-slate-600 resize-none focus:outline-none focus:border-slate-600"
                rows={3}
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Explain why this action should not proceed…"
              />
              <button
                disabled={busy || !rejectReason.trim()}
                onClick={handleReject}
                className="mt-2 flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-40 transition-colors"
              >
                {busy && <Loader2 className="w-4 h-4 animate-spin" />}
                Confirm rejection
              </button>
            </div>
          )}

          {err && <p className="text-xs text-red-400">{err}</p>}
        </div>
      )}
    </div>
  );
}

export default function InboxDetail() {
  const { itemId } = useParams<{ itemId: string }>();
  const navigate = useNavigate();

  const { data: action, isLoading, isError } = useQuery<InboxItem>({
    queryKey: ['inbox-item', itemId],
    queryFn: () => getApproval(itemId as string),
    enabled: !!itemId,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="p-6 flex items-center text-slate-500">
        <Loader2 className="w-4 h-4 animate-spin mr-2" />
        Loading action…
      </div>
    );
  }

  if (isError || !action) {
    return (
      <div className="p-6 max-w-3xl mx-auto space-y-4">
        <button
          className="flex items-center gap-2 text-sm text-slate-400 hover:text-slate-200 transition-colors"
          onClick={() => navigate('/inbox')}
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Inbox
        </button>
        <p className="text-sm text-slate-500">Action not found.</p>
      </div>
    );
  }

  return <ActionDetail action={action} />;
}
