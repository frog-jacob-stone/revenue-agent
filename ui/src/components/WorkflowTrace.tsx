/**
 * WorkflowTrace — flat-list view of every action in an orchestrated workflow.
 *
 * Renders each step as a row with an icon (per step_kind), summary, status,
 * attempt count, and duration. Failed/superseded retry attempts are shown
 * dimmed. Critique feedback is inline-collapsible.
 *
 * Phase C ships the flat list. Phase F upgrades to a tree with retry grouping.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ChevronDown,
  ChevronRight,
  Cog,
  Brain,
  Search,
  User,
  Send,
  Loader2,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
} from 'lucide-react';
import { getWorkflowTrace } from '../api';
import type { StepKind, TraceAction } from '../types';
import StubBadge from './shared/StubBadge';

const STEP_ICON: Record<StepKind, typeof Cog> = {
  tool_call: Cog,
  llm_step: Brain,
  critique: Search,
  checkpoint: User,
  execution: Send,
};

const STEP_LABEL: Record<StepKind, string> = {
  tool_call: 'tool',
  llm_step: 'llm',
  critique: 'critique',
  checkpoint: 'review',
  execution: 'execute',
};

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

function StatusIcon({ status }: { status: TraceAction['status'] }) {
  switch (status) {
    case 'completed':
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" aria-label="completed" />;
    case 'failed':
      return <AlertTriangle className="w-3.5 h-3.5 text-red-400" aria-label="failed" />;
    case 'rejected':
      return <XCircle className="w-3.5 h-3.5 text-red-400" aria-label="rejected" />;
    case 'executing':
      return <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" aria-label="executing" />;
    case 'proposed':
    case 'approved':
      return <Clock className="w-3.5 h-3.5 text-amber-400" aria-label={status} />;
    default:
      return null;
  }
}

function StepRow({ action, isRetried }: { action: TraceAction; isRetried: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const hasCritique = action.step_kind === 'critique' && action.critique_result;
  const Icon = action.step_kind ? STEP_ICON[action.step_kind] : Cog;
  const label = action.step_kind ? STEP_LABEL[action.step_kind] : 'step';
  const muted = isRetried || action.status === 'failed' || action.status === 'rejected';

  return (
    <div
      className={`px-4 py-2.5 border-b border-slate-800 last:border-b-0 ${
        muted ? 'opacity-50' : ''
      }`}
    >
      <div className="flex items-start gap-3">
        <div className="flex items-center gap-2 shrink-0 pt-0.5">
          <span className="font-mono text-xs text-slate-600 w-6 text-right">
            {action.sequence}
          </span>
          <Icon className="w-3.5 h-3.5 text-slate-500" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-800 text-slate-400 border border-slate-700`}
            >
              {label}
            </span>
            <StatusIcon status={action.status} />
            <p
              className={`text-sm truncate ${
                muted ? 'text-slate-500 line-through' : 'text-slate-200'
              }`}
            >
              {action.summary}
            </p>
          </div>

          {action.attempt_number > 1 && (
            <p className="text-[11px] text-slate-500 mt-0.5">
              attempt {action.attempt_number}
              {action.max_attempts ? ` of ${action.max_attempts}` : ''}
            </p>
          )}

          {hasCritique && (
            <button
              onClick={() => setExpanded((e) => !e)}
              className="mt-1 inline-flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              {expanded ? (
                <ChevronDown className="w-3 h-3" />
              ) : (
                <ChevronRight className="w-3 h-3" />
              )}
              critique{' '}
              {action.critique_result?.passed ? (
                <span className="text-emerald-400">passed</span>
              ) : (
                <span className="text-amber-400">failed</span>
              )}
              {typeof action.critique_result?.score === 'number' && (
                <span className="text-slate-500">
                  · score {action.critique_result.score.toFixed(2)}
                </span>
              )}
            </button>
          )}

          {hasCritique && expanded && action.critique_result && (
            <div className="mt-1.5 ml-4 text-[11px] text-slate-400 space-y-1 border-l-2 border-slate-700 pl-3">
              {action.critique_result.feedback && (
                <p className="leading-relaxed">{action.critique_result.feedback}</p>
              )}
              {action.critique_result.issues && action.critique_result.issues.length > 0 && (
                <ul className="list-disc list-inside text-amber-400/80 space-y-0.5">
                  {action.critique_result.issues.map((issue, i) => (
                    <li key={i}>{issue}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <span className="text-[11px] text-slate-600 shrink-0 pt-1 font-mono">
          {fmtDuration(action.duration_ms)}
        </span>
      </div>
    </div>
  );
}

interface WorkflowTraceProps {
  workflowId: string | null | undefined;
}

export default function WorkflowTrace({ workflowId }: WorkflowTraceProps) {
  const enabled = Boolean(workflowId);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['workflow-trace', workflowId],
    queryFn: () => getWorkflowTrace(workflowId as string),
    enabled,
  });

  // No workflow id → unwired surface; show a stub badge so reviewers can tell.
  if (!enabled) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3 flex items-center">
          Workflow Trace
          <StubBadge />
        </h2>
        <p className="text-xs text-slate-500">
          Trace appears here once this surface is wired to a real workflow.
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">
          Workflow Trace
        </h2>
        <div className="flex items-center text-xs text-slate-500">
          <Loader2 className="w-3.5 h-3.5 mr-2 animate-spin" />
          Loading trace…
        </div>
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">
          Workflow Trace
        </h2>
        <p className="text-xs text-red-400">
          Could not load trace: {(error as Error)?.message ?? 'unknown error'}
        </p>
      </div>
    );
  }

  const { actions } = data;
  // A retry exists when this action's id appears as another action's retry_of_action_id.
  const supersededIds = new Set(
    actions.filter((a) => a.retry_of_action_id).map((a) => a.retry_of_action_id as string),
  );
  const retryCount = actions.filter((a) => a.attempt_number > 1).length;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl">
      <div className="px-5 py-4 border-b border-slate-800 flex items-center justify-between">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">
          Workflow Trace
        </h2>
        <p className="text-xs text-slate-500">
          {actions.length} step{actions.length === 1 ? '' : 's'}
          {retryCount > 0 && ` · ${retryCount} retr${retryCount === 1 ? 'y' : 'ies'}`}
          {data.status && ` · ${data.status.replace('_', ' ')}`}
        </p>
      </div>

      {actions.length === 0 ? (
        <p className="px-5 py-4 text-xs text-slate-500">No steps yet.</p>
      ) : (
        <div>
          {actions.map((action) => (
            <StepRow
              key={action.id}
              action={action}
              isRetried={supersededIds.has(action.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
