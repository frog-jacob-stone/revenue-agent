import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { StatTile, Delta } from '../Invoices/components/Bits';
import MonthGrid from './components/MonthGrid';
import type { GridRow } from './components/MonthGrid';
import EmptyState from '../../components/shared/EmptyState';
import RangeFilter, { bounds, currentYearRange, rangeLabel } from './components/RangeFilter';
import type { Range } from './components/RangeFilter';
import ClientFilter from './components/ClientFilter';
import { money } from '../../invoicing';
import {
  getRevenueClients, getRevenueEntries, getRevenueSummary, num,
} from '../../api';
import { toPeriod } from './periods';

/** Axis labels want `$1.2M` / `$486k`, not eleven characters of currency. */
function compactMoney(n: number) {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

/** Effective rate for a period: what it earned over the hours it took.
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
 * Overview — the selected period, chart and grids over the same window.
 *
 * Two queries with two jobs. `/revenue/summary` supplies every month total, so
 * the chart, the stat tiles and the grid footers all read the same server-side
 * aggregate rather than three browser-side re-derivations that could drift
 * apart. `/revenue/entries` fills in the per-project cells, which is the one
 * thing the summary cannot answer.
 *
 * Both queries are bounded by the selected period, so the browser filters
 * nothing — a project with no revenue this year is never sent, rather than
 * being sent and hidden.
 *
 * Everything rolls up on `recognized_amount` — the revenue for that month.
 * `cumulative_recognized` is since project inception, so summing it across
 * months would count every earlier month again in each later one.
 */
export default function Overview() {
  const [range, setRange] = useState<Range>(currentYearRange);
  const [clientIds, setClientIds] = useState<number[]>([]);
  const window = bounds(range);
  const scope = { ...window, client_ids: clientIds };

  const summary = useQuery({
    queryKey: ['revenue-summary', scope],
    queryFn: () => getRevenueSummary(scope),
  });

  const entries = useQuery({
    queryKey: ['revenue-entries', scope, 'with-revenue'],
    // The grid's rows are its projects, so "projects with no revenue this
    // period" is a question about which rows exist — answered where the rows
    // come from rather than by dropping them after they arrive.
    queryFn: () => getRevenueEntries({ ...scope, exclude_empty_projects: true }),
  });

  // Options come from the *unfiltered* window, so selecting a client never
  // removes the others from the list. Keyed on the period alone for the same
  // reason — re-fetching on every selection would make the list flicker and
  // shrink under the cursor.
  const clients = useQuery({
    queryKey: ['revenue-clients', window],
    queryFn: () => getRevenueClients(window),
  });

  const windowMonths = summary.data ?? [];
  const months = useMemo(
    () => windowMonths.map((m) => toPeriod(m.period_month)),
    [windowMonths],
  );

  const {
    projectNames, windowTotal, monthTotals, revenueRows, rateRows, rateFooter,
    windowRate, monthsWithoutHours,
  } = useMemo(() => {
    // Revenue and hours accumulate together: the per-hour grid needs both
    // halves of the ratio for the same project-month cell.
    const byProject = new Map<string, Map<string, { rev: number; hours: number }>>();
    const inWindow = new Set(months.map((m) => m.key));

    for (const e of entries.data ?? []) {
      const key = e.period_month.slice(0, 10);
      if (!inWindow.has(key)) continue;
      let row = byProject.get(e.harvest_project_name);
      if (!row) {
        row = new Map();
        byProject.set(e.harvest_project_name, row);
      }
      const cell = row.get(key) ?? { rev: 0, hours: 0 };
      cell.rev += num(e.recognized_amount);
      cell.hours += num(e.logged_hours);
      row.set(key, cell);
    }

    // From the server aggregate, not from the entries above — one source for
    // every total on the screen.
    const monthTotals = windowMonths.map((m) => num(m.recognized_amount));
    const total = monthTotals.reduce((a, b) => a + b, 0);
    // Same exclusion as the rate rows: only months whose hours are known can
    // contribute to a rate, on either side of the division.
    const priced = windowMonths.filter((m) => num(m.logged_hours) > 0);

    // Alphabetical. These grids are scanned for a project someone already has
    // in mind, and a name is findable in a sorted list in a way that a revenue
    // ranking is not — the ranking is visible in the numbers anyway. Both grids
    // use the same order so a project sits on the same line in each.
    const projectNames = [...byProject.keys()].sort((a, b) => a.localeCompare(b));

    const revenueRows: GridRow[] = projectNames.map((name) => {
      const row = byProject.get(name)!;
      const cells = months.map((m) => row.get(m.key)?.rev ?? null);
      return {
        name,
        cells,
        total: cells.reduce((sum: number, v) => sum + (v ?? 0), 0),
      };
    });

    // A rate row's total is the project's blended rate across the window —
    // total revenue over total hours. Averaging the monthly rates would weight
    // a 40-hour month the same as a 400-hour one and read high.
    //
    // Months with no hours are left out of **both** sides of the blend, not
    // just the denominator. The imported history has revenue whose hours are
    // simply unknown (Airtable's hours history starts later than its revenue
    // history, and every project's first imported month is a catch-up lump
    // covering years of prior work). Counting that revenue against an
    // incomplete hour count inflates the rate — 2025 reads $260/hr that way
    // against a real figure nearer $165. A rate is about work done; revenue
    // with no hours behind it has no rate, and excluding it says so.
    const rateRows: GridRow[] = projectNames.map((name) => {
      const row = byProject.get(name)!;
      const cells = months.map((m) => {
        const c = row.get(m.key);
        return c ? rate(c.rev, c.hours) : null;
      });
      const priced = months
        .map((m) => row.get(m.key))
        .filter((c): c is { rev: number; hours: number } => (c?.hours ?? 0) > 0);
      return {
        name,
        cells,
        total: rate(
          priced.reduce((s, c) => s + c.rev, 0),
          priced.reduce((s, c) => s + c.hours, 0),
        ),
      };
    });

    // Footer is the whole book's rate for that month, on the same principle:
    // every project's revenue over every project's hours, not a mean of rates.
    const rateFooter = windowMonths.map((m) =>
      rate(num(m.recognized_amount), num(m.logged_hours)),
    );

    return {
      projectNames,
      monthTotals,
      windowTotal: total,
      revenueRows,
      rateRows,
      rateFooter,
      windowRate: rate(
        priced.reduce((s, m) => s + num(m.recognized_amount), 0),
        priced.reduce((s, m) => s + num(m.logged_hours), 0),
      ),
      monthsWithoutHours: windowMonths.length - priced.length,
    };
  }, [months, windowMonths, entries.data]);

  const chartData = windowMonths.map((m) => ({
    label: toPeriod(m.period_month).label,
    revenue: num(m.recognized_amount),
  }));

  const isLoading = summary.isLoading || entries.isLoading;
  const error = summary.error ?? entries.error;

  if (error) {
    return (
      <p className="text-sm text-red-700 bg-red-500/5 border border-red-500/30 rounded-lg px-4 py-3">
        {(error as Error).message}
      </p>
    );
  }

  if (isLoading) {
    return (
      <p className="text-xs text-slate-500 animate-pulse py-10 text-center">
        Loading revenue…
      </p>
    );
  }

  const latest = monthTotals[monthTotals.length - 1] ?? 0;
  const prior = monthTotals.length > 1 ? monthTotals[monthTotals.length - 2] : null;
  const label = rangeLabel(range);
  const span = months.length
    ? `${months[0].label} – ${months[months.length - 1].label}`
    : label;

  const filter = (
    <div className="flex items-center gap-3 flex-wrap">
      <RangeFilter value={range} onChange={setRange} />
      <ClientFilter
        clients={clients.data ?? []}
        value={clientIds}
        onChange={setClientIds}
      />
    </div>
  );

  // The filter stays on screen: an empty window is something the reader has to
  // be able to get back out of, and hiding the control would strand them.
  if (months.length === 0) {
    return (
      <div className="space-y-5">
        {filter}
        <EmptyState
          title={`Nothing recognized in ${label}`}
          description={
            clientIds.length > 0
              ? 'No revenue for the selected clients in this period. Clear the client filter, or try a wider period.'
              : 'Months appear here once a revenue recognition run is finalized. Try a wider period.'
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {filter}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label={`Revenue — ${label}`} value={money(windowTotal)} sub={span} />
        <StatTile
          label="Average / month"
          value={money(windowTotal / (months.length || 1))}
          sub={`across ${months.length} month${months.length === 1 ? '' : 's'}`}
        />
        <StatTile label="Active projects" value={projectNames.length} sub="recognizing revenue" />
        <StatTile
          label={months[months.length - 1]?.label ?? 'Latest month'}
          value={money(latest)}
          sub={<Delta current={latest} prior={prior} />}
        />
      </div>

      <div className="bg-white border border-slate-200 rounded-xl px-4 pt-4 pb-2">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          Recognized revenue by month
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
          Revenue by project — {span}
        </p>
        <MonthGrid
          months={months}
          rows={revenueRows}
          footer={{ label: 'Total', cells: monthTotals, total: windowTotal }}
          fmt={money}
        />
        <p className="text-[11px] text-slate-400 leading-relaxed mt-2">
          Cells are each project's revenue for that month (<code>recognized_amount</code>), not its
          cumulative recognized total — those are shown per entry on the Entries tab and cannot be
          summed across months without double counting.
        </p>
      </div>

      <div>
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium mb-2">
          Revenue per billable hour — {span}
        </p>
        <MonthGrid
          months={months}
          rows={rateRows}
          footer={{ label: 'Blended', cells: rateFooter, total: windowRate }}
          fmt={money}
        />
        <p className="text-[11px] text-slate-400 leading-relaxed mt-2">
          Each cell is that month's <code>recognized_amount</code> ÷ that month's{' '}
          <code>logged_hours</code> — the rate the project actually earned in the period, which is
          what makes it comparable month to month. The right-hand column and the bottom row are
          blended rates (total revenue ÷ total hours), <span className="font-medium">not</span>{' '}
          averages of the cells beside them: averaging would weight a light month equally with a
          heavy one.
          {monthsWithoutHours > 0 && (
            <>
              {' '}
              <span className="text-amber-700">
                {monthsWithoutHours} month{monthsWithoutHours === 1 ? '' : 's'} in this period
                recognized revenue with no hours recorded against it, and {monthsWithoutHours === 1
                  ? 'is'
                  : 'are'}{' '}
                left out of the blended figures entirely — counting that revenue against an
                incomplete hour count would overstate the rate.
              </span>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
