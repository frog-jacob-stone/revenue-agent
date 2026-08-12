import { useState } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Play, ChevronRight, AlertOctagon, Loader2 } from 'lucide-react';
import { RunStatusChip, StatTile } from './components/Bits';
import { SeverityCount } from './components/FlagChip';
import { getBillingRuns, planBillingRun } from '../../api';
import { dateTime, money } from '../../invoicing';
import type { BillingRunSummary } from '../../invoicing';

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

export default function BillingRuns() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [runMonth, setRunMonth] = useState(currentMonth());
  const [error, setError] = useState<string | null>(null);

  // Draw runs are single-invoice and frequent — including them by default
  // would bury the monthly history they sit alongside. `?kind=draw` opts in, so
  // "All draw runs" from the Draws tab lands on a list that actually shows them
  // rather than on a page where they are filtered out.
  const [params] = useSearchParams();
  const [includeDraws, setIncludeDraws] = useState(
    params.get('kind') === 'draw' || params.get('kind') === 'all',
  );
  const { data: runs = [], isLoading } = useQuery({
    queryKey: ['billing-runs', includeDraws ? 'all' : 'monthly'],
    queryFn: () => getBillingRuns(includeDraws ? 'all' : 'monthly'),
  });

  const plan = useMutation({
    mutationFn: () => planBillingRun(`${runMonth}-01`),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
      navigate(`/invoices/runs/${run.id}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  // A run whose planning was blocked by an unresolved in-flight row surfaces
  // that as an item-level flag, so the banner reads off the summary counts.
  const blocked = runs.filter((r) => (r.flag_counts?.error ?? 0) > 0);
  const open = runs.find((r) => r.status === 'awaiting_approval');

  return (
    <div className="space-y-5">
      {error && (
        <div className="flex items-start gap-3 bg-red-500/10 border border-red-500/40 rounded-lg px-4 py-3">
          <AlertOctagon className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
          <p className="text-red-700 text-sm">{error}</p>
        </div>
      )}

      {open && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatTile label="Open run" value={open.label} sub={<RunStatusChip status={open.status} />} />
          <StatTile
            label="Invoices to create"
            value={open.planned_count}
            sub={`${open.skipped_count} skipped`}
          />
          <StatTile label="Estimated value" value={money(open.planned_total)} sub="draft totals, pre-edit" />
          <StatTile
            label="Flags"
            value={
              <span className="flex items-center gap-3 text-sm">
                <SeverityCount severity="error" count={open.flag_counts?.error ?? 0} />
                <SeverityCount severity="warning" count={open.flag_counts?.warning ?? 0} />
              </span>
            }
            sub={`${open.flag_counts?.info ?? 0} info`}
          />
        </div>
      )}

      {blocked.length > 0 && !open && (
        <div className="flex items-start gap-3 bg-red-500/10 border border-red-500/40 rounded-lg px-4 py-3">
          <AlertOctagon className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="text-sm">
            <p className="text-red-700 font-medium">
              {blocked.length} run{blocked.length === 1 ? '' : 's'} carry error-severity flags
            </p>
            <p className="text-slate-600 text-xs mt-1">
              Open the run to see which groups are affected.
            </p>
          </div>
        </div>
      )}

      {/* Plan a run */}
      <div className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-3">
        <span className="text-xs text-slate-500 uppercase tracking-wide font-medium">New run</span>
        <input
          type="month"
          value={runMonth}
          onChange={(e) => setRunMonth(e.target.value)}
          className="bg-slate-100 border border-slate-300 text-slate-700 text-xs rounded px-2 py-1"
        />
        <span className="text-xs text-slate-400">
          Defaults to the current calendar month; override for backfills.
        </span>
        <button
          onClick={() => { setError(null); plan.mutate(); }}
          disabled={plan.isPending}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-600 hover:bg-cyan-500/25 disabled:opacity-50 transition-colors"
        >
          {plan.isPending
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Play className="w-3.5 h-3.5" />}
          {plan.isPending ? 'Planning…' : 'Plan (read-only)'}
        </button>
      </div>

      {/* Run history */}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          Run history
        </p>
        <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
          <input
            type="checkbox"
            checked={includeDraws}
            onChange={(e) => setIncludeDraws(e.target.checked)}
            className="w-3.5 h-3.5 rounded accent-cyan-600"
          />
          Include draw invoices
        </label>
      </div>
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Run month</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-left px-4 py-3 font-medium">Planned</th>
              <th className="text-right px-4 py-3 font-medium">Invoices</th>
              <th className="text-right px-4 py-3 font-medium">Value</th>
              <th className="text-left px-4 py-3 font-medium">Flags</th>
              <th className="w-8 px-3 py-3" />
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-xs text-slate-500 animate-pulse">
                  Loading…
                </td>
              </tr>
            ) : runs.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-sm text-slate-500">
                  No billing runs yet. Plan one above — planning never writes to Harvest.
                </td>
              </tr>
            ) : runs.map((run: BillingRunSummary, i) => (
              <tr
                key={run.id}
                onClick={() => navigate(`/invoices/runs/${run.id}`)}
                className={`hover:bg-slate-50 cursor-pointer transition-colors ${i < runs.length - 1 ? 'border-b border-slate-200' : ''}`}
              >
                <td className="px-4 py-3 text-slate-900 font-medium">{run.label}</td>
                <td className="px-4 py-3"><RunStatusChip status={run.status} /></td>
                <td className="px-4 py-3 text-xs text-slate-500">{dateTime(run.created_at)}</td>
                <td className="px-4 py-3 text-right text-slate-700">{run.planned_count}</td>
                <td className="px-4 py-3 text-right text-slate-700 tabular-nums">
                  {money(run.planned_total)}
                </td>
                <td className="px-4 py-3">
                  <span className="flex items-center gap-2.5">
                    <SeverityCount severity="error" count={run.flag_counts?.error ?? 0} />
                    <SeverityCount severity="warning" count={run.flag_counts?.warning ?? 0} />
                  </span>
                </td>
                <td className="px-3 py-3 text-slate-400"><ChevronRight className="w-4 h-4" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-slate-400">
        Planning is read-only against Harvest — it reads time, expenses, and existing invoices, and
        writes only to this system's ledger.{' '}
        <Link to="/invoices/groups" className="text-cyan-600 hover:text-cyan-600">
          Check config health
        </Link>{' '}
        before a run if projects have changed.
      </p>
    </div>
  );
}
