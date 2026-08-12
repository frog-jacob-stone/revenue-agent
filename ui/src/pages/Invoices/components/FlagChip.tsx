import { AlertOctagon, AlertTriangle, Info } from 'lucide-react';
import type { Flag, FlagSeverity } from '../../../invoicing';

const STYLES: Record<FlagSeverity, string> = {
  error: 'bg-red-500/15 text-red-600 border-red-500/40',
  warning: 'bg-amber-400/15 text-amber-600 border-amber-400/40',
  info: 'bg-sky-500/15 text-sky-600 border-sky-500/40',
};

const ICONS: Record<FlagSeverity, typeof Info> = {
  error: AlertOctagon,
  warning: AlertTriangle,
  info: Info,
};

export function FlagChip({ flag }: { flag: Flag }) {
  const Icon = ICONS[flag.severity];
  return (
    <span
      title={flag.message}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide ${STYLES[flag.severity]}`}
    >
      <Icon className="w-3 h-3" />
      {flag.code}
    </span>
  );
}

export function FlagRow({ flag }: { flag: Flag }) {
  const Icon = ICONS[flag.severity];
  return (
    <div className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 ${STYLES[flag.severity]}`}>
      <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-[11px] font-semibold uppercase tracking-wide">{flag.code}</p>
        <p className="text-xs text-slate-700 mt-0.5 leading-relaxed">{flag.message}</p>
      </div>
    </div>
  );
}

export function SeverityCount({ severity, count }: { severity: FlagSeverity; count: number }) {
  const Icon = ICONS[severity];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${count === 0 ? 'text-slate-400' : STYLES[severity].split(' ')[1]}`}>
      <Icon className="w-3.5 h-3.5" />
      {count} {severity}{count === 1 ? '' : 's'}
    </span>
  );
}
