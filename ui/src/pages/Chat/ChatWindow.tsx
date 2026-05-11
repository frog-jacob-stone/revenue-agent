import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Send,
  Info,
  ChevronRight,
  ChevronDown,
  CheckCircle2,
  XCircle,
  Loader2,
} from 'lucide-react';
import { getChatMessages, sendChatMessage } from '../../api';
import type { ChatStreamEvent } from '../../api';
import type {
  ActivityLine,
  AgentRecord,
  ChatPersistedMessage,
  ChatMessageStatus,
} from '../../types';
import { labelForKind, labelForNode } from './nodeLabels';

const AGENT_COLORS: Record<string, string> = {
  'sdr-researcher': '#6366f1',
  'outreach-agent': '#06b6d4',
  'content-writer': '#10b981',
  'content-orchestrator': '#10b981',
  'proposal-generator': '#f59e0b',
  'slide-deck-agent': '#ec4899',
  'revenue-recognition': '#8b5cf6',
};

interface Props {
  agentId: string;
  sessionId: string;
  agent: AgentRecord | null;
}

interface DisplayMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  activity?: ActivityLine[];
  createdAt: string;
  status: ChatMessageStatus;
  error?: string | null;
}

function fmt(iso: string) {
  return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

function compactTokens(payload: Record<string, unknown>): string | undefined {
  const total =
    (typeof payload?.total_tokens === 'number' && payload.total_tokens) ||
    (typeof (payload?.usage as { total_tokens?: number })?.total_tokens === 'number' &&
      (payload.usage as { total_tokens: number }).total_tokens) ||
    null;
  if (!total) return undefined;
  return total >= 1000 ? `${(total / 1000).toFixed(1)}k tokens` : `${total} tokens`;
}

function hydrate(msg: ChatPersistedMessage): DisplayMessage {
  return {
    id: `p-${msg.id}`,
    role: msg.role,
    content: msg.content,
    activity: msg.activity,
    createdAt: msg.created_at,
    status: msg.status,
    error: msg.error,
  };
}

export default function ChatWindow({ agentId, sessionId, agent }: Props) {
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [triggeredNotice, setTriggeredNotice] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const bottomRef = useRef<HTMLDivElement>(null);

  const color = AGENT_COLORS[agentId] ?? '#64748b';
  const agentName = agent?.name ?? agentId;
  const agentDescription = agent?.description ?? '';

  const messagesQuery = useQuery({
    queryKey: ['chat-messages', sessionId],
    queryFn: () => getChatMessages(sessionId),
    // Poll while a turn is in flight (any message with status='streaming').
    refetchInterval: (q) => {
      const data = q.state.data as ChatPersistedMessage[] | undefined;
      if (isSending) return false; // live stream is active in this tab
      return data?.some((m) => m.status === 'streaming') ? 3000 : false;
    },
  });

  // Hydrate from persisted messages whenever they refresh AND we're not
  // actively streaming locally (don't clobber the optimistic stream).
  useEffect(() => {
    if (isSending) return;
    if (!messagesQuery.data) return;
    setMessages(messagesQuery.data.map(hydrate));
  }, [messagesQuery.data, isSending]);

  // Reset notice + scroll on session change.
  useEffect(() => {
    setInput('');
    setTriggeredNotice(false);
    setExpanded({});
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isSending]);

  async function handleSend() {
    const text = input.trim();
    if (!text || isSending) return;
    if (messages.some((m) => m.status === 'streaming')) return;

    const nowIso = new Date().toISOString();
    const userMsg: DisplayMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text,
      createdAt: nowIso,
      status: 'complete',
    };
    const assistantId = `a-${Date.now()}`;
    const assistantMsg: DisplayMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      activity: [],
      createdAt: nowIso,
      status: 'streaming',
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput('');
    setIsSending(true);
    setTriggeredNotice(false);
    setExpanded((prev) => ({ ...prev, [assistantId]: true }));

    // Activity-builder cursor state — matches app/services/activity_builder.py
    let toolLineId: string | null = null;
    let workflowLineId: string | null = null;
    let workflowKind = '';
    let currentNodeLineId: string | null = null;
    let pendingAgentByLineId: string | null = null;
    let pendingAgentSlug: string | null = null;

    const updateAssistant = (mut: (m: DisplayMessage) => DisplayMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? mut(m) : m)));

    const pushLine = (line: ActivityLine) =>
      updateAssistant((m) => ({ ...m, activity: [...(m.activity ?? []), line] }));

    const patchLine = (id: string, patch: Partial<ActivityLine>) =>
      updateAssistant((m) => ({
        ...m,
        activity: (m.activity ?? []).map((l) => (l.id === id ? { ...l, ...patch } : l)),
      }));

    let triggered = false;

    const onEvent = (evt: ChatStreamEvent) => {
      switch (evt.type) {
        case 'delta':
          updateAssistant((m) => ({ ...m, content: m.content + evt.text }));
          break;
        case 'tool_call_started': {
          toolLineId = `tl-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
          pushLine({
            id: toolLineId,
            kind: 'tool',
            parentId: null,
            label: `Calling ${evt.name}`,
            status: 'running',
          });
          if (evt.name === 'trigger_revenue_recognition') triggered = true;
          break;
        }
        case 'workflow_started': {
          workflowLineId = `wf-${evt.workflow_id}`;
          workflowKind = evt.kind;
          pushLine({
            id: workflowLineId,
            kind: 'workflow',
            parentId: toolLineId,
            label: `Workflow: ${labelForKind(evt.kind)}`,
            status: 'running',
          });
          break;
        }
        case 'workflow_event': {
          const et = evt.event_type;
          if (et === 'node.entered') {
            const node = (evt.payload?.node as string | undefined) ?? '?';
            currentNodeLineId = `nd-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            pushLine({
              id: currentNodeLineId,
              kind: 'node',
              parentId: workflowLineId,
              label: labelForNode(workflowKind, node),
              status: 'running',
            });
          } else if (et === 'node.exited') {
            const node = (evt.payload?.node as string | undefined) ?? '?';
            currentNodeLineId = `nd-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            pushLine({
              id: currentNodeLineId,
              kind: 'node',
              parentId: workflowLineId,
              label: labelForNode(workflowKind, node),
              status: 'ok',
            });
          } else if (et === 'node.failed') {
            const node = (evt.payload?.node as string | undefined) ?? '?';
            currentNodeLineId = `nd-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            pushLine({
              id: currentNodeLineId,
              kind: 'node',
              parentId: workflowLineId,
              label: labelForNode(workflowKind, node),
              status: 'fail',
              detail: (evt.payload?.error as string) ?? undefined,
            });
          } else if (et === 'agent.invoked') {
            const slug = (evt.payload?.agent_slug as string) ?? evt.actor ?? 'agent';
            pendingAgentSlug = slug;
            pendingAgentByLineId = `ag-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
            pushLine({
              id: pendingAgentByLineId,
              kind: 'subagent',
              parentId: currentNodeLineId,
              label: slug,
              status: 'running',
            });
          } else if (et === 'agent.completed' || et === 'agent.failed') {
            const tokens = compactTokens(evt.payload ?? {});
            if (pendingAgentByLineId) {
              patchLine(pendingAgentByLineId, {
                status: et === 'agent.completed' ? 'ok' : 'fail',
                label: pendingAgentSlug ?? 'agent',
                detail: tokens,
              });
            } else {
              const slug = (evt.payload?.agent_slug as string) ?? evt.actor ?? 'agent';
              pushLine({
                id: `ag-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                kind: 'subagent',
                parentId: currentNodeLineId,
                label: slug,
                status: et === 'agent.completed' ? 'ok' : 'fail',
                detail: tokens,
              });
            }
            pendingAgentByLineId = null;
            pendingAgentSlug = null;
          } else if (et === 'workflow.paused') {
            pushLine({
              id: `pa-${Date.now()}`,
              kind: 'pause',
              parentId: workflowLineId,
              label: 'Awaiting approval',
              status: 'ok',
            });
          } else if (et === 'workflow.completed') {
            if (workflowLineId) patchLine(workflowLineId, { status: 'ok' });
          } else if (et === 'workflow.failed') {
            if (workflowLineId)
              patchLine(workflowLineId, {
                status: 'fail',
                detail: (evt.payload?.error as string) ?? undefined,
              });
          }
          break;
        }
        case 'tool_call_completed':
          if (toolLineId)
            patchLine(toolLineId, {
              status: evt.ok ? 'ok' : 'fail',
              detail: evt.result_summary,
            });
          toolLineId = null;
          workflowLineId = null;
          currentNodeLineId = null;
          break;
        case 'done':
          updateAssistant((m) => ({ ...m, status: 'complete' }));
          setExpanded((prev) => ({ ...prev, [assistantId]: false }));
          break;
        case 'error':
          pushLine({
            id: `er-${Date.now()}`,
            kind: 'error',
            parentId: null,
            label: evt.message,
            status: 'fail',
          });
          updateAssistant((m) => ({ ...m, status: 'failed', error: evt.message }));
          break;
      }
    };

    try {
      await sendChatMessage(agentId, sessionId, text, { onEvent });
      if (triggered) setTriggeredNotice(true);
    } catch (err) {
      pushLine({
        id: `er-${Date.now()}`,
        kind: 'error',
        parentId: null,
        label: err instanceof Error ? err.message : 'Something went wrong.',
        status: 'fail',
      });
      updateAssistant((m) => ({
        ...m,
        status: 'failed',
        error: err instanceof Error ? err.message : 'unknown error',
      }));
    } finally {
      setIsSending(false);
      queryClient.invalidateQueries({ queryKey: ['chat-sessions', agentId] });
      queryClient.invalidateQueries({ queryKey: ['chat-messages', sessionId] });
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const hasInFlight = messages.some((m) => m.status === 'streaming');
  const inputDisabled = isSending || hasInFlight;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-800 flex-shrink-0">
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center"
          style={{ backgroundColor: `${color}22`, border: `1px solid ${color}44` }}
        >
          <span className="text-xs font-bold" style={{ color }}>
            {agentName[0]}
          </span>
        </div>
        <div>
          <p className="text-sm font-medium text-slate-200">{agentName}</p>
          <p className="text-xs text-slate-500 truncate max-w-xs">{agentDescription}</p>
        </div>
      </div>

      <div className="mx-4 mt-3 flex items-center gap-2 bg-cyan-500/10 border border-cyan-500/20 rounded-lg px-3 py-2 flex-shrink-0">
        <Info className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" />
        <p className="text-xs text-cyan-300">
          Actions from this chat route to your Approval Inbox for review before execution.
        </p>
      </div>

      {triggeredNotice && (
        <div className="mx-4 mt-2 flex items-center gap-2 bg-violet-500/10 border border-violet-500/20 rounded-lg px-3 py-2 flex-shrink-0">
          <Info className="w-3.5 h-3.5 text-violet-400 flex-shrink-0" />
          <p className="text-xs text-violet-300">
            Revenue recognition triggered — check your <strong>Approval Inbox</strong> to review and approve.
          </p>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {messages.length === 0 && !messagesQuery.isLoading && (
          <p className="text-xs text-slate-600 text-center mt-8">
            Ask a question or give a command to get started.
          </p>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div className={`max-w-[75%] ${msg.role === 'user' ? 'order-2' : 'order-1'}`}>
              {msg.role === 'assistant' && (
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium" style={{ color }}>
                    {agentName}
                  </span>
                  <span className="text-xs text-slate-600">{fmt(msg.createdAt)}</span>
                  {msg.status === 'streaming' && !isSending && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-amber-400">
                      <Loader2 className="w-3 h-3 animate-spin" />
                      Still working…
                    </span>
                  )}
                  {msg.status === 'failed' && (
                    <span className="inline-flex items-center gap-1 text-[10px] text-rose-400">
                      <XCircle className="w-3 h-3" />
                      Failed
                    </span>
                  )}
                </div>
              )}

              {msg.role === 'assistant' && msg.activity && msg.activity.length > 0 && (
                <ActivityPanel
                  activity={msg.activity}
                  isOpen={expanded[msg.id] ?? false}
                  onToggle={() =>
                    setExpanded((prev) => ({ ...prev, [msg.id]: !(prev[msg.id] ?? false) }))
                  }
                />
              )}

              {msg.role === 'assistant' &&
                msg.status === 'streaming' &&
                !isSending &&
                !msg.content && (
                  <div className="rounded-xl px-4 py-3 text-sm leading-relaxed bg-slate-800 text-slate-400 border border-slate-700 italic">
                    The agent is still working on this turn. It will finish in the
                    background — refresh later to see the result.
                  </div>
                )}

              {(msg.content || msg.role === 'user') && (
                <div
                  className={`rounded-xl px-4 py-3 text-sm leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-cyan-500/20 text-cyan-100 border border-cyan-500/20'
                      : 'bg-slate-800 text-slate-200 border border-slate-700'
                  }`}
                >
                  <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
                </div>
              )}

              {msg.role === 'assistant' && msg.status === 'failed' && msg.error && (
                <p className="text-[10px] text-rose-400 mt-1">{msg.error}</p>
              )}

              {msg.role === 'user' && (
                <p className="text-xs text-slate-600 text-right mt-1">{fmt(msg.createdAt)}</p>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="px-4 pb-4 flex-shrink-0">
        <div className="flex items-end gap-2 bg-slate-900 border border-slate-700 rounded-xl p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              hasInFlight && !isSending
                ? 'Waiting for the current turn to finish…'
                : `Message ${agentName}…`
            }
            rows={2}
            disabled={inputDisabled}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-600 resize-none focus:outline-none disabled:opacity-50"
          />
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={handleSend}
            disabled={inputDisabled || !input.trim()}
          >
            <Send className="w-3.5 h-3.5" />
            {isSending ? 'Thinking…' : 'Send'}
          </button>
        </div>
        <p className="text-[10px] text-slate-700 mt-1 px-1">
          Enter to send · Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}

interface ActivityPanelProps {
  activity: ActivityLine[];
  isOpen: boolean;
  onToggle: () => void;
}

function ActivityPanel({ activity, isOpen, onToggle }: ActivityPanelProps) {
  const lineIndents = computeIndents(activity);

  return (
    <div className="mb-2 rounded-lg border border-slate-800 bg-slate-900/40">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-1.5 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
      >
        {isOpen ? (
          <ChevronDown className="w-3 h-3" />
        ) : (
          <ChevronRight className="w-3 h-3" />
        )}
        <span>{isOpen ? 'Hide steps' : `Show steps (${activity.length})`}</span>
      </button>
      {isOpen && (
        <div className="px-3 pb-2 space-y-0.5">
          {activity.map((line) => (
            <ActivityRow key={line.id} line={line} indent={lineIndents[line.id] ?? 0} />
          ))}
        </div>
      )}
    </div>
  );
}

function ActivityRow({ line, indent }: { line: ActivityLine; indent: number }) {
  const dimmer = line.kind === 'subagent';
  return (
    <div
      className="flex items-center gap-1.5 text-xs"
      style={{ paddingLeft: `${indent * 14}px` }}
    >
      <StatusIcon status={line.status} />
      <span className={dimmer ? 'text-slate-500' : 'text-slate-300'}>{line.label}</span>
      {line.detail && (
        <span className="text-slate-600 truncate max-w-xs">— {line.detail}</span>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ActivityLine['status'] }) {
  if (status === 'ok')
    return <CheckCircle2 className="w-3 h-3 text-emerald-500 flex-shrink-0" />;
  if (status === 'fail') return <XCircle className="w-3 h-3 text-rose-500 flex-shrink-0" />;
  return <Loader2 className="w-3 h-3 text-slate-500 animate-spin flex-shrink-0" />;
}

function computeIndents(activity: ActivityLine[]): Record<string, number> {
  const indents: Record<string, number> = {};
  for (const line of activity) {
    if (!line.parentId) {
      indents[line.id] = 0;
    } else {
      indents[line.id] = (indents[line.parentId] ?? 0) + 1;
    }
  }
  return indents;
}
