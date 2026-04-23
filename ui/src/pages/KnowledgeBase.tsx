import { useState } from 'react';
import { Search, Trash2, Plus, X } from 'lucide-react';
import { AGENTS, MEMORY_ENTRIES } from '../mocks';
import type { AgentId } from '../mocks';
import StubBadge from '../components/shared/StubBadge';

export default function KnowledgeBase() {
  const [activeAgent, setActiveAgent] = useState<AgentId>(AGENTS[0].id);
  const [showAddModal, setShowAddModal] = useState(false);

  const entries = MEMORY_ENTRIES.filter((e) => e.agentId === activeAgent);
  const agent = AGENTS.find((a) => a.id === activeAgent)!;

  return (
    <div className="p-6 space-y-4 max-w-5xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Knowledge Base</h1>
          <p className="text-sm text-slate-400 mt-0.5">Per-agent memory entries</p>
        </div>
        <button
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/25 transition-colors"
          onClick={() => setShowAddModal(true)}
        >
          <Plus className="w-3.5 h-3.5" />
          Add Memory
          <StubBadge />
        </button>
      </div>

      {/* Agent tabs */}
      <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-xl p-1">
        {AGENTS.map((a) => (
          <button
            key={a.id}
            className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-colors ${
              activeAgent === a.id
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-500 hover:text-slate-300'
            }`}
            onClick={() => setActiveAgent(a.id)}
          >
            {a.name.split(' ')[0]}
          </button>
        ))}
      </div>

      {/* Search */}
      <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 rounded-lg px-3 py-2">
        <Search className="w-4 h-4 text-slate-500" />
        <input
          type="text"
          placeholder="Search memory…"
          className="flex-1 bg-transparent text-sm text-slate-300 placeholder-slate-600 focus:outline-none"
        />
        <StubBadge />
      </div>

      {/* Entries */}
      <div className="space-y-3">
        {entries.length === 0 && (
          <div className="text-center py-12 text-slate-500 text-sm">No memory entries for {agent.name}.</div>
        )}
        {entries.map((entry) => (
          <div key={entry.id} className="bg-slate-900 border border-slate-800 rounded-xl p-4 hover:border-slate-700 transition-colors">
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-200 leading-relaxed">{entry.content}</p>
                <div className="flex items-center gap-3 mt-2.5 flex-wrap">
                  <span className="text-xs text-slate-500">Source: <span className="text-slate-400">{entry.source}</span></span>
                  <span className="text-xs text-slate-600">·</span>
                  <span className="text-xs text-slate-500">{entry.date}</span>
                  <div className="flex gap-1.5 flex-wrap">
                    {entry.tags.map((tag) => (
                      <span key={tag} className="text-[10px] bg-slate-800 text-slate-400 border border-slate-700 rounded px-1.5 py-0.5 font-mono">
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
              <button
                className="text-slate-600 hover:text-red-400 transition-colors flex items-center gap-1"
                onClick={() => console.log('delete memory', entry.id)}
              >
                <Trash2 className="w-3.5 h-3.5" />
                <StubBadge />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Add Memory Modal */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl p-6 w-full max-w-md space-y-4 shadow-2xl">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-slate-100">Add Memory Entry</h2>
              <button className="text-slate-500 hover:text-slate-300" onClick={() => setShowAddModal(false)}>
                <X className="w-4 h-4" />
              </button>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Agent</label>
              <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
                {AGENTS.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Content</label>
              <textarea rows={3} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none resize-none" placeholder="What should the agent remember?" />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1.5">Tags (comma-separated)</label>
              <input type="text" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" placeholder="e.g. icp, outreach-rules" />
            </div>
            <div className="flex gap-2 pt-1">
              <button
                className="flex-1 py-2 rounded-lg text-sm font-medium bg-cyan-500/15 text-cyan-400 border border-cyan-500/20 hover:bg-cyan-500/25 transition-colors"
                onClick={() => console.log('save memory')}
              >
                Save Memory
                <StubBadge />
              </button>
              <button className="px-4 py-2 rounded-lg text-sm text-slate-400 hover:text-slate-200 transition-colors" onClick={() => setShowAddModal(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
