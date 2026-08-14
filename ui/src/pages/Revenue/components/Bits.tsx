// Revenue-specific badges. Deliberately not shared with the Invoices ones in
// `pages/Invoices/components/Bits.tsx`: the words overlap ("billing type",
// "run status") but the vocabularies do not. Invoicing's billing types come
// from this system's own config; rev rec's come from Airtable, and its runs
// have three states rather than six.

import type { BillingType, RevRecRunStatus } from '../mockData';

const TYPE_STYLES: Record<BillingType, string> = {
  'Fixed Fee': 'bg-violet-500/15 text-violet-700 border-violet-500/40',
  'T&M': 'bg-indigo-500/15 text-indigo-700 border-indigo-500/40',
  MSF: 'bg-teal-500/15 text-teal-700 border-teal-500/40',
  Hosting: 'bg-sky-500/15 text-sky-700 border-sky-500/40',
  Retainer: 'bg-cyan-500/15 text-cyan-700 border-cyan-500/40',
};

export function BillingTypeChip({ type }: { type: BillingType }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide whitespace-nowrap ${TYPE_STYLES[type]}`}>
      {type}
    </span>
  );
}

const RUN_STATUS_STYLES: Record<RevRecRunStatus, string> = {
  completed: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/40',
  awaiting_approval: 'bg-amber-400/15 text-amber-600 border-amber-400/40',
  blocked: 'bg-red-500/15 text-red-600 border-red-500/40',
};

const RUN_STATUS_LABEL: Record<RevRecRunStatus, string> = {
  completed: 'Completed',
  awaiting_approval: 'Awaiting approval',
  blocked: 'Blocked',
};

export function RunStatusChip({ status }: { status: RevRecRunStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium whitespace-nowrap ${RUN_STATUS_STYLES[status]}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {RUN_STATUS_LABEL[status]}
    </span>
  );
}

/** Percent complete only means something for Fixed Fee — everything else has no
 *  fixed denominator to be a percentage of. */
export function PercentComplete({ value }: { value: number | null }) {
  if (value === null) return <span className="text-slate-300">—</span>;
  return (
    <span className="inline-flex items-center gap-2 justify-end">
      <span className="w-14 h-1.5 rounded-full bg-slate-200 overflow-hidden">
        <span
          className="block h-full bg-cyan-500"
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </span>
      <span className="tabular-nums text-slate-700 w-9 text-right">
        {Math.round(value * 100)}%
      </span>
    </span>
  );
}
