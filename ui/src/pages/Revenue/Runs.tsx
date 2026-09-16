import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Play } from 'lucide-react';
import { StatTile } from '../Invoices/components/Bits';
import { RunStatusChip } from './components/Bits';
import EmptyState from '../../components/shared/EmptyState';
import { money, dateTime } from '../../invoicing';
import { getRevenueRuns, planRevenueRun, num, ApiError } from '../../api';
import type { UnconfiguredProjectsDetail } from '../../api';
import { monthLabel } from './periods';

/**
 * Runs — one revenue recognition run per closed month.
 *
 * Totals are of each run's own entries, so a draft reports what it currently
 * proposes rather than what was booked. Only a recognized run's entries reach
 * the Overview and Entries tabs.
 *
 * Planning defaults to last month: a run recognizes a *closed* period, and the
 * month in progress has nothing settled to recognize yet.
 */

/** Last month, as an ISO first-of-month. Built from local date parts rather
 *  than an ISO slice, which would land a day earlier west of Greenwich and
 *  silently offer the wrong month on the first of each month. */
function lastMonth(): string {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`;
}

export default function Runs() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [period, setPeriod] = useState(() => lastMonth().slice(0, 7));

  const { data: runs = [], isLoading, error } = useQuery({
    queryKey: ['revenue-runs'],
    queryFn: () => getRevenueRuns(),
  });

  const plan = useMutation({
    mutationFn: () => planRevenueRun(`${period}-01`),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['revenue-runs'] });
      // Straight to the review screen: the draft is not the outcome, reading
      // and deciding it is.
      navigate(`/revenue/runs/${run.id}`);
    },
  });

  // Two distinct 409s. Unconfigured projects come back with the list so this
  // can name them and link; a month that already has a run is a plain string.
  const planError = plan.error as ApiError | Error | undefined;
  const unconfigured =
    planError instanceof ApiError &&
    planError.status === 409 &&
    typeof planError.detail === 'object' &&
    planError.detail !== null &&
    'unconfigured_projects' in planError.detail
      ? (planError.detail as UnconfiguredProjectsDetail)
      : null;

  const drafts = runs.filter((r) => r.status === 'draft').length;
  const recognized = runs
    .filter((r) => r.status === 'recognized')
    .reduce((sum, r) => sum + num(r.total_recognized), 0);

  if (error) {
    return (
      <p className="text-sm text-red-700 bg-red-500/5 border border-red-500/30 rounded-lg px-4 py-3">
        {(error as Error).message}
      </p>
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Runs" value={runs.length} sub="one per closed month" />
        <StatTile
          label="Drafts"
          value={drafts}
          tone={drafts > 0 ? 'warn' : 'default'}
          sub={drafts > 0 ? 'nothing counts until finalized' : 'all settled'}
        />
        <StatTile label="Recognized across runs" value={money(recognized)} sub="finalized only" />
        <StatTile
          label="Projects in latest run"
          value={runs[0]?.entry_count ?? 0}
          sub="active engagements"
        />
      </div>

      <div className="bg-white border border-slate-200 rounded-lg px-4 py-3 space-y-2">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs text-slate-500 uppercase tracking-wide font-medium">
            New run
          </span>
          <input
            type="month"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            className="bg-white border border-slate-300 text-slate-700 text-xs rounded px-2 py-1.5"
          />
          <span className="text-xs text-slate-400 flex-1 min-w-[18rem]">
            Reads Harvest hours and each project's configuration, then drafts one entry per active
            project. Nothing counts until you finalize it.
          </span>
          <button
            onClick={() => plan.mutate()}
            disabled={plan.isPending}
            className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-700 hover:bg-cyan-500/25 disabled:opacity-40 transition-colors"
          >
            <Play className="w-3.5 h-3.5" />
            {plan.isPending ? 'Planning…' : 'Run Revenue Recognition'}
          </button>
        </div>

        {unconfigured && (
          <p className="text-xs text-amber-800 bg-amber-400/10 border border-amber-400/40 rounded-lg px-3 py-2">
            {unconfigured.unconfigured_projects.length} project
            {unconfigured.unconfigured_projects.length > 1 ? 's need' : ' needs'} revenue
            configuration before this month can be planned:{' '}
            {unconfigured.unconfigured_projects.map((p) => p.harvest_project_name).join(', ')}.{' '}
            <Link to="/revenue/setup" className="font-medium underline">Set them up</Link>.
          </p>
        )}
        {planError && !unconfigured && (
          <p className="text-xs text-red-700">{planError.message}</p>
        )}
      </div>

      <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">Run history</p>
      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Month</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-right px-4 py-3 font-medium">Projects</th>
              <th className="text-right px-4 py-3 font-medium">Recognized</th>
              <th className="text-left px-4 py-3 font-medium">Planned</th>
              <th className="text-left px-4 py-3 font-medium">By</th>
              <th className="text-left px-4 py-3 font-medium">Finalized</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-xs text-slate-500 animate-pulse">
                  Loading runs…
                </td>
              </tr>
            )}
            {!isLoading && runs.length === 0 && (
              <tr>
                <td colSpan={7} className="px-0 py-0">
                  <EmptyState
                    title="No runs yet"
                    description="A run appears here once one is planned. Historical months arrive with the Airtable import."
                  />
                </td>
              </tr>
            )}
            {runs.map((run) => (
              <tr
                key={run.id}
                onClick={() => navigate(`/revenue/runs/${run.id}`)}
                className="border-b border-slate-100 last:border-0 hover:bg-slate-50 cursor-pointer"
              >
                <td className="px-4 py-3 text-slate-900 font-medium whitespace-nowrap">
                  {monthLabel(run.period_month)}
                </td>
                <td className="px-4 py-3"><RunStatusChip status={run.status} /></td>
                <td className="px-4 py-3 text-right text-slate-700 tabular-nums">
                  {run.entry_count}
                </td>
                <td className="px-4 py-3 text-right text-slate-900 tabular-nums">
                  {money(num(run.total_recognized))}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                  {dateTime(run.created_at)}
                </td>
                <td className="px-4 py-3 text-xs text-slate-600">{run.created_by}</td>
                <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                  {run.finalized_at
                    ? dateTime(run.finalized_at)
                    : <span className="text-slate-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        A run drafts entries; only finalizing makes them the ledger. Until then they count toward
        nothing — not the{' '}
        <Link to="/revenue/overview" className="text-cyan-600 hover:text-cyan-600">Overview</Link>,
        not the{' '}
        <Link to="/revenue/entries" className="text-cyan-600 hover:text-cyan-600">Entries</Link> tab,
        and not the prior-period total that next month's figures are computed against. An abandoned
        run is a discarded draft, kept rather than deleted, and frees its month to be planned again.
      </p>
    </div>
  );
}
