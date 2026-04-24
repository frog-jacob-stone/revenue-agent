import { useEffect, useRef, useState } from 'react';
import { Send, Info } from 'lucide-react';
import { AGENT_MAP } from '../../mocks';
import type { AgentId } from '../../mocks';
import { agentChat } from '../../api';
import type { ChatMessage } from '../../api';

interface Props {
  agentId: AgentId;
}

interface DisplayMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

const MAX_HISTORY = 20;

function fmt(ts: number) {
  return new Date(ts).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

export default function ChatWindow({ agentId }: Props) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [triggeredNotice, setTriggeredNotice] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const agent = AGENT_MAP[agentId];

  // Reset on agent switch
  useEffect(() => {
    setMessages([]);
    setInput('');
    setTriggeredNotice(false);
  }, [agentId]);

  // Scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  async function handleSend() {
    const text = input.trim();
    if (!text || isLoading) return;

    const userMsg: DisplayMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text,
    };

    const next = [...messages, userMsg].slice(-MAX_HISTORY);
    setMessages(next);
    setInput('');
    setIsLoading(true);
    setTriggeredNotice(false);

    const apiMessages: ChatMessage[] = next.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    try {
      const res = await agentChat(agentId, apiMessages);
      const assistantMsg: DisplayMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: res.answer,
      };
      setMessages((prev) => [...prev, assistantMsg].slice(-MAX_HISTORY));
      if (res.tool_used === 'trigger_revenue_recognition') {
        setTriggeredNotice(true);
      }
    } catch (err) {
      const errorMsg: DisplayMessage = {
        id: `e-${Date.now()}`,
        role: 'assistant',
        content: err instanceof Error ? `Error: ${err.message}` : 'Something went wrong. Please try again.',
      };
      setMessages((prev) => [...prev, errorMsg].slice(-MAX_HISTORY));
    } finally {
      setIsLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-800 flex-shrink-0">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ backgroundColor: `${agent.color}22`, border: `1px solid ${agent.color}44` }}>
          <span className="text-xs font-bold" style={{ color: agent.color }}>{agent.name[0]}</span>
        </div>
        <div>
          <p className="text-sm font-medium text-slate-200">{agent.name}</p>
          <p className="text-xs text-slate-500 truncate max-w-xs">{agent.description}</p>
        </div>
      </div>

      {/* Approval inbox notice */}
      <div className="mx-4 mt-3 flex items-center gap-2 bg-cyan-500/10 border border-cyan-500/20 rounded-lg px-3 py-2 flex-shrink-0">
        <Info className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" />
        <p className="text-xs text-cyan-300">Actions from this chat route to your Approval Inbox for review before execution.</p>
      </div>

      {/* Revenue recognition triggered notice */}
      {triggeredNotice && (
        <div className="mx-4 mt-2 flex items-center gap-2 bg-violet-500/10 border border-violet-500/20 rounded-lg px-3 py-2 flex-shrink-0">
          <Info className="w-3.5 h-3.5 text-violet-400 flex-shrink-0" />
          <p className="text-xs text-violet-300">Revenue recognition triggered — check your <strong>Approval Inbox</strong> to review and approve.</p>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {messages.length === 0 && !isLoading && (
          <p className="text-xs text-slate-600 text-center mt-8">
            Ask a question or give a command to get started.
          </p>
        )}
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[75%] ${msg.role === 'user' ? 'order-2' : 'order-1'}`}>
              {msg.role === 'assistant' && (
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium" style={{ color: agent.color }}>{agent.name}</span>
                  <span className="text-xs text-slate-600">{fmt(parseInt(msg.id.split('-')[1]))}</span>
                </div>
              )}
              <div
                className={`rounded-xl px-4 py-3 text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-cyan-500/20 text-cyan-100 border border-cyan-500/20'
                    : 'bg-slate-800 text-slate-200 border border-slate-700'
                }`}
              >
                <pre className="whitespace-pre-wrap font-sans">{msg.content}</pre>
              </div>
              {msg.role === 'user' && (
                <p className="text-xs text-slate-600 text-right mt-1">{fmt(parseInt(msg.id.split('-')[1]))}</p>
              )}
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-slate-800 border border-slate-700 rounded-xl px-4 py-3">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 bg-slate-500 rounded-full animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 bg-slate-500 rounded-full animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 bg-slate-500 rounded-full animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-4 pb-4 flex-shrink-0">
        <div className="flex items-end gap-2 bg-slate-900 border border-slate-700 rounded-xl p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Message ${agent.name}…`}
            rows={2}
            disabled={isLoading}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-600 resize-none focus:outline-none disabled:opacity-50"
          />
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30 transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
          >
            <Send className="w-3.5 h-3.5" />
            {isLoading ? 'Thinking…' : 'Send'}
          </button>
        </div>
        <p className="text-[10px] text-slate-700 mt-1 px-1">Enter to send · Shift+Enter for new line</p>
      </div>
    </div>
  );
}
