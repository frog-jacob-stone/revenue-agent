import type { ReactNode } from 'react';
import type {
  BillingType, BillingTiming, DrawState, RunItemStatus, RunStatus,
} from '../../../invoicing';
import { BILLING_TYPE_LABEL, DRAW_STATE_LABEL, RUN_STATUS_LABEL } from '../../../invoicing';

const TYPE_STYLES: Record<BillingType, string> = {
  time_and_materials: 'bg-indigo-500/15 text-indigo-700 border-indigo-500/40',
  fixed_fee_schedule: 'bg-violet-500/15 text-violet-700 border-violet-500/40',
  recurring_monthly: 'bg-teal-500/15 text-teal-700 border-teal-500/40',
  manual: 'bg-slate-100 text-slate-600 border-slate-300',
};

export function BillingTypeChip({ type }: { type: BillingType }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide ${TYPE_STYLES[type]}`}>
      {BILLING_TYPE_LABEL[type]}
    </span>
  );
}

export function TimingChip({ timing }: { timing: BillingTiming }) {
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-slate-300 bg-slate-100 text-[10px] font-medium uppercase tracking-wide text-slate-600">
      {timing}
    </span>
  );
}

const RUN_STATUS_STYLES: Record<RunStatus, string> = {
  planning: 'bg-slate-200 text-slate-700 border-slate-300',
  awaiting_approval: 'bg-amber-400/15 text-amber-600 border-amber-400/40',
  executing: 'bg-blue-500/15 text-blue-600 border-blue-500/40',
  completed: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/40',
  failed: 'bg-red-500/15 text-red-600 border-red-500/40',
  abandoned: 'bg-slate-100 text-slate-500 border-slate-300',
};

export function RunStatusChip({ status }: { status: RunStatus }) {
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium ${RUN_STATUS_STYLES[status]}`}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {RUN_STATUS_LABEL[status]}
    </span>
  );
}

const ITEM_STATUS_STYLES: Record<RunItemStatus, string> = {
  planned: 'bg-slate-200 text-slate-700 border-slate-300',
  approved: 'bg-cyan-500/15 text-cyan-600 border-cyan-500/40',
  skipped: 'bg-slate-100 text-slate-500 border-slate-300',
  in_flight: 'bg-red-500/15 text-red-600 border-red-500/40',
  created: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/40',
  failed: 'bg-red-500/15 text-red-600 border-red-500/40',
  abandoned: 'bg-slate-100 text-slate-500 border-slate-300',
};

export function ItemStatusChip({ status }: { status: RunItemStatus }) {
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide ${ITEM_STATUS_STYLES[status]}`}>
      {status.replace('_', ' ')}
    </span>
  );
}

const DRAW_STATE_STYLES: Record<DrawState, string> = {
  pending: 'bg-slate-100 text-slate-600 border-slate-300',
  ready: 'bg-emerald-500/15 text-emerald-700 border-emerald-500/40',
  in_flight: 'bg-cyan-500/15 text-cyan-700 border-cyan-500/40',
  invoiced: 'bg-slate-100 text-slate-500 border-slate-300',
};

export function DrawStateChip({
  state, overdue, due,
}: {
  state: DrawState;
  overdue?: boolean;
  due?: boolean;
}) {
  // Overdue and due-today are properties of a pending draw, not states of their
  // own — they say where the scheduled date sits relative to today, not that
  // anything changed about the draw. Overdue wins if both are somehow passed:
  // a slipped commitment is the louder fact.
  //
  // Due-today is green, not amber. Nothing is wrong — the date the contract
  // committed to has arrived, and that is the system working.
  const style = overdue
    ? 'bg-amber-400/15 text-amber-700 border-amber-400/50'
    : due
      ? 'bg-emerald-500/15 text-emerald-700 border-emerald-500/40'
      : DRAW_STATE_STYLES[state];
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] font-semibold uppercase tracking-wide ${style}`}>
      {overdue ? 'Overdue' : due ? 'Due' : DRAW_STATE_LABEL[state]}
    </span>
  );
}

export function StatTile({
  label,
  value,
  sub,
  tone = 'default',
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: 'default' | 'good' | 'warn' | 'bad';
}) {
  const valueTone = {
    default: 'text-slate-900',
    good: 'text-emerald-600',
    warn: 'text-amber-600',
    bad: 'text-red-600',
  }[tone];
  return (
    <div className="bg-white border border-slate-200 rounded-xl px-4 py-3">
      <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">{label}</p>
      <p className={`text-lg font-semibold mt-1 ${valueTone}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">{label}</p>
      <div className="text-sm text-slate-700 mt-0.5">{children}</div>
    </div>
  );
}

export function Delta({ current, prior }: { current: number; prior: number | null }) {
  if (prior === null || prior === 0) {
    return <span className="text-xs text-slate-400">no prior</span>;
  }
  const pct = ((current - prior) / prior) * 100;
  const tone = Math.abs(pct) < 0.05 ? 'text-slate-500' : pct > 0 ? 'text-emerald-600' : 'text-amber-600';
  const sign = pct > 0 ? '+' : '';
  return (
    <span className={`text-xs font-medium ${tone}`}>
      {sign}{pct.toFixed(1)}% vs prior
    </span>
  );
}
