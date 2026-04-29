/**
 * WorkflowTrace — tree-grouped view of every action in an orchestrated workflow.
 *
 * Each chain step renders as a root row. Retry attempts (siblings linked via
 * `retry_of_action_id`) render as indented children under their root, with the
 * original draft muted and the latest attempt highlighted. Critique results
 * are inline-collapsible.
 *
 * Default state: collapsed — shows a one-line summary like
 *   "8 steps · 2 retries · awaiting approval".
 * Click the header to expand the full tree.
 */
import { useMemo, useState } from 'react';
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
  CornerDownRight,
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

interface ActionGroup {
  root: TraceAction;
  retries: TraceAction[]; // ordered by attempt_number ascending
}

/** Group actions by their retry root.
 *
 * Walks `retry_of_action_id` chains to find each action's root (the first
 * attempt). Roots become groups; non-root attempts are appended to their
 * group's retries list, ordered by attempt_number.
 */
function buildGroups(actions: TraceAction[]): ActionGroup[] {
  const byId = new Map(actions.map((a) => [a.id, a]));
  const groups = new Map<string, ActionGroup>();
  const order: string[] = [];

  for (const a of actions) {
    let root = a;
    while (root.retry_of_action_id && byId.has(root.retry_of_action_id)) {
      root = byId.get(root.retry_of_action_id)!;
    }
    if (!groups.has(root.id)) {
      groups.set(root.id, { root, retries: [] });
      order.push(root.id);
    }
    if (a.id !== root.id) {
      groups.get(root.id)!.retries.push(a);
    }
  }
  for (const g of groups.values()) {
    g.retries.sort((x, y) => x.attempt_number - y.attempt_number);
  }
  return order.map((id) => groups.get(id)!);
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

interface AttemptRowProps {
  action: TraceAction;
  /** True for retry rows (rendered indented). */
  isRetry: boolean;
  /** True when a more recent attempt of this step exists — mute the row. */
  superseded: boolean;
}

function AttemptRow({ action, isRetry, superseded }: AttemptRowProps) {
  const [expanded, setExpanded] = useState(false);
  const hasCritique = action.step_kind === 'critique' && action.critique_result;
  const Icon = action.step_kind ? STEP_ICON[action.step_kind] : Cog;
  const label = action.step_kind ? STEP_LABEL[action.step_kind] : 'step';
  const muted = superseded || action.status === 'failed' || action.status === 'rejected';

  return (
    <div
      className={`px-4 py-2 ${
        isRetry ? 'pl-12 bg-slate-950/40 border-l border-slate-800/60' : ''
      } ${muted ? 'opacity-50' : ''}`}
    >
      <div className="flex items-start gap-3">
        <div className="flex items-center gap-2 shrink-0 pt-0.5">
          {isRetry && <CornerDownRight className="w-3 h-3 text-slate-700" />}
          <span className="font-mono text-xs text-slate-600 w-6 text-right">
            {action.sequence}
          </span>
          <Icon className="w-3.5 h-3.5 text-slate-500" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-800 text-slate-400 border border-slate-700">
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
            {action.attempt_number > 1 && (
              <span className="text-[10px] text-slate-500 font-mono">
                attempt {action.attempt_number}
                {action.max_attempts ? `/${action.max_attempts}` : ''}
              </span>
            )}
          </div>

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

function GroupRow({ group }: { group: ActionGroup }) {
  // The latest attempt is the one rendered most prominently; everything before
  // it is superseded.
  const all = [group.root, ...group.retries];
  const latestId = all[all.length - 1].id;

  return (
    <div className="border-b border-slate-800 last:border-b-0">
      {all.map((action) => (
        <AttemptRow
          key={action.id}
          action={action}
          isRetry={action.id !== group.root.id}
          superseded={action.id !== latestId && all.length > 1}
        />
      ))}
    </div>
  );
}

interface WorkflowTraceProps {
  workflowId: string | null | undefined;
  /** Default to true to open expanded; false matches the directive's
   * "collapsed by default with a one-line summary" behavior. */
  defaultExpanded?: boolean;
}

export default function WorkflowTrace({
  workflowId,
  defaultExpanded = false,
}: WorkflowTraceProps) {
  const enabled = Boolean(workflowId);
  const [expanded, setExpanded] = useState(defaultExpanded);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['workflow-trace', workflowId],
    queryFn: () => getWorkflowTrace(workflowId as string),
    enabled,
  });

  const groups = useMemo(() => (data ? buildGroups(data.actions) : []), [data]);
  const retryCount = useMemo(
    () => groups.reduce((acc, g) => acc + g.retries.length, 0),
    [groups],
  );

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

  const summary = (() => {
    const stepWord = groups.length === 1 ? 'step' : 'steps';
    const parts = [`${groups.length} ${stepWord}`];
    if (retryCount > 0) {
      parts.push(`${retryCount} retr${retryCount === 1 ? 'y' : 'ies'}`);
    }
    if (data.status) parts.push(data.status.replace('_', ' '));
    return parts.join(' · ');
  })();

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="w-full px-5 py-4 border-b border-slate-800 flex items-center justify-between hover:bg-slate-800/40 transition-colors text-left"
      >
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide flex items-center gap-2">
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5" />
          )}
          Workflow Trace
        </h2>
        <p className="text-xs text-slate-500">{summary}</p>
      </button>

      {expanded && (
        groups.length === 0 ? (
          <p className="px-5 py-4 text-xs text-slate-500">No steps yet.</p>
        ) : (
          <div>
            {groups.map((group) => (
              <GroupRow key={group.root.id} group={group} />
            ))}
          </div>
        )
      )}
    </div>
  );
}
