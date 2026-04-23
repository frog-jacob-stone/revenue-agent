import { useState } from 'react';
import { Send, Info } from 'lucide-react';
import { CHAT_HISTORIES, AGENT_MAP } from '../../mocks';
import type { AgentId } from '../../mocks';
import StubBadge from '../../components/shared/StubBadge';

interface Props {
  agentId: AgentId;
}

function fmt(iso: string) {
  return new Date(iso).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

export default function ChatWindow({ agentId }: Props) {
  const [input, setInput] = useState('');
  const agent = AGENT_MAP[agentId];
  const messages = CHAT_HISTORIES[agentId] ?? [];

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

      {/* Notice */}
      <div className="mx-4 mt-3 flex items-center gap-2 bg-cyan-500/10 border border-cyan-500/20 rounded-lg px-3 py-2 flex-shrink-0">
        <Info className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0" />
        <p className="text-xs text-cyan-300">Actions from this chat route to your Approval Inbox for review before execution.</p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[75%] ${msg.role === 'user' ? 'order-2' : 'order-1'}`}>
              {msg.role === 'agent' && (
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-xs font-medium" style={{ color: agent.color }}>{agent.name}</span>
                  <span className="text-xs text-slate-600">{fmt(msg.timestamp)}</span>
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
                <p className="text-xs text-slate-600 text-right mt-1">{fmt(msg.timestamp)}</p>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="px-4 pb-4 flex-shrink-0">
        <div className="flex items-end gap-2 bg-slate-900 border border-slate-700 rounded-xl p-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={`Message ${agent.name}…`}
            rows={2}
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-600 resize-none focus:outline-none"
          />
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/30 transition-colors flex-shrink-0"
            onClick={() => console.log('send message', input)}
          >
            <Send className="w-3.5 h-3.5" />
            Send
            <StubBadge />
          </button>
        </div>
      </div>
    </div>
  );
}
