import { useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { Plus, Trash2, Loader2 } from 'lucide-react';
import {
  createChatSession,
  deleteChatSession,
  listAgents,
  listChatSessions,
} from '../../api';
import type { AgentRecord, ChatSession } from '../../types';
import ChatWindow from './ChatWindow';

const AGENT_COLORS: Record<string, string> = {
  'sdr-researcher': '#6366f1',
  'outreach-agent': '#06b6d4',
  'content-writer': '#10b981',
  'content-orchestrator': '#10b981',
  'proposal-generator': '#f59e0b',
  'slide-deck-agent': '#ec4899',
  'revenue-recognition': '#8b5cf6',
};

function fmtSessionTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function ChatLayout() {
  const { agentId, sessionId } = useParams<{ agentId: string; sessionId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const agentsQuery = useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
  });
  const conversational: AgentRecord[] = (agentsQuery.data ?? []).filter(
    (a) => a.is_conversational,
  );

  // Auto-select first agent if none in URL
  useEffect(() => {
    if (!agentId && conversational.length > 0) {
      navigate(`/chat/${conversational[0].slug}`, { replace: true });
    }
  }, [agentId, conversational, navigate]);

  const sessionsQuery = useQuery({
    queryKey: ['chat-sessions', agentId],
    queryFn: () => listChatSessions(agentId as string),
    enabled: Boolean(agentId),
  });
  const sessions: ChatSession[] = sessionsQuery.data ?? [];

  // Auto-select most recent session when agent has sessions but URL has none
  useEffect(() => {
    if (agentId && !sessionId && sessions.length > 0) {
      navigate(`/chat/${agentId}/${sessions[0].id}`, { replace: true });
    }
  }, [agentId, sessionId, sessions, navigate]);

  const activeAgent = conversational.find((a) => a.slug === agentId) ?? null;

  const createMutation = useMutation({
    mutationFn: () => createChatSession(agentId as string),
    onSuccess: (newSession) => {
      queryClient.invalidateQueries({ queryKey: ['chat-sessions', agentId] });
      navigate(`/chat/${agentId}/${newSession.id}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteChatSession(id),
    onSuccess: (_data, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['chat-sessions', agentId] });
      if (deletedId === sessionId) {
        navigate(`/chat/${agentId}`, { replace: true });
      }
    },
  });

  if (agentsQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-slate-500 text-sm">
        Loading agents…
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Agent selector sidebar */}
      <aside className="w-52 flex-shrink-0 border-r border-slate-800 py-3 px-2 space-y-0.5 overflow-y-auto">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold px-3 mb-2">
          Agents
        </p>
        {conversational.map((agent) => {
          const color = AGENT_COLORS[agent.slug] ?? '#64748b';
          return (
            <button
              key={agent.slug}
              className={`w-full flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-left transition-colors ${
                agentId === agent.slug
                  ? 'bg-slate-800 text-slate-100'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
              }`}
              onClick={() => navigate(`/chat/${agent.slug}`)}
            >
              <div
                className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 text-xs font-bold"
                style={{ backgroundColor: `${color}22`, color, border: `1px solid ${color}44` }}
              >
                {agent.name[0]}
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium truncate">{agent.name}</p>
                <p className="text-[10px] text-slate-600 truncate">
                  {agent.is_active ? 'active' : 'disabled'}
                </p>
              </div>
            </button>
          );
        })}
      </aside>

      {/* Session list rail */}
      {agentId && (
        <aside className="w-64 flex-shrink-0 border-r border-slate-800 flex flex-col">
          <div className="flex items-center justify-between px-3 py-3 border-b border-slate-800">
            <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold">
              Chats
            </p>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="flex items-center gap-1 px-2 py-1 rounded text-xs text-cyan-400 hover:text-cyan-300 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
              title="New chat"
            >
              <Plus className="w-3.5 h-3.5" />
              New
            </button>
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            {sessionsQuery.isLoading && (
              <div className="flex items-center justify-center py-6 text-slate-500 text-xs">
                <Loader2 className="w-3.5 h-3.5 animate-spin mr-1.5" />
                Loading…
              </div>
            )}
            {!sessionsQuery.isLoading && sessions.length === 0 && (
              <p className="text-xs text-slate-600 text-center px-4 py-6">
                No chats yet. Start a new one.
              </p>
            )}
            {sessions.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                isActive={s.id === sessionId}
                onSelect={() => navigate(`/chat/${agentId}/${s.id}`)}
                onDelete={() => deleteMutation.mutate(s.id)}
              />
            ))}
          </div>
        </aside>
      )}

      {/* Chat window */}
      <div className="flex-1 min-w-0">
        {agentId && sessionId ? (
          <ChatWindow agentId={agentId} sessionId={sessionId} agent={activeAgent} />
        ) : agentId ? (
          <div className="flex flex-col h-full items-center justify-center text-slate-500 text-sm gap-3">
            <p>No chat selected.</p>
            <button
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-cyan-400 bg-cyan-500/10 border border-cyan-500/20 hover:bg-cyan-500/20 disabled:opacity-50"
            >
              <Plus className="w-3.5 h-3.5" />
              Start a new chat
            </button>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-slate-500 text-sm">
            Select an agent to start chatting.
          </div>
        )}
      </div>
    </div>
  );
}

interface SessionRowProps {
  session: ChatSession;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
}

function SessionRow({ session, isActive, onSelect, onDelete }: SessionRowProps) {
  return (
    <div
      className={`group relative flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${
        isActive ? 'bg-slate-800' : 'hover:bg-slate-800/50'
      }`}
      onClick={onSelect}
    >
      <div className="min-w-0 flex-1">
        <p
          className={`text-xs truncate ${
            isActive ? 'text-slate-100 font-medium' : 'text-slate-300'
          }`}
        >
          {session.title}
        </p>
        <p className="text-[10px] text-slate-600 mt-0.5">
          {fmtSessionTime(session.last_message_at ?? session.created_at)}
        </p>
      </div>
      <button
        onClick={(e) => {
          e.stopPropagation();
          if (confirm(`Delete "${session.title}"?`)) onDelete();
        }}
        className="opacity-0 group-hover:opacity-100 text-slate-500 hover:text-rose-400 transition-opacity p-1"
        title="Delete chat"
      >
        <Trash2 className="w-3 h-3" />
      </button>
    </div>
  );
}
