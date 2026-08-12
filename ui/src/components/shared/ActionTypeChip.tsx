const STYLES: Record<string, string> = {
  create: 'bg-emerald-500/20 text-emerald-600 border border-emerald-500/40',
  update: 'bg-blue-500/20 text-blue-600 border border-blue-500/40',
  delete: 'bg-red-500/20 text-red-600 border border-red-500/40',
};

const FALLBACK = 'bg-slate-100 text-slate-600 border border-slate-300';

export default function ActionTypeChip({ type }: { type: string }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide ${STYLES[type] ?? FALLBACK}`}>
      {type}
    </span>
  );
}
