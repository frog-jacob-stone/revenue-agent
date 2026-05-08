/**
 * WorkflowTrace — audit-event timeline view of a LangGraph workflow.
 *
 * Default state: collapsed — shows a one-line summary like "12 events · awaiting approval".
 * Click the header to expand the full event list.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import { getWorkflowTrace } from '../api';
import type { TraceEvent } from '../types';
import StubBadge from './shared/StubBadge';

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

const EVENT_COLOR: Record<string, string> = {
  'workflow.started': 'text-cyan-400',
  'workflow.completed': 'text-emerald-400',
  'workflow.failed': 'text-red-400',
  'workflow.paused': 'text-amber-400',
  'workflow.resumed': 'text-cyan-400',
  'node.entered': 'text-slate-400',
  'node.exited': 'text-slate-300',
  'node.failed': 'text-red-400',
  'approval.requested': 'text-amber-400',
  'approval.granted': 'text-emerald-400',
  'approval.rejected': 'text-red-400',
  'approval.executed': 'text-emerald-400',
  'agent.invoked': 'text-cyan-400',
  'agent.completed': 'text-emerald-400',
  'agent.failed': 'text-red-400',
};

function EventRow({ event }: { event: TraceEvent }) {
  const color = EVENT_COLOR[event.event_type] ?? 'text-slate-400';
  const node = (event.payload?.node as string | undefined) ?? null;
  return (
    <div className="px-4 py-2 border-b border-slate-800/60 last:border-b-0">
      <div className="flex items-start gap-3">
        <span className="text-[11px] text-slate-600 font-mono w-20 shrink-0 pt-0.5">
          {fmtTime(event.occurred_at)}
        </span>
        <span className={`text-xs font-mono shrink-0 ${color}`}>
          {event.event_type}
        </span>
        {node && (
          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-slate-800 text-slate-400 border border-slate-700">
            {node}
          </span>
        )}
        {event.actor && event.actor !== 'orchestrator' && (
          <span className="text-[11px] text-slate-500 truncate">{event.actor}</span>
        )}
      </div>
    </div>
  );
}

interface WorkflowTraceProps {
  workflowId: string | null | undefined;
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

  const events = data?.events ?? [];

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

  const eventWord = events.length === 1 ? 'event' : 'events';
  const summaryParts = [`${events.length} ${eventWord}`];
  if (data.status) summaryParts.push(data.status.replace('_', ' '));
  const summary = summaryParts.join(' · ');

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
        events.length === 0 ? (
          <p className="px-5 py-4 text-xs text-slate-500">No events yet.</p>
        ) : (
          <div>
            {events.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
          </div>
        )
      )}
    </div>
  );
}
