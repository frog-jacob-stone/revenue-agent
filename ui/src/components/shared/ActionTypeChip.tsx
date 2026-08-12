const STYLES: Record<string, string> = {
  create: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  update: 'bg-blue-500/20 text-blue-400 border border-blue-500/30',
  delete: 'bg-red-500/20 text-red-400 border border-red-500/30',
};

const FALLBACK = 'bg-slate-700/50 text-slate-400 border border-slate-600/30';

export default function ActionTypeChip({ type }: { type: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide ${STYLES[type] ?? FALLBACK}`}>
      {type}
    </span>
  );
}
