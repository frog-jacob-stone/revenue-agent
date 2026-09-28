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
import MetricToggle from './components/MetricToggle';
import { METRICS, hours as fmtHours } from './metrics';
import type { MetricKey, Pair } from './metrics';
import { money } from '../../invoicing';
import {
  getRevenueClients, getRevenueEntries, getRevenueSummary, num,
} from '../../api';
import { toPeriod } from './periods';

function ChartTooltip({ active, payload, label, fmt }: {
  active?: boolean;
  payload?: { value: number | null }[];
  label?: string;
  fmt: (n: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const value = payload[0].value;
  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-sm px-3 py-2">
      <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">{label}</p>
      <p className="text-sm font-semibold text-slate-900 tabular-nums mt-0.5">
        {value == null ? 'no hours' : fmt(value)}
      </p>
    </div>
  );
}

/**
 * Overview — one period, one metric, one set of cards, one chart, one table.
 *
 * The metric selector is the organising idea. Revenue, revenue per billable
 * hour and effort are three questions about the same book, asked one at a time,
 * so the page answers whichever is selected rather than stacking a full-width
 * grid per measure. What each metric *is* lives in `metrics.tsx`; this file
 * only arranges what it returns.
 *
 * Two queries with two jobs. `/revenue/summary` supplies every month's revenue
 * and hours, so the chart, the stat cards and the grid footers all read the
 * same server-side aggregate rather than three browser-side re-derivations that
 * could drift apart. `/revenue/entries` fills in the per-project cells, which
 * is the one thing the summary cannot answer.
 *
 * Both are bounded by the selected period and narrowed to the selected clients,
 * so the browser filters nothing — a project with nothing to show is never
 * sent, rather than being sent and hidden.
 *
 * Everything rolls up on `recognized_amount` — the revenue for that month.
 * `cumulative_recognized` is since project inception, so summing it across
 * months would count every earlier month again in each later one.
 */
export default function Overview() {
  const [range, setRange] = useState<Range>(currentYearRange);
  const [clientIds, setClientIds] = useState<number[]>([]);
  const [metric, setMetric] = useState<MetricKey>('revenue');
  const m = METRICS[metric];
  const window = bounds(range);
  const scope = { ...window, client_ids: clientIds };

  const summary = useQuery({
    queryKey: ['revenue-summary', scope],
    queryFn: () => getRevenueSummary(scope),
  });

  const entries = useQuery({
    // The grid's rows are its projects, so "projects with nothing this period"
    // is a question about which rows exist — answered where the rows come from
    // rather than by dropping them after they arrive. What counts as nothing
    // follows the metric, so it belongs in the key: switching refetches once
    // and is cached per metric thereafter.
    queryKey: ['revenue-entries', scope, m.nonEmpty],
    queryFn: () => getRevenueEntries({ ...scope, non_empty: m.nonEmpty }),
  });

  // Options come from the *unfiltered* window, so selecting a client never
  // removes the others from the list. Keyed on the period alone for the same
  // reason — re-fetching on every selection would make the list flicker and
  // shrink under the cursor. The metric is absent from the key deliberately:
  // the endpoint returns both measures, so switching changes the number beside
  // each name without disturbing the names themselves.
  const clients = useQuery({
    queryKey: ['revenue-clients', window],
    queryFn: () => getRevenueClients(window),
  });

  const windowMonths = summary.data ?? [];
  const months = useMemo(
    () => windowMonths.map((x) => toPeriod(x.period_month)),
    [windowMonths],
  );

  const {
    projectNames, rows, footerCells, windowPair, monthPairs,
  } = useMemo(() => {
    // Revenue and hours accumulate together: every metric is a function of the
    // pair, so the grid is built once and read three ways.
    const byProject = new Map<string, Map<string, Pair>>();
    const inWindow = new Set(months.map((x) => x.key));

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
    const monthPairs: Pair[] = windowMonths.map((x) => ({
      rev: num(x.recognized_amount),
      hours: num(x.logged_hours),
    }));

    // Alphabetical. This grid is scanned for a project someone already has in
    // mind, and a name is findable in a sorted list in a way that a ranking is
    // not — the ranking is visible in the numbers anyway, and the total column
    // sorts by it on a click.
    const projectNames = [...byProject.keys()].sort((a, b) => a.localeCompare(b));

    // Cells and totals both come from the metric, so a row's right-hand figure
    // is the window's value for that project rather than a sum of its cells —
    // which is the same number for revenue and hours, and the difference
    // between a blend and a mean for a rate.
    const rows: GridRow[] = projectNames.map((name) => {
      const row = byProject.get(name)!;
      return {
        name,
        cells: months.map((x) => {
          const p = row.get(x.key);
          return p ? m.value(p) : null;
        }),
        total: m.value(m.rollup([...row.values()])),
      };
    });

    return {
      projectNames,
      rows,
      monthPairs,
      footerCells: monthPairs.map((p) => m.value(p)),
      windowPair: m.rollup(monthPairs),
    };
  }, [months, windowMonths, entries.data, m]);

  const chartData = windowMonths.map((x, i) => ({
    label: toPeriod(x.period_month).label,
    value: m.value(monthPairs[i]),
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

  const label = rangeLabel(range);
  const span = months.length
    ? `${months[0].label} – ${months[months.length - 1].label}`
    : label;

  // Two rows, because they are two different kinds of control. The toggle
  // chooses *which question* the page answers; the period and clients narrow
  // *what it answers it over*. On one line they read as a single row of
  // filters, and the toggle is the more consequential of the two.
  const filter = (
    <div className="space-y-2.5">
      <MetricToggle value={metric} onChange={setMetric} />
      <div className="flex items-center gap-3 flex-wrap">
        <RangeFilter value={range} onChange={setRange} />
        <ClientFilter
          clients={clients.data ?? []}
          value={clientIds}
          onChange={setClientIds}
          metric={metric}
        />
      </div>
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

  const windowValue = m.value(windowPair);
  const latest = footerCells[footerCells.length - 1] ?? null;
  const prior = footerCells.length > 1 ? footerCells[footerCells.length - 2] : null;
  // The rate drops hours-less months from both sides of its blend, so how many
  // months are actually behind the headline figure is worth saying out loud.
  const priced = monthPairs.filter((p) => p.hours > 0).length;
  const monthsSub = priced === months.length
    ? `${months.length} month${months.length === 1 ? '' : 's'}`
    : `${priced} of ${months.length} months`;

  return (
    <div className="space-y-5">
      {filter}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile
          label={`${m.label} — ${label}`}
          value={windowValue == null ? '—' : m.fmt(windowValue)}
          sub={span}
        />
        {metric === 'rate' ? (
          // Cards 2 and 3 are literally the numerator and denominator of card
          // 1, taken from the same rollup — so the blend is not a number the
          // reader has to take on trust.
          <>
            <StatTile label="Revenue" value={money(windowPair.rev)} sub={monthsSub} />
            <StatTile label="Hours" value={fmtHours(windowPair.hours)} sub={monthsSub} />
          </>
        ) : (
          <>
            <StatTile
              label="Average / month"
              value={m.fmt((windowValue ?? 0) / (months.length || 1))}
              sub={`across ${months.length} month${months.length === 1 ? '' : 's'}`}
            />
            <StatTile
              label="Active projects"
              value={projectNames.length}
              sub={metric === 'effort' ? 'logging hours' : 'recognizing revenue'}
            />
          </>
        )}
        <StatTile
          label={months[months.length - 1]?.label ?? 'Latest month'}
          value={latest == null ? '—' : m.fmt(latest)}
          sub={
            latest == null
              ? <span className="text-xs text-slate-400">no hours</span>
              : <Delta current={latest} prior={prior} />
          }
        />
      </div>

      <div className="bg-white border border-slate-200 rounded-xl px-4 pt-4 pb-2">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          {m.gridTitle} by month
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
                tickFormatter={m.axisFmt}
                tick={{ fill: '#64748b', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={56}
              />
              <Tooltip content={<ChartTooltip fmt={m.fmt} />} cursor={{ fill: '#f1f5f9' }} />
              <Bar dataKey="value" fill="#06b6d4" radius={[4, 4, 0, 0]} maxBarSize={44} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div>
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium mb-2">
          {m.gridTitle} — {span}
        </p>
        <MonthGrid
          months={months}
          rows={rows}
          footer={{ label: m.footerLabel, cells: footerCells, total: windowValue }}
          fmt={m.fmt}
        />
        <p className="text-[11px] text-slate-400 leading-relaxed mt-2">
          {m.note}
          {metric === 'rate' && priced < months.length && (
            <>
              {' '}
              <span className="text-amber-700">
                {months.length - priced} month{months.length - priced === 1 ? '' : 's'} in this
                period recognized revenue with no hours recorded against it, and{' '}
                {months.length - priced === 1 ? 'is' : 'are'} left out of the blended figures
                entirely — counting that revenue against an incomplete hour count would overstate
                the rate.
              </span>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
