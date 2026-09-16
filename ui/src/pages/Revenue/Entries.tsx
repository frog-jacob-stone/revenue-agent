import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { StatTile } from '../Invoices/components/Bits';
import { RevenueTypeChip, PercentComplete } from './components/Bits';
import EmptyState from '../../components/shared/EmptyState';
import { money } from '../../invoicing';
import { getRevenueEntries, num } from '../../api';
import { monthLabel } from './periods';

const ALL = 'all';

/**
 * Entries — every recognized-revenue row, flattened across runs.
 *
 * Filtered by month rather than by run. One live run owns a month (the partial
 * unique index in migration 0040 enforces it), so "which run" and "which month"
 * are the same question asked twice, and the month is the one a human actually
 * holds in their head.
 *
 * Only finalized runs appear — a draft is a proposal, and the server does not
 * serve it here.
 */
export default function Entries() {
  const [month, setMonth] = useState<string>(ALL);

  // Same query key as the Overview grid, so switching tabs is free.
  const { data: entries = [], isLoading, error } = useQuery({
    queryKey: ['revenue-entries'],
    queryFn: () => getRevenueEntries(),
  });

  const months = useMemo(
    () => [...new Set(entries.map((e) => e.period_month.slice(0, 10)))].sort().reverse(),
    [entries],
  );

  const rows = useMemo(() => {
    const filtered = month === ALL
      ? entries
      : entries.filter((e) => e.period_month.slice(0, 10) === month);
    // Newest month first, then by size within the month — same reading order as
    // the Overview grid, so the two screens agree about what matters.
    return [...filtered].sort(
      (a, b) =>
        b.period_month.localeCompare(a.period_month) ||
        num(b.recognized_amount) - num(a.recognized_amount),
    );
  }, [entries, month]);

  const shown = rows.reduce((sum, e) => sum + num(e.recognized_amount), 0);
  const scope = month === ALL ? `all ${months.length} months` : monthLabel(month);

  if (error) {
    return (
      <p className="text-sm text-red-700 bg-red-500/5 border border-red-500/30 rounded-lg px-4 py-3">
        {(error as Error).message}
      </p>
    );
  }

  if (isLoading) {
    return (
      <p className="text-xs text-slate-500 animate-pulse py-10 text-center">Loading entries…</p>
    );
  }

  if (entries.length === 0) {
    return (
      <EmptyState
        title="No entries yet"
        description="An entry is written for each project when a revenue recognition run is finalized."
      />
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Entries shown" value={rows.length} sub={scope} />
        <StatTile label="Revenue in view" value={money(shown)} sub="sum of the period amounts" />
        <StatTile
          label="Fixed fee entries"
          value={rows.filter((e) => e.revenue_type === 'fixed_fee').length}
          sub="the only type with % complete"
        />
        <StatTile
          label="Projects"
          value={new Set(rows.map((e) => e.harvest_project_name)).size}
        />
      </div>

      <div className="flex items-center gap-3">
        <label htmlFor="rev-month-filter" className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          Month
        </label>
        <select
          id="rev-month-filter"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="bg-white border border-slate-300 text-slate-700 text-xs rounded px-2 py-1.5"
        >
          <option value={ALL}>All months</option>
          {months.map((m) => (
            <option key={m} value={m}>{monthLabel(m)}</option>
          ))}
        </select>
        <span className="text-xs text-slate-400">
          Filters the loaded rows in the browser — there is no second query behind it.
        </span>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Month</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-right px-4 py-3 font-medium">Recognized</th>
              <th className="text-right px-4 py-3 font-medium">Cumulative</th>
              <th className="text-right px-4 py-3 font-medium">% complete</th>
              <th className="text-right px-4 py-3 font-medium">Hours</th>
              <th className="text-right px-4 py-3 font-medium">Contracted</th>
              <th className="text-right px-4 py-3 font-medium">Invoiced to date</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 align-top">
                <td className="px-4 py-2.5">
                  <span className="text-slate-900 font-medium whitespace-nowrap">
                    {e.harvest_project_name}
                  </span>
                  <span className="block text-[11px] text-slate-400">
                    {e.client_name ?? `Harvest ${e.harvest_project_id}`}
                  </span>
                  {e.notes && (
                    <span className="block text-[11px] text-slate-500 mt-0.5 max-w-xs">
                      {e.notes}
                    </span>
                  )}
                  {/* An overridden figure is not what the system computed, and
                      the difference is the whole reason the column exists. */}
                  {e.overridden_at && (
                    <span className="block text-[11px] text-amber-700 mt-0.5 max-w-xs">
                      Overridden from {money(num(e.computed_amount))} by {e.overridden_by}
                      {e.override_reason ? ` — ${e.override_reason}` : ''}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-slate-600 text-xs whitespace-nowrap">
                  {monthLabel(e.period_month)}
                </td>
                <td className="px-4 py-2.5"><RevenueTypeChip type={e.revenue_type} /></td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-900 font-medium whitespace-nowrap">
                  {money(num(e.recognized_amount))}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {money(num(e.cumulative_recognized))}
                </td>
                <td className="px-4 py-2.5 text-right whitespace-nowrap">
                  <PercentComplete
                    value={e.percent_complete == null ? null : num(e.percent_complete)}
                  />
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {num(e.logged_hours).toLocaleString('en-US')}
                  <span className="block text-[11px] text-slate-400">
                    of {num(e.scheduled_hours).toLocaleString('en-US')} sched
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {e.contracted_fees == null
                    ? <span className="text-slate-300">—</span>
                    : money(num(e.contracted_fees))}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {money(num(e.invoiced_to_date))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        <span className="font-medium">Recognized</span> is the revenue for that month and is the
        figure every rollup on this tab uses. <span className="font-medium">Cumulative</span> is the
        running total since the project began, so it will not sum across rows — it is derived at
        read time rather than stored, which is why correcting an earlier month flows through to
        every later one. Invoiced to date sits beside it because the gap between them, not either
        number alone, is what shows whether billing has kept up with delivery.
      </p>
    </div>
  );
}
