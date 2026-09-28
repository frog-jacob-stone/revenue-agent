import type { ReactNode } from 'react';
import { money } from '../../invoicing';

/**
 * The three ways the Overview is read, as data rather than as three code paths.
 *
 * Revenue, effort and revenue-per-hour are not three reports. They are three
 * functions of the *same pair of numbers* — what a project-month earned and how
 * many hours went into it — so the page computes one grid of pairs and each
 * metric turns a pair into a number.
 *
 * Which gives the rule everything here follows:
 *
 *     sum the PAIRS, then apply the metric — never combine the metric's output.
 *
 * For revenue and effort that is trivially the same thing. For a rate it is the
 * difference between a blend and a mean, and only the blend is right: averaging
 * the monthly cells would weight a 40-hour month the same as a 400-hour one and
 * read high. One `rollup` per metric, called everywhere a total is needed, means
 * the grid footer, the right-hand column and the stat cards cannot disagree.
 */

export type MetricKey = 'revenue' | 'rate' | 'effort';

/** The two raw quantities every metric is built from. */
export interface Pair {
  rev: number;
  hours: number;
}

export interface Metric {
  key: MetricKey;
  /** The toggle button, and the noun used in card and grid headings. */
  label: string;
  /** The one number this metric reads off one pair. Null where the metric
   *  genuinely has no value — which `MonthGrid` renders as a dash. */
  value(p: Pair): number | null;
  /** Combine pairs into the pair a window's value is computed from. */
  rollup(ps: Pair[]): Pair;
  /** Cells, cards and the chart tooltip. */
  fmt(n: number): string;
  /** The Y axis, where eleven characters of currency will not fit. */
  axisFmt(n: number): string;
  /** Heading for the grid's total column and its footer row. */
  footerLabel: string;
  /** What the server should drop as an all-dashes row. */
  nonEmpty: 'revenue' | 'hours';
  /** Heading above the grid, before the month span is appended. */
  gridTitle: string;
  /** The paragraph under the grid explaining what a cell is. */
  note: ReactNode;
}

// ── Formatting ──────────────────────────────────────────────────────────────

/** Axis labels want `$1.2M` / `$486k`, not eleven characters of currency. */
export function compactMoney(n: number) {
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `$${Math.round(n / 1_000)}k`;
  return `$${Math.round(n)}`;
}

/** Hours to the tenth below ten, whole above — a 4,183.25-hour month is not
 *  more precise for the quarter, it is just harder to scan. */
export function hours(n: number) {
  return `${n.toLocaleString('en-US', {
    maximumFractionDigits: Math.abs(n) < 10 ? 1 : 0,
  })} h`;
}

export function compactHours(n: number) {
  if (Math.abs(n) >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return Math.round(n).toLocaleString('en-US');
}

// ── Rollups ─────────────────────────────────────────────────────────────────

const sumPairs = (ps: Pair[]): Pair => ps.reduce(
  (a, p) => ({ rev: a.rev + p.rev, hours: a.hours + p.hours }),
  { rev: 0, hours: 0 },
);

/**
 * Pairs with no hours are dropped from **both** sides of a rate, not just the
 * denominator.
 *
 * The imported history has revenue whose hours are simply unknown: Airtable's
 * hours history starts later than its revenue history, and every project's
 * first imported month is a catch-up lump covering years of prior work.
 * Counting that revenue against an incomplete hour count inflates the rate —
 * 2025 reads $260/hr that way against a real figure nearer $165. A rate is
 * about work done; revenue with no hours behind it has no rate, and leaving it
 * out says so.
 */
const pricedPairs = (ps: Pair[]): Pair => sumPairs(ps.filter((p) => p.hours > 0));

// ── The metrics ─────────────────────────────────────────────────────────────

export const METRICS: Record<MetricKey, Metric> = {
  revenue: {
    key: 'revenue',
    label: 'Revenue',
    value: (p) => p.rev,
    rollup: sumPairs,
    fmt: money,
    axisFmt: compactMoney,
    footerLabel: 'Total',
    nonEmpty: 'revenue',
    gridTitle: 'Revenue by project',
    note: (
      <>
        Cells are each project's revenue for that month (<code>recognized_amount</code>), not its
        cumulative recognized total — those are shown per entry on the Entries tab and cannot be
        summed across months without double counting.
      </>
    ),
  },

  rate: {
    key: 'rate',
    label: 'Blended rate',
    // A rate with no denominator is not zero. Null, so the grid shows a dash
    // and the reader is not told the project earned nothing per hour.
    value: (p) => (p.hours > 0 ? p.rev / p.hours : null),
    rollup: pricedPairs,
    fmt: money,
    axisFmt: compactMoney,
    footerLabel: 'Blended',
    // Not 'revenue': a project with hours and no revenue reads $0/hr, which is
    // real — work went in and nothing came out — and is exactly what someone
    // looking at rates wants to see.
    nonEmpty: 'hours',
    gridTitle: 'Revenue per billable hour',
    note: (
      <>
        Each cell is that month's <code>recognized_amount</code> ÷ that month's{' '}
        <code>logged_hours</code> — the rate the project actually earned in the period, which is
        what makes it comparable month to month. The right-hand column and the bottom row are
        blended rates (total revenue ÷ total hours), <span className="font-medium">not</span>{' '}
        averages of the cells beside them: averaging would weight a light month equally with a
        heavy one.
      </>
    ),
  },

  effort: {
    key: 'effort',
    label: 'Effort',
    value: (p) => p.hours,
    rollup: sumPairs,
    fmt: hours,
    axisFmt: compactHours,
    footerLabel: 'Total',
    nonEmpty: 'hours',
    gridTitle: 'Hours by project',
    note: (
      <>
        Cells are hours logged against the project in that month (<code>logged_hours</code>) —
        time already worked, not forecast. Scheduled hours are deliberately absent: they are a
        forward-looking snapshot taken when the month was recognized, so they cannot be summed
        down a column or across months.
      </>
    ),
  },
};

export const METRIC_ORDER: MetricKey[] = ['revenue', 'rate', 'effort'];
