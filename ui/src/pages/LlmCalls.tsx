import { useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertCircle, ChevronDown, ChevronRight, Filter, Zap,
} from 'lucide-react';
import {
  getLlmCall, getLlmCallsSummary, listAgents, listLlmCalls,
} from '../api';
import type {
  LlmCallDetail,
  LlmCallSummary,
  LlmCallsSummary,
} from '../api';
import type { AgentRecord } from '../types';

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function fmtNumber(n: number | null | undefined) {
  if (n == null) return '—';
  return n.toLocaleString('en-US');
}

function fmtLatency(ms: number) {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtPercent(rate: number) {
  return `${(rate * 100).toFixed(1)}%`;
}

const STATUS_OPTS: Array<'' | 'ok' | 'error'> = ['', 'ok', 'error'];

export default function LlmCalls() {
  const [summary, setSummary] = useState<LlmCallsSummary | null>(null);
  const [calls, setCalls] = useState<LlmCallSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [agents, setAgents] = useState<AgentRecord[]>([]);
  const [details, setDetails] = useState<Record<number, LlmCallDetail | 'loading' | 'error'>>({});
  const [expanded, setExpanded] = useState<number | null>(null);

  const [agentSlug, setAgentSlug] = useState('');
  const [model, setModel] = useState('');
  const [status, setStatus] = useState<'' | 'ok' | 'error'>('');

  useEffect(() => {
    listAgents().then(setAgents).catch(console.error);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      getLlmCallsSummary(),
      listLlmCalls({
        agent_slug: agentSlug || undefined,
        model: model || undefined,
        status: status || undefined,
        limit: 200,
      }),
    ])
      .then(([s, c]) => {
        if (cancelled) return;
        setSummary(s);
        setCalls(c);
      })
      .catch((err) => {
        if (!cancelled) console.error(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentSlug, model, status]);

  const modelOptions = useMemo(() => {
    const fromSummary = summary?.by_model.map((m) => m.model) ?? [];
    const fromCalls = calls.map((c) => c.model);
    return Array.from(new Set([...fromSummary, ...fromCalls])).sort();
  }, [summary, calls]);

  function toggleRow(id: number) {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    if (details[id] && details[id] !== 'error') return;
    setDetails((d) => ({ ...d, [id]: 'loading' }));
    getLlmCall(id)
      .then((detail) => setDetails((d) => ({ ...d, [id]: detail })))
      .catch((err) => {
        console.error(err);
        setDetails((d) => ({ ...d, [id]: 'error' }));
      });
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">LLM Calls</h1>
        <p className="text-sm text-slate-400 mt-0.5">
          {loading && !summary ? '…' : `${summary?.total_calls ?? 0} total calls`}
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard
          label="Total calls"
          value={fmtNumber(summary?.total_calls ?? 0)}
          icon={<Activity className="w-4 h-4 text-cyan-400" />}
        />
        <StatCard
          label="Total tokens"
          value={fmtNumber(summary?.total_tokens ?? 0)}
          sub={
            summary
              ? `${fmtNumber(summary.total_prompt_tokens)} in · ${fmtNumber(summary.total_completion_tokens)} out`
              : undefined
          }
          icon={<Zap className="w-4 h-4 text-amber-400" />}
        />
        <StatCard
          label="Avg latency"
          value={summary ? fmtLatency(Math.round(summary.avg_latency_ms)) : '—'}
        />
        <StatCard
          label="Error rate"
          value={summary ? fmtPercent(summary.error_rate) : '—'}
          icon={<AlertCircle className={`w-4 h-4 ${summary && summary.error_rate > 0 ? 'text-rose-400' : 'text-slate-500'}`} />}
          accent={summary && summary.error_rate > 0 ? 'text-rose-400' : undefined}
        />
      </div>

      {/* By-model + By-agent breakdowns */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BreakdownTable
          title="By model"
          rows={
            summary?.by_model.map((r) => ({
              label: r.model,
              labelClass: 'font-mono text-cyan-400',
              calls: r.calls,
              tokens: r.tokens,
            })) ?? []
          }
        />
        <BreakdownTable
          title="By agent"
          rows={
            summary?.by_agent.map((r) => ({
              label: r.agent_slug ?? '— (no agent)',
              labelClass: r.agent_slug ? 'text-slate-200' : 'text-slate-500 italic',
              calls: r.calls,
              tokens: r.tokens,
            })) ?? []
          }
        />
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-3 bg-slate-900 border border-slate-800 rounded-lg px-4 py-3">
        <Filter className="w-4 h-4 text-slate-500" />
        <span className="text-xs text-slate-500 font-medium uppercase tracking-wide">Filters</span>
        <select
          className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1 ml-2"
          value={agentSlug}
          onChange={(e) => setAgentSlug(e.target.value)}
        >
          <option value="">All agents</option>
          {agents.map((a) => (
            <option key={a.slug} value={a.slug}>{a.name}</option>
          ))}
        </select>
        <select
          className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        >
          <option value="">All models</option>
          {modelOptions.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <select
          className="bg-slate-800 border border-slate-700 text-slate-300 text-xs rounded px-2 py-1"
          value={status}
          onChange={(e) => setStatus(e.target.value as '' | 'ok' | 'error')}
        >
          {STATUS_OPTS.map((s) => (
            <option key={s} value={s}>{s === '' ? 'All statuses' : s}</option>
          ))}
        </select>
      </div>

      {/* Calls table */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
              <th className="w-6 px-3 py-3" />
              <th className="text-left px-4 py-3 font-medium">Timestamp</th>
              <th className="text-left px-4 py-3 font-medium">Model</th>
              <th className="text-left px-4 py-3 font-medium">Agent</th>
              <th className="text-left px-4 py-3 font-medium">Purpose</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-right px-4 py-3 font-medium">Latency</th>
              <th className="text-right px-4 py-3 font-medium">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {loading && calls.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-xs text-slate-500 animate-pulse">Loading…</td>
              </tr>
            ) : calls.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-xs text-slate-500">No LLM calls match the current filters.</td>
              </tr>
            ) : (
              calls.map((call, i) => {
                const isOpen = expanded === call.id;
                const rowBorder = i < calls.length - 1 || isOpen ? 'border-b' : '';
                return (
                  <CallRow
                    key={call.id}
                    call={call}
                    open={isOpen}
                    rowBorderClass={rowBorder}
                    detail={details[call.id]}
                    onToggle={() => toggleRow(call.id)}
                  />
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StatCard({
  label, value, sub, icon, accent,
}: { label: string; value: string; sub?: string; icon?: React.ReactNode; accent?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs text-slate-500">{label}</p>
        {icon}
      </div>
      <p className={`text-3xl font-bold ${accent ?? 'text-slate-100'}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}

interface BreakdownRow {
  label: string;
  labelClass: string;
  calls: number;
  tokens: number;
}

function BreakdownTable({ title, rows }: { title: string; rows: BreakdownRow[] }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-slate-800">
        <h2 className="text-sm font-semibold text-slate-300">{title}</h2>
      </div>
      {rows.length === 0 ? (
        <div className="px-5 py-6 text-center text-xs text-slate-500">No data</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-5 py-2 font-medium">Name</th>
              <th className="text-right px-5 py-2 font-medium">Calls</th>
              <th className="text-right px-5 py-2 font-medium">Tokens</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.label}-${i}`} className={i < rows.length - 1 ? 'border-b border-slate-800/60' : ''}>
                <td className={`px-5 py-2 text-xs ${r.labelClass}`}>{r.label}</td>
                <td className="px-5 py-2 text-xs text-slate-300 text-right tabular-nums">{fmtNumber(r.calls)}</td>
                <td className="px-5 py-2 text-xs text-slate-300 text-right tabular-nums">{fmtNumber(r.tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function CallRow({
  call, open, rowBorderClass, detail, onToggle,
}: {
  call: LlmCallSummary;
  open: boolean;
  rowBorderClass: string;
  detail: LlmCallDetail | 'loading' | 'error' | undefined;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className={`${rowBorderClass} border-slate-800 hover:bg-slate-800/40 cursor-pointer transition-colors`}
        onClick={onToggle}
      >
        <td className="px-3 py-2.5 text-slate-600">
          {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </td>
        <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">{fmtTime(call.started_at)}</td>
        <td className="px-4 py-2.5 font-mono text-xs text-cyan-400 whitespace-nowrap">
          {call.model}
          {call.streamed && (
            <span className="ml-1.5 text-[10px] text-slate-500 uppercase tracking-wide">stream</span>
          )}
        </td>
        <td className="px-4 py-2.5 text-xs text-slate-300 whitespace-nowrap">{call.agent_slug ?? '—'}</td>
        <td className="px-4 py-2.5 text-xs text-slate-400 max-w-[260px] truncate">{call.purpose ?? '—'}</td>
        <td className="px-4 py-2.5">
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${
              call.status === 'ok'
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
            }`}
          >
            {call.status}
          </span>
        </td>
        <td className="px-4 py-2.5 text-xs text-slate-400 text-right tabular-nums whitespace-nowrap">
          {fmtLatency(call.latency_ms)}
        </td>
        <td className="px-4 py-2.5 text-xs text-slate-300 text-right tabular-nums whitespace-nowrap">
          {call.total_tokens != null ? (
            <>
              {fmtNumber(call.total_tokens)}
              <span className="text-slate-600 ml-1">
                ({fmtNumber(call.prompt_tokens)}/{fmtNumber(call.completion_tokens)})
              </span>
            </>
          ) : '—'}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-slate-800 bg-slate-950/50">
          <td colSpan={8} className="px-8 py-4">
            <CallDetailView call={call} detail={detail} />
          </td>
        </tr>
      )}
    </>
  );
}

function CallDetailView({
  call, detail,
}: {
  call: LlmCallSummary;
  detail: LlmCallDetail | 'loading' | 'error' | undefined;
}) {
  if (detail === 'loading' || detail == null) {
    return <p className="text-xs text-slate-500 animate-pulse">Loading details…</p>;
  }
  if (detail === 'error') {
    return <p className="text-xs text-rose-400">Failed to load call detail.</p>;
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
        <span><span className="text-slate-500">id</span> {detail.id}</span>
        <span><span className="text-slate-500">provider</span> {detail.provider}</span>
        {detail.workflow_id && <span><span className="text-slate-500">workflow</span> <span className="font-mono">{detail.workflow_id}</span></span>}
        {detail.thread_id && <span><span className="text-slate-500">thread</span> <span className="font-mono">{detail.thread_id}</span></span>}
        <span><span className="text-slate-500">ended</span> {fmtTime(detail.ended_at)}</span>
      </div>
      {call.status === 'error' && detail.error && (
        <div>
          <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold mb-2">Error</p>
          <pre className="text-xs text-rose-400 bg-slate-950 rounded-lg p-3 overflow-x-auto font-mono leading-relaxed whitespace-pre-wrap">
            {detail.error}
          </pre>
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <JsonBlock title="Request" value={detail.request} />
        <JsonBlock title="Response" value={detail.response} />
      </div>
    </div>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold mb-2">{title}</p>
      <pre className="text-xs text-emerald-400 bg-slate-950 rounded-lg p-3 overflow-auto font-mono leading-relaxed max-h-96">
        {value == null ? '—' : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
