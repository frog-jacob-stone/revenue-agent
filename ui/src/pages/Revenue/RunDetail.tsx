import { Fragment, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Archive, ArrowLeft, Check, Pencil, Trash2, TriangleAlert } from 'lucide-react';
import { StatTile } from '../Invoices/components/Bits';
import { RevenueTypeChip, RunStatusChip, PercentComplete } from './components/Bits';
import { money, dateTime } from '../../invoicing';
import {
  getRevenueRun, overrideRevenueEntry, finalizeRevenueRun, abandonRevenueRun, num,
} from '../../api';
import type { RevenueEntry } from '../../api';
import { monthLabel } from './periods';

/**
 * One run, and the screen where a month is decided.
 *
 * This is the payload ADR-0004 turns into an authorization: the operator reads
 * these exact entries, adjusts what needs judgement, and the Finalize click is
 * the approval. So the page shows the figures in full rather than a summary —
 * there is no separate approval record to go back to, and finalizing writes
 * nothing outside Postgres, which makes this the only place the numbers are
 * ever reviewed.
 *
 * A retainer at zero is the one blocking state: it computes to zero by design,
 * so a zero means nobody has decided rather than nothing was earned.
 */

/** Needs a human before the month can close. Mirrors the server's rule in
 *  `revenue_run.finalize_run` — duplicated deliberately so the button can be
 *  disabled with a reason rather than the click returning a 409. */
/** Takes back revenue booked in an earlier month.
 *
 *  Legitimate — a period amount goes negative when a closed month is corrected,
 *  and absorbing corrections that way is why the ledger stores period amounts
 *  rather than cumulative ones. It is flagged rather than blocked because it is
 *  rare and consequential, not because it is wrong. */
function reversesRevenue(e: RevenueEntry) {
  return num(e.recognized_amount) < 0;
}

function needsDecision(e: RevenueEntry) {
  return e.revenue_type === 'retainer' && num(e.recognized_amount) === 0 && !e.overridden_at;
}

function OverrideRow({ entry, runId, onDone }: {
  entry: RevenueEntry;
  runId: string;
  onDone: () => void;
}) {
  const [amount, setAmount] = useState(entry.recognized_amount);
  const [reason, setReason] = useState(entry.override_reason ?? '');

  const save = useMutation({
    mutationFn: () =>
      overrideRevenueEntry(runId, entry.id, {
        recognized_amount: amount,
        override_reason: reason,
      }),
    onSuccess: onDone,
  });

  return (
    <tr className="bg-cyan-500/5 border-b border-slate-100">
      <td colSpan={8} className="px-4 py-3">
        <div className="flex items-end gap-3 flex-wrap">
          <label className="text-xs text-slate-600">
            <span className="block mb-1 font-medium">Recognized amount</span>
            <input
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              className="w-36 bg-white border border-slate-300 rounded px-2 py-1.5 text-sm tabular-nums"
            />
          </label>
          <label className="text-xs text-slate-600 flex-1 min-w-[16rem]">
            <span className="block mb-1 font-medium">
              Reason <span className="text-slate-400">— required</span>
            </span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Why this figure differs from what was computed"
              className="w-full bg-white border border-slate-300 rounded px-2 py-1.5 text-sm"
            />
          </label>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || !reason.trim()}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-700 hover:bg-cyan-500/25 disabled:opacity-40 transition-colors"
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={onDone}
            className="px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100"
          >
            Cancel
          </button>
        </div>
        <p className="text-[11px] text-slate-500 mt-2">
          The computed figure ({money(num(entry.computed_amount))}) is kept either way — this
          records what was booked instead, and why.
        </p>
        {save.error && (
          <p className="text-xs text-red-700 mt-2">{(save.error as Error).message}</p>
        )}
      </td>
    </tr>
  );
}

export default function RunDetail() {
  const { runId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);

  const { data: run, isLoading, error } = useQuery({
    queryKey: ['revenue-run', runId],
    queryFn: () => getRevenueRun(runId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['revenue-run', runId] });
    queryClient.invalidateQueries({ queryKey: ['revenue-runs'] });
    // Finalizing moves entries into the ledger, so both reading screens change.
    queryClient.invalidateQueries({ queryKey: ['revenue-entries'] });
    queryClient.invalidateQueries({ queryKey: ['revenue-summary'] });
  };

  const finalize = useMutation({ mutationFn: () => finalizeRevenueRun(runId), onSuccess: invalidate });
  const abandon = useMutation({
    mutationFn: () => abandonRevenueRun(runId),
    onSuccess: () => { invalidate(); navigate('/revenue/runs'); },
  });

  if (isLoading) {
    return <p className="text-xs text-slate-500 animate-pulse py-10 text-center">Loading run…</p>;
  }
  if (error || !run) {
    return (
      <p className="text-sm text-red-700 bg-red-500/5 border border-red-500/30 rounded-lg px-4 py-3">
        {(error as Error)?.message ?? 'Run not found.'}
      </p>
    );
  }

  const isDraft = run.status === 'draft';
  const undecided = run.entries.filter(needsDecision);
  const overridden = run.entries.filter((e) => e.overridden_at);
  const total = run.entries.reduce((s, e) => s + num(e.recognized_amount), 0);

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <Link
          to="/revenue/runs"
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Runs
        </Link>
        <h2 className="text-lg font-semibold text-slate-900">{monthLabel(run.period_month)}</h2>
        <RunStatusChip status={run.status} />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Projects" value={run.entries.length} />
        <StatTile
          label={isDraft ? 'Proposed' : 'Recognized'}
          value={money(total)}
          sub={isDraft ? 'nothing counts until finalized' : undefined}
        />
        <StatTile
          label="Needs a decision"
          value={undecided.length}
          tone={undecided.length > 0 ? 'warn' : 'default'}
          sub={undecided.length > 0 ? 'retainers at zero' : 'none outstanding'}
        />
        <StatTile label="Overridden" value={overridden.length} sub="by a human" />
      </div>

      {isDraft && (
        <div className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-3 flex-wrap">
          <span className="text-xs text-slate-500">
            {undecided.length > 0
              ? `${undecided.length} retainer${undecided.length > 1 ? 's' : ''} still need an amount. `
              : 'These figures become the ledger. '}
            Finalizing writes nothing outside this system — a month that goes wrong is abandoned
            and planned again.
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => abandon.mutate()}
              disabled={abandon.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-40"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {abandon.isPending ? 'Abandoning…' : 'Abandon'}
            </button>
            <button
              onClick={() => finalize.mutate()}
              disabled={finalize.isPending || undecided.length > 0}
              title={
                undecided.length > 0
                  ? 'Every retainer needs an amount, or an explicit zero with a reason.'
                  : undefined
              }
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/15 border border-emerald-500/40 text-emerald-700 hover:bg-emerald-500/25 disabled:opacity-40 transition-colors"
            >
              <Check className="w-3.5 h-3.5" />
              {finalize.isPending ? 'Finalizing…' : 'Finalize'}
            </button>
          </div>
        </div>
      )}

      {(finalize.error || abandon.error) && (
        <p className="text-sm text-red-700 bg-red-500/5 border border-red-500/30 rounded-lg px-4 py-3">
          {((finalize.error ?? abandon.error) as Error).message}
        </p>
      )}

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-right px-4 py-3 font-medium">Recognized</th>
              <th className="text-right px-4 py-3 font-medium">Computed</th>
              <th className="text-right px-4 py-3 font-medium">% complete</th>
              {/* Named for the period, because it is the period's own — the
                  percentage next to it is a fraction of the project's whole
                  effort, and an unqualified "Hours" invited reading the two as
                  the same quantity. */}
              <th className="text-right px-4 py-3 font-medium">Hours this month</th>
              <th className="text-right px-4 py-3 font-medium">Invoiced to date</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {run.entries.map((e) => {
              const isOverridden = Boolean(e.overridden_at);
              const decide = needsDecision(e);
              return (
                // Keyed on the Fragment: the row and its edit panel are two
                // siblings for one entry, so the key belongs on the pair.
                <Fragment key={e.id}>
                  <tr
                    className={`border-b border-slate-100 last:border-0 align-top ${
                      decide ? 'bg-amber-400/5' : 'hover:bg-slate-50'
                    }`}
                  >
                    <td className="px-4 py-2.5">
                      <span className="text-slate-900 font-medium whitespace-nowrap">
                        {e.harvest_project_name}
                      </span>
                      <span className="block text-[11px] text-slate-400">
                        {e.client_name ?? `Harvest ${e.harvest_project_id}`}
                      </span>
                      {decide && (
                        <span className="flex items-center gap-1 text-[11px] text-amber-700 mt-0.5">
                          <TriangleAlert className="w-3 h-3" />
                          Retainers are not computed — enter what was earned
                        </span>
                      )}
                      {/* An archived project is here only because it still had
                          a balance, and clearing that balance assumes the work
                          finished. The system cannot tell a completed project
                          from a cancelled one — both stop being booked in
                          Forecast, which is what drives completion to 100% —
                          so the assumption is stated rather than made quietly.
                          Shown whatever the amount: a zero on an archived
                          project is just as much a claim about it. */}
                      {e.project_is_active === false && (
                        <span className="flex items-start gap-1 text-[11px] text-violet-700 mt-0.5 max-w-sm">
                          <Archive className="w-3 h-3 mt-0.5 flex-shrink-0" />
                          <span>
                            Archived in Harvest — included because it still had
                            revenue to recognize. This assumes it was completed;
                            if it was cancelled, override with a reason.
                          </span>
                        </span>
                      )}
                      {/* A negative period amount is legitimate — it is how a
                          correction to a closed month is absorbed, which is the
                          reason the ledger stores period amounts rather than
                          cumulative ones. But it always means revenue booked
                          earlier is being taken back, and it is rare enough to
                          be worth a second look.

                          Both defects found during the first live close read as
                          large negatives and nothing distinguished them from an
                          ordinary figure: retainers reversing their whole
                          history, and fixed-fee projects reversing expenses
                          that were never classified. Derived from the amount,
                          so there is nothing to keep in sync. */}
                      {reversesRevenue(e) && (
                        <span className="flex items-start gap-1 text-[11px] text-red-700 mt-0.5 max-w-sm">
                          <TriangleAlert className="w-3 h-3 mt-0.5 flex-shrink-0" />
                          <span>
                            Reverses revenue recognized in an earlier month.
                            Expected for a correction — otherwise check the
                            figures before finalizing.
                          </span>
                        </span>
                      )}
                      {isOverridden && (
                        <span className="block text-[11px] text-cyan-700 mt-0.5 max-w-sm">
                          {e.override_reason} — {e.overridden_by}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-2.5"><RevenueTypeChip type={e.revenue_type} /></td>
                    <td className={`px-4 py-2.5 text-right tabular-nums font-medium whitespace-nowrap ${
                      reversesRevenue(e) ? 'text-red-700' : 'text-slate-900'
                    }`}>
                      {money(num(e.recognized_amount))}
                    </td>
                    {/* Always shown, never overwritten. "What did it say, what
                        did we book" has to be answerable from the row. */}
                    <td className={`px-4 py-2.5 text-right tabular-nums whitespace-nowrap ${
                      isOverridden ? 'text-slate-400 line-through' : 'text-slate-300'
                    }`}>
                      {isOverridden ? money(num(e.computed_amount)) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      <PercentComplete
                        value={e.percent_complete == null ? null : num(e.percent_complete)}
                        loggedToDate={
                          e.cumulative_hours == null ? null : num(e.cumulative_hours)
                        }
                        scheduled={
                          e.scheduled_hours == null ? null : num(e.scheduled_hours)
                        }
                      />
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                      {num(e.logged_hours).toLocaleString('en-US')}
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                      {money(num(e.invoiced_to_date))}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      {isDraft && editing !== e.id && (
                        <button
                          onClick={() => setEditing(e.id)}
                          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 ml-auto"
                        >
                          <Pencil className="w-3 h-3" /> Adjust
                        </button>
                      )}
                    </td>
                  </tr>
                  {editing === e.id && (
                    <OverrideRow
                      entry={e}
                      runId={runId}
                      onDone={() => { setEditing(null); invalidate(); }}
                    />
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Each figure is the revenue recognized <span className="font-medium">in this month</span> —
        computed as the project's cumulative total minus everything already recognized. That is why
        correcting a closed month needs no restatement: the correction changes the prior sum, so
        the next open month absorbs it.
        {run.status === 'recognized' && run.finalized_at && (
          <> Finalized {dateTime(run.finalized_at)} by {run.finalized_by}.</>
        )}
      </p>
    </div>
  );
}
