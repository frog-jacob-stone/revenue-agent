import { useEffect, useState } from 'react';
import { getChains, getChainDiagram, type ChainSummary } from '../../api';
import MermaidDiagram from '../../components/MermaidDiagram';

export default function ChainsList() {
  const [chains, setChains] = useState<ChainSummary[]>([]);
  const [diagrams, setDiagrams] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getChains()
      .then(async (rows) => {
        if (cancelled) return;
        setChains(rows);
        const sources = await Promise.all(
          rows.map((r) =>
            getChainDiagram(r.kind)
              .then((src) => [r.kind, src] as const)
              .catch(() => [r.kind, ''] as const),
          ),
        );
        if (!cancelled) {
          setDiagrams(Object.fromEntries(sources.filter(([, s]) => s)));
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <div className="p-6 text-sm text-red-400">Failed to load chains: {error}</div>;
  }

  return (
    <div className="p-6 space-y-5 overflow-y-auto h-full">
      <div>
        <h1 className="text-xl font-semibold text-slate-100 mb-1">Chains</h1>
        <p className="text-sm text-slate-400">
          Every registered orchestrator chain. Each diagram shows the static structure —
          steps, critique loops, branching gates, and approval points.
        </p>
      </div>

      {chains.length === 0 ? (
        <p className="text-sm text-slate-500">No chains registered.</p>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {chains.map((c) => (
            <div
              key={c.kind}
              className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden"
            >
              <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
                <p className="text-sm font-mono text-slate-200">{c.kind}</p>
                <span className="text-xs text-slate-500 font-mono">
                  {c.pattern} · {c.agent_slug} · {c.step_count} step
                  {c.step_count === 1 ? '' : 's'}
                </span>
              </div>
              <div className="bg-slate-950 p-3 overflow-x-auto">
                {diagrams[c.kind] ? (
                  <MermaidDiagram source={diagrams[c.kind]} />
                ) : (
                  <p className="text-xs text-slate-600">Loading diagram…</p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
