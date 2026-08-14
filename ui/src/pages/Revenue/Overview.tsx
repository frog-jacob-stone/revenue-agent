import { useMemo } from 'react';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { StatTile, Delta } from '../Invoices/components/Bits';
import { money } from '../../invoicing';
import { MOCK_REVENUE } from './mockData';

/** Axis labels want `$1.2M` / `$486k`, not eleven characters of currency. */
function compactMoney(n: number) {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
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
 * Overview — the twelve-month picture, chart and grid over the same window.
 *
 * Rollups are on `revenue_delta`, never `total_recognized_revenue`: the latter
 * is cumulative since project inception, so summing it across months would
 * count every prior month again in each subsequent one.
 */
export default function Overview() {
  const { months, entries } = MOCK_REVENUE;

  const { byMonth, byProject, projectNames, ttmTotal, monthTotals } = useMemo(() => {
    const byMonth = new Map<string, number>();
    const byProject = new Map<string, Map<string, number>>();

    for (const e of entries) {
      byMonth.set(e.date_recognized, (byMonth.get(e.date_recognized) ?? 0) + e.revenue_delta);
      let row = byProject.get(e.project_name);
      if (!row) {
        row = new Map();
        byProject.set(e.project_name, row);
      }
      row.set(e.date_recognized, (row.get(e.date_recognized) ?? 0) + e.revenue_delta);
    }

    const monthTotals = months.map((m) => byMonth.get(m.key) ?? 0);
    // Biggest contributor first — the question this grid answers is which
    // projects carry the year, and alphabetical order hides that.
    const projectNames = [...byProject.keys()].sort((a, b) => {
      const sum = (n: string) => [...byProject.get(n)!.values()].reduce((x, y) => x + y, 0);
      return sum(b) - sum(a);
    });

    return {
      byMonth,
      byProject,
      projectNames,
      monthTotals,
      ttmTotal: monthTotals.reduce((a, b) => a + b, 0),
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
          By project — {window}
        </p>
        <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
                {/* Sticky so the project name stays readable while the twelve
                    month columns scroll under it. */}
                <th className="sticky left-0 z-10 bg-white text-left px-4 py-3 font-medium min-w-[200px]">
                  Project
                </th>
                {months.map((m) => (
                  <th key={m.key} className="text-right px-3 py-3 font-medium whitespace-nowrap">
                    {m.label}
                  </th>
                ))}
                <th className="text-right px-4 py-3 font-medium bg-slate-50">Total</th>
              </tr>
            </thead>
            <tbody>
              {projectNames.map((name) => {
                const row = byProject.get(name)!;
                const total = months.reduce((sum, m) => sum + (row.get(m.key) ?? 0), 0);
                return (
                  <tr key={name} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 group">
                    <td className="sticky left-0 z-10 bg-white group-hover:bg-slate-50 px-4 py-2.5 text-slate-900 font-medium whitespace-nowrap">
                      {name}
                    </td>
                    {months.map((m) => {
                      const v = row.get(m.key);
                      return (
                        <td key={m.key} className="px-3 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                          {v == null || v === 0
                            ? <span className="text-slate-300">—</span>
                            : money(v)}
                        </td>
                      );
                    })}
                    <td className="px-4 py-2.5 text-right tabular-nums font-medium text-slate-900 bg-slate-50 whitespace-nowrap">
                      {money(total)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-slate-200 bg-slate-50 text-slate-900 font-semibold">
                <td className="sticky left-0 z-10 bg-slate-50 px-4 py-3 text-xs uppercase tracking-wide">
                  Total
                </td>
                {months.map((m, i) => (
                  <td key={m.key} className="px-3 py-3 text-right tabular-nums whitespace-nowrap">
                    {money(monthTotals[i])}
                  </td>
                ))}
                <td className="px-4 py-3 text-right tabular-nums bg-slate-100 whitespace-nowrap">
                  {money(ttmTotal)}
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Cells are each project's revenue for that month (<code>revenue_delta</code>), not its
        cumulative recognised total — those are shown per entry on the Entries tab and cannot be
        summed across months without double counting.
      </p>
    </div>
  );
}
