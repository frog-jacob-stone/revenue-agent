// Revenue-specific badges. Deliberately not shared with the Invoices ones in
// `pages/Invoices/components/Bits.tsx`: the words overlap ("type", "run
// status") but the vocabularies do not. Invoicing's `billing_type` is how a
// client is *invoiced*; `revenue_type` here is how a project *recognizes*, and
// a project can be invoiced on a draw schedule while recognizing
// percent-complete. Keeping the two chip sets apart keeps that distinction
// visible.

import type { RevenueType, RevenueRunStatus } from '../../../api';

const TYPE_STYLES: Record<RevenueType, string> = {
  fixed_fee: 'bg-violet-500/15 text-violet-700 border-violet-500/40',
  time_and_materials: 'bg-indigo-500/15 text-indigo-700 border-indigo-500/40',
  msf: 'bg-teal-500/15 text-teal-700 border-teal-500/40',
  hosting: 'bg-sky-500/15 text-sky-700 border-sky-500/40',
  retainer: 'bg-cyan-500/15 text-cyan-700 border-cyan-500/40',
};

const TYPE_LABEL: Record<RevenueType, string> = {
  fixed_fee: 'Fixed Fee',
  time_and_materials: 'T&M',
  msf: 'MSF',
  hosting: 'Hosting',
  retainer: 'Retainer',
};

export function RevenueTypeChip({ type }: { type: RevenueType }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide whitespace-nowrap ${TYPE_STYLES[type]}`}>
      {TYPE_LABEL[type]}
    </span>
  );
}

// Three states, and only one of them is the ledger. A draft is a proposal
// nobody has decided yet; an abandoned run is a discarded draft kept rather
// than deleted. Neither counts toward any reported figure.
const RUN_STATUS_STYLES: Record<RevenueRunStatus, string> = {
  recognized: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/40',
  draft: 'bg-amber-400/15 text-amber-600 border-amber-400/40',
  abandoned: 'bg-slate-400/15 text-slate-500 border-slate-400/40',
};

const RUN_STATUS_LABEL: Record<RevenueRunStatus, string> = {
  recognized: 'Recognized',
  draft: 'Draft',
  abandoned: 'Abandoned',
};

export function RunStatusChip({ status }: { status: RevenueRunStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium whitespace-nowrap ${RUN_STATUS_STYLES[status]}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {RUN_STATUS_LABEL[status]}
    </span>
  );
}

const hrs = (n: number) =>
  n.toLocaleString('en-US', { maximumFractionDigits: 2 });

/** Percent complete only means something for fixed fee — everything else has no
 *  fixed denominator to be a percentage of.
 *
 *  Pass `loggedToDate` and `scheduled` to show the fraction the percentage came
 *  from. Worth the second line: the Hours column is the *month's* hours, while
 *  this is a fraction of the project's whole effort, so the two numbers on a row
 *  cannot be reconciled with each other — 25 hours beside 100% complete reads as
 *  a bug until the 1,816 hours behind it are visible. Shown rather than hidden
 *  behind a hover, because the point of the run detail is checking figures and a
 *  figure you have to go looking for does not get checked.
 */
export function PercentComplete({
  value,
  loggedToDate = null,
  scheduled = null,
}: {
  value: number | null;
  /** Hours from inception through this period. */
  loggedToDate?: number | null;
  /** Still booked in Forecast — the rest of the denominator. */
  scheduled?: number | null;
}) {
  if (value === null) return <span className="text-slate-300">—</span>;
  const remaining = scheduled ?? 0;
  const projected = loggedToDate === null ? null : loggedToDate + remaining;
  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <span className="inline-flex items-center gap-2">
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
      {projected !== null && loggedToDate !== null && (
        <span
          className="text-[11px] text-slate-400 tabular-nums"
          title={
            `${hrs(loggedToDate)} hours logged since the project started` +
            (remaining
              ? `, ${hrs(remaining)} still scheduled in Forecast`
              : ', none still scheduled in Forecast') +
            ` — ${hrs(projected)} projected in total. Percent complete is the ` +
            'first over the last, and the Hours column is this month alone.'
          }
        >
          {hrs(loggedToDate)} of {hrs(projected)} hrs
        </span>
      )}
    </span>
  );
}
