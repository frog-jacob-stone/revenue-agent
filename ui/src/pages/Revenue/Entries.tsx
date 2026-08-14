import { useMemo, useState } from 'react';
import { StatTile } from '../Invoices/components/Bits';
import { BillingTypeChip, PercentComplete } from './components/Bits';
import { money, shortDate } from '../../invoicing';
import { MOCK_REVENUE } from './mockData';

const ALL = 'all';

/**
 * Entries — every recognised-revenue row, flattened across runs.
 *
 * The columns are the real slim schema (`app/services/revenue.py::_SLIM_FIELDS`)
 * rather than a friendlier subset, because the point of this screen is to check
 * whether that schema is legible as a table before it gets an API.
 */
export default function Entries() {
  const { runs, entries } = MOCK_REVENUE;
  const [runId, setRunId] = useState<string>(ALL);

  const rows = useMemo(() => {
    const filtered = runId === ALL ? entries : entries.filter((e) => e.run_id === runId);
    // Newest month first, then by size within the month — same reading order as
    // the Overview grid, so the two screens agree about what matters.
    return [...filtered].sort(
      (a, b) =>
        b.date_recognized.localeCompare(a.date_recognized) || b.revenue_delta - a.revenue_delta,
    );
  }, [entries, runId]);

  const shown = rows.reduce((sum, e) => sum + e.revenue_delta, 0);
  const scope = runId === ALL
    ? `all ${runs.length} months`
    : runs.find((r) => r.id === runId)?.month_label ?? '';

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Entries shown" value={rows.length} sub={scope} />
        <StatTile label="Revenue in view" value={money(shown)} sub="sum of revenue delta" />
        <StatTile
          label="Fixed fee entries"
          value={rows.filter((e) => e.billing_type === 'Fixed Fee').length}
          sub="the only type with % complete"
        />
        <StatTile
          label="Projects"
          value={new Set(rows.map((e) => e.project_name)).size}
        />
      </div>

      <div className="flex items-center gap-3">
        <label htmlFor="rev-run-filter" className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          Run
        </label>
        <select
          id="rev-run-filter"
          value={runId}
          onChange={(e) => setRunId(e.target.value)}
          className="bg-white border border-slate-300 text-slate-700 text-xs rounded px-2 py-1.5"
        >
          <option value={ALL}>All months</option>
          {runs.map((r) => (
            <option key={r.id} value={r.id}>{r.month_label}</option>
          ))}
        </select>
        <span className="text-xs text-slate-400">
          Filters this list in the browser — there is no query behind it.
        </span>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Month</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-right px-4 py-3 font-medium">Revenue delta</th>
              <th className="text-right px-4 py-3 font-medium">Recognised to date</th>
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
                    {e.project_name}
                  </span>
                  <span className="block text-[11px] text-slate-400 tabular-nums">
                    Harvest {e.harvest_id}
                  </span>
                  {e.notes && (
                    <span className="block text-[11px] text-slate-500 mt-0.5 max-w-xs">
                      {e.notes}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-slate-600 text-xs whitespace-nowrap">
                  {shortDate(e.date_recognized)}
                </td>
                <td className="px-4 py-2.5"><BillingTypeChip type={e.billing_type} /></td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-900 font-medium whitespace-nowrap">
                  {money(e.revenue_delta)}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {money(e.total_recognized_revenue)}
                </td>
                <td className="px-4 py-2.5 text-right whitespace-nowrap">
                  <PercentComplete value={e.percentage_complete} />
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {e.logged_hours.toLocaleString('en-US')}
                  <span className="block text-[11px] text-slate-400">
                    of {e.scheduled_hours.toLocaleString('en-US')} sched
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {e.contracted_fees == null
                    ? <span className="text-slate-300">—</span>
                    : money(e.contracted_fees)}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                  {money(e.invoiced_to_date)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        <span className="font-medium">Recognised to date</span> is cumulative since the project
        began, so it will not sum across rows — <span className="font-medium">revenue delta</span> is
        the figure for the period and the one every rollup on this tab uses. Invoiced to date sits
        beside it because the gap between them, not either number alone, is what shows whether
        billing has kept up with delivery.
      </p>
    </div>
  );
}
