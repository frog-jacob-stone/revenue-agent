import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Action, RiskLevel } from '../types';
import { approveAction, rejectAction } from '../api';
import EditBodyModal from './EditBodyModal';

interface Props {
  action: Action;
  status: string;
}

const RISK_COLORS: Record<RiskLevel, string> = {
  high: 'bg-red-500',
  medium: 'bg-amber-400',
  low: 'bg-green-500',
};

const ACTION_TYPE_LABELS: Record<string, string> = {
  research: 'research',
  send_email: 'send_email',
  create_hubspot_record: 'create_hubspot',
  update_hubspot_record: 'update_hubspot',
  publish_content: 'publish',
  generate_document: 'gen_doc',
  write_rev_rec: 'rev_rec',
  other: 'other',
};

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function displayValue(v: unknown): string {
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

export default function ActionCard({ action, status }: Props) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editedPayload, setEditedPayload] = useState<Record<string, unknown>>(
    action.proposed_payload,
  );
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [showEditModal, setShowEditModal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const isPending = status === 'proposed';
  const riskColor = action.risk_level ? RISK_COLORS[action.risk_level] : 'bg-gray-300';

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['actions'] });

  const handleApprove = async () => {
    setLoading(true);
    setError(null);
    try {
      await approveAction(action.id, 'system', editedPayload);
      await invalidate();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleReject = async () => {
    if (!rejectReason.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await rejectAction(action.id, rejectReason.trim());
      await invalidate();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const handleFieldChange = (key: string, rawValue: string) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(rawValue);
    } catch {
      parsed = rawValue;
    }
    setEditedPayload((prev) => ({ ...prev, [key]: parsed }));
  };

  const handleBodySave = (value: unknown) => {
    if ('body' in editedPayload) {
      setEditedPayload((prev) => ({ ...prev, body: value }));
    } else if (typeof value === 'object' && value !== null) {
      setEditedPayload(value as Record<string, unknown>);
    }
  };

  return (
    <>
      {showEditModal && (
        <EditBodyModal
          initialValue={editedPayload.body ?? editedPayload}
          onSave={handleBodySave}
          onClose={() => setShowEditModal(false)}
        />
      )}
      <div className="bg-white border border-gray-200 rounded-md overflow-hidden">
        {/* Collapsed header — always visible */}
        <button
          className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-gray-50 transition-colors"
          onClick={() => setExpanded((e) => !e)}
        >
          <span
            className={`shrink-0 w-2 h-2 rounded-full ${riskColor}`}
            title={action.risk_level ?? 'unknown risk'}
          />
          <span className="flex-1 text-sm text-gray-900 truncate min-w-0">
            {action.summary}
          </span>
          <span className="shrink-0 text-xs text-gray-400 font-mono">
            {action.agent_id.slice(0, 8)}
          </span>
          <span className="shrink-0 text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded font-mono">
            {ACTION_TYPE_LABELS[action.action_type] ?? action.action_type}
          </span>
          <span className="shrink-0 text-xs text-gray-400">
            {formatTimestamp(action.created_at)}
          </span>
          <span className="shrink-0 text-gray-300 text-xs select-none">
            {expanded ? '▲' : '▼'}
          </span>
        </button>

        {/* Expanded content */}
        {expanded && (
          <div className="border-t border-gray-100 px-4 py-4 space-y-4">
            {/* Reasoning callout */}
            {action.reasoning && (
              <div className="border-l-2 border-gray-200 pl-3 py-0.5">
                <p className="text-xs text-gray-500 leading-relaxed">{action.reasoning}</p>
              </div>
            )}

            {/* Editable payload fields */}
            <div>
              <p className="text-xs text-gray-400 mb-2 font-medium uppercase tracking-wide">
                Payload
              </p>
              <div className="space-y-1">
                {Object.entries(editedPayload).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-2">
                    <span className="text-xs text-gray-400 font-mono w-28 shrink-0 truncate">
                      {key}
                    </span>
                    <input
                      className="flex-1 text-xs font-mono bg-gray-50 border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-gray-300 disabled:text-gray-400"
                      value={displayValue(value)}
                      onChange={(e) => handleFieldChange(key, e.target.value)}
                      disabled={!isPending}
                    />
                  </div>
                ))}
              </div>
            </div>

            {/* Actions — pending only */}
            {isPending && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    onClick={handleApprove}
                    disabled={loading}
                    className="text-xs px-3 py-1.5 rounded bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-50 transition-colors"
                  >
                    {loading ? 'Processing…' : 'Approve'}
                  </button>
                  <button
                    onClick={() => setShowEditModal(true)}
                    disabled={loading}
                    className="text-xs px-3 py-1.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                  >
                    Edit body
                  </button>
                  <button
                    onClick={() => setShowReject((s) => !s)}
                    disabled={loading}
                    className="text-xs px-3 py-1.5 rounded border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors"
                  >
                    Reject
                  </button>
                </div>

                {showReject && (
                  <div className="space-y-1.5">
                    <textarea
                      className="w-full text-xs border border-gray-200 rounded p-2 h-16 resize-none focus:outline-none focus:ring-1 focus:ring-gray-300"
                      placeholder="Rejection reason…"
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={handleReject}
                        disabled={loading || !rejectReason.trim()}
                        className="text-xs px-3 py-1.5 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                      >
                        Confirm reject
                      </button>
                      <button
                        onClick={() => {
                          setShowReject(false);
                          setRejectReason('');
                        }}
                        className="text-xs px-3 py-1.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                )}

                {error && <p className="text-xs text-red-500">{error}</p>}
              </div>
            )}

            {/* Non-pending: read-only status */}
            {!isPending && (
              <div className="text-xs text-gray-400 space-y-0.5">
                <div>
                  Status:{' '}
                  <span className="font-medium text-gray-600">{action.status}</span>
                </div>
                {action.rejection_reason && (
                  <div>Reason: {action.rejection_reason}</div>
                )}
                {action.approved_by && (
                  <div>Approved by: {action.approved_by}</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
