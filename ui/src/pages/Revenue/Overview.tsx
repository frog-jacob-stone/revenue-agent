import { useMemo } from 'react';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { StatTile, Delta } from '../Invoices/components/Bits';
import MonthGrid from './components/MonthGrid';
import type { GridRow } from './components/MonthGrid';
import { money } from '../../invoicing';
import { MOCK_REVENUE } from './mockData';

/** Axis labels want `$1.2M` / `$486k`, not eleven characters of currency. */
function compactMoney(n: number) {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

/** Effective rate for a period: what that period earned over the hours it took.
 *  Null when there are no hours — a rate with no denominator is not zero. */
function rate(revenue: number, hours: number): number | null {
  return hours > 0 ? revenue / hours : null;
}

function ChartTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: { value: number }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm px-3 py-2">
      <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">{label}</p>
      <p className="text-sm font-semibold text-slate-900 tabular-nums mt-0.5">
        {money(payload[0].value)}
      </p>
    </div>
  );
}

/**
 * Overview — the twelve-month picture, chart and grids over the same window.
 *
 * Rollups are on `revenue_delta`, never `total_recognized_revenue`: the latter
 * is cumulative since project inception, so summing it across months would
 * count every prior month again in each subsequent one. The same reasoning
 * drives the revenue-per-hour grid — see the note beneath it.
 */
export default function Overview() {
  const { months, entries } = MOCK_REVENUE;

  const {
    byMonth, projectNames, ttmTotal, monthTotals, revenueRows, rateRows, rateFooter, ttmRate,
  } = useMemo(() => {
    // Revenue and hours are accumulated together: the per-hour grid needs both
    // halves of the ratio for the same project-month cell.
    const byMonth = new Map<string, number>();
    const byProject = new Map<string, Map<string, { rev: number; hours: number }>>();

    for (const e of entries) {
      byMonth.set(e.date_recognized, (byMonth.get(e.date_recognized) ?? 0) + e.revenue_delta);
      let row = byProject.get(e.project_name);
      if (!row) {
        row = new Map();
        byProject.set(e.project_name, row);
      }
      const cell = row.get(e.date_recognized) ?? { rev: 0, hours: 0 };
      cell.rev += e.revenue_delta;
      cell.hours += e.logged_hours;
      row.set(e.date_recognized, cell);
    }

    const monthTotals = months.map((m) => byMonth.get(m.key) ?? 0);
    // Biggest contributor first — the question these grids answer is which
    // projects carry the year, and alphabetical order hides that. Both grids
    // use the same order so a project sits on the same line in each.
    const projectNames = [...byProject.keys()].sort((a, b) => {
      const sum = (n: string) =>
        [...byProject.get(n)!.values()].reduce((x, c) => x + c.rev, 0);
      return sum(b) - sum(a);
    });

    const revenueRows: GridRow[] = projectNames.map((name) => {
      const row = byProject.get(name)!;
      const cells = months.map((m) => row.get(m.key)?.rev ?? null);
      return {
        name,
        cells,
        total: cells.reduce((sum: number, v) => sum + (v ?? 0), 0),
      };
    });

    // A rate row's "TTM" is the project's blended rate across the window —
    // total revenue over total hours. Averaging twelve monthly rates would
    // weight a 40-hour month the same as a 400-hour one and read high.
    const rateRows: GridRow[] = projectNames.map((name) => {
      const row = byProject.get(name)!;
      const cells = months.map((m) => {
        const c = row.get(m.key);
        return c ? rate(c.rev, c.hours) : null;
      });
      const rev = months.reduce((s, m) => s + (row.get(m.key)?.rev ?? 0), 0);
      const hours = months.reduce((s, m) => s + (row.get(m.key)?.hours ?? 0), 0);
      return { name, cells, total: rate(rev, hours) };
    });

    // Footer is the whole book's rate for that month, on the same principle:
    // every project's revenue over every project's hours, not a mean of rates.
    const rateFooter = months.map((m) => {
      let rev = 0;
      let hours = 0;
      for (const row of byProject.values()) {
        const c = row.get(m.key);
        if (c) {
          rev += c.rev;
          hours += c.hours;
        }
      }
      return rate(rev, hours);
    });

    const allHours = entries.reduce((s, e) => s + e.logged_hours, 0);
    const ttm = monthTotals.reduce((a, b) => a + b, 0);

    return {
      byMonth,
      projectNames,
      monthTotals,
      ttmTotal: ttm,
      revenueRows,
      rateRows,
      rateFooter,
      ttmRate: rate(ttm, allHours),
    };
  }, [months, entries]);

  const chartData = months.map((m) => ({
    label: m.label,
    revenue: byMonth.get(m.key) ?? 0,
  }));

  const latest = monthTotals[monthTotals.length - 1] ?? 0;
  const prior = monthTotals.length > 1 ? monthTotals[monthTotals.length - 2] : null;
  const window = `${months[0]?.label} – ${months[months.length - 1]?.label}`;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="TTM revenue" value={money(ttmTotal)} sub={window} />
        <StatTile
          label="Average / month"
          value={money(ttmTotal / (months.length || 1))}
          sub={`across ${months.length} months`}
        />
        <StatTile label="Active projects" value={projectNames.length} sub="recognising revenue" />
        <StatTile
          label={months[months.length - 1]?.label ?? 'Latest month'}
          value={money(latest)}
          sub={<Delta current={latest} prior={prior} />}
        />
      </div>

      <div className="bg-white border border-slate-200 rounded-xl px-4 pt-4 pb-2">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          Recognised revenue by month
        </p>
        <div className="h-64 mt-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
              <CartesianGrid stroke="#e2e8f0" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: '#64748b', fontSize: 11 }}
                axisLine={{ stroke: '#e2e8f0' }}
                tickLine={false}
              />
              <YAxis
                tickFormatter={compactMoney}
                tick={{ fill: '#64748b', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={56}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: '#f1f5f9' }} />
              <Bar dataKey="revenue" fill="#06b6d4" radius={[4, 4, 0, 0]} maxBarSize={44} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div>
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium mb-2">
          Revenue by project — {window}
        </p>
        <MonthGrid
          months={months}
          rows={revenueRows}
          footer={{ label: 'Total', cells: monthTotals, total: ttmTotal }}
          fmt={money}
        />
        <p className="text-[11px] text-slate-400 leading-relaxed mt-2">
          Cells are each project's revenue for that month (<code>revenue_delta</code>), not its
          cumulative recognised total — those are shown per entry on the Entries tab and cannot be
          summed across months without double counting.
        </p>
      </div>

      <div>
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium mb-2">
          Revenue per billable hour — {window}
        </p>
        <MonthGrid
          months={months}
          rows={rateRows}
          footer={{ label: 'Blended', cells: rateFooter, total: ttmRate }}
          fmt={money}
        />
        <p className="text-[11px] text-slate-400 leading-relaxed mt-2">
          Each cell is that month's <code>revenue_delta</code> ÷ that month's{' '}
          <code>logged_hours</code> — the rate the project actually earned in the period, which is
          what makes it comparable month to month. The right-hand column and the bottom row are
          blended rates (total revenue ÷ total hours), <span className="font-medium">not</span>{' '}
          averages of the cells beside them: averaging would weight a light month equally with a
          heavy one. Note this differs from <code>blended_rate</code> in{' '}
          <code>app/services/revenue.py</code>, which divides cumulative recognised revenue by a
          single period's hours and so climbs every month regardless of performance.
        </p>
      </div>
    </div>
  );
}
