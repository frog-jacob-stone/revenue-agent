const STYLES: Record<string, string> = {
  idle: 'bg-slate-700 text-slate-300',
  running: 'bg-blue-500/20 text-blue-400 border border-blue-500/30',
  error: 'bg-red-500/20 text-red-400 border border-red-500/30',
  disabled: 'bg-slate-800 text-slate-500',
  success: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  failed: 'bg-red-500/20 text-red-400 border border-red-500/30',
  pending: 'bg-amber-500/20 text-amber-400 border border-amber-500/30',
  approved: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  rejected: 'bg-red-500/20 text-red-400 border border-red-500/30',
};

const LABELS: Record<string, string> = {
  idle: 'Idle',
  running: 'Running',
  error: 'Error',
  disabled: 'Disabled',
  success: 'Success',
  failed: 'Failed',
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
};

interface Props {
  status: string;
  size?: 'sm' | 'md';
}

export default function StatusChip({ status, size = 'sm' }: Props) {
  const base = size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-medium ${base} ${STYLES[status] ?? 'bg-slate-700 text-slate-300'}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {LABELS[status] ?? status}
    </span>
  );
}
