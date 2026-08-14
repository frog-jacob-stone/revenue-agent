// Sample data for the Revenue tab mockup. NOT LIVE — nothing here touches an
// API, and no screen outside `pages/Revenue/` may import it.
//
// It lives in its own file rather than beside real domain types on purpose:
// `src/invoicing.ts` records what happened last time a fixture shared a file
// with production types (people read the mocks as real). The shapes below
// deliberately mirror the real slim revenue schema
// (`app/services/revenue.py::_SLIM_FIELDS`) and the `trigger_revenue_recognition`
// entry shape, so wiring this up for real is a replacement of this file rather
// than a redesign of the screens that read it.

export type BillingType = 'Fixed Fee' | 'T&M' | 'MSF' | 'Hosting' | 'Retainer';

export interface RevenueEntry {
  id: string;
  run_id: string;
  project_name: string;
  harvest_id: number;
  date_recognized: string;            // ISO, last day of month
  billing_type: BillingType;
  total_recognized_revenue: number;   // cumulative since project inception
  revenue_delta: number;              // this period's actual revenue — use for rollups/charts
  percentage_complete: number | null; // Fixed Fee only
  logged_hours: number;
  scheduled_hours: number;
  contracted_fees: number | null;
  invoiced_to_date: number;
  notes?: string;
}

export type RevRecRunStatus = 'completed' | 'awaiting_approval' | 'blocked';

export interface RevRecRun {
  id: string;
  date_recognized: string;   // month key, e.g. "2026-07-31"
  month_label: string;       // "Jul 2026"
  status: RevRecRunStatus;
  project_count: number;
  total_recognized: number;  // sum of revenue_delta across entries
  triggered_at: string;
  triggered_by: string;
}

export interface RevenueMonth {
  key: string;   // "2026-07-31"
  label: string; // "Jul 2026"
}

export interface MockRevenueData {
  months: RevenueMonth[];
  runs: RevRecRun[];
  entries: RevenueEntry[];
}

/** Deterministic PRNG — the mockup must look the same on every reload, or a
 *  reviewer cannot tell a layout change from a data change. */
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function lastDayOfMonth(year: number, monthIndex: number) {
  // Day 0 of the next month is the last day of this one. Built in UTC so the
  // ISO slice cannot drift a day west of Greenwich.
  return new Date(Date.UTC(year, monthIndex + 1, 0)).toISOString().slice(0, 10);
}

function monthLabel(year: number, monthIndex: number) {
  return new Date(year, monthIndex, 1).toLocaleDateString('en-US', {
    month: 'short',
    year: 'numeric',
  });
}

interface ProjectSeed {
  name: string;
  harvest_id: number;
  billing_type: BillingType;
  contracted_fees: number | null;
  /** Fixed Fee: completion at the start and end of the window. */
  pct_from?: number;
  pct_to?: number;
  /** T&M: hours per month before jitter, and the blended bill rate. */
  hours?: number;
  rate?: number;
  /** MSF / Hosting / Retainer: the flat monthly figure before jitter. */
  monthly?: number;
  /** Revenue recognised before the window opened. */
  prior_recognized: number;
  notes?: string;
}

const PROJECTS: ProjectSeed[] = [
  {
    name: 'Meridian Health Portal',
    harvest_id: 41208871,
    billing_type: 'Fixed Fee',
    contracted_fees: 486_000,
    pct_from: 0.12,
    pct_to: 0.94,
    prior_recognized: 58_320,
    notes: 'Phase 2 scope added Feb; % complete re-baselined against the amended SOW.',
  },
  {
    name: 'Cascade Logistics Platform',
    harvest_id: 41209104,
    billing_type: 'T&M',
    contracted_fees: null,
    hours: 420,
    rate: 178,
    prior_recognized: 612_400,
  },
  {
    name: 'Northwind Retail Ops',
    harvest_id: 41211930,
    billing_type: 'Fixed Fee',
    contracted_fees: 268_500,
    pct_from: 0.34,
    pct_to: 1.0,
    prior_recognized: 91_290,
    notes: 'Reaches 100% this period — final acceptance signed, nothing left to recognise.',
  },
  {
    name: 'Atlas Field Services',
    harvest_id: 41214077,
    billing_type: 'MSF',
    contracted_fees: 540_000,
    monthly: 45_000,
    prior_recognized: 315_000,
  },
  {
    name: 'Brightline Analytics',
    harvest_id: 41216542,
    billing_type: 'T&M',
    contracted_fees: null,
    hours: 236,
    rate: 195,
    prior_recognized: 148_900,
  },
  {
    name: 'Sable & Co Storefront',
    harvest_id: 41218330,
    billing_type: 'Fixed Fee',
    contracted_fees: 182_000,
    pct_from: 0.05,
    pct_to: 0.71,
    prior_recognized: 9_100,
  },
  {
    name: 'Kestrel Manufacturing Cloud',
    harvest_id: 41219815,
    billing_type: 'Hosting',
    contracted_fees: null,
    monthly: 8_400,
    prior_recognized: 176_400,
  },
  {
    name: 'Verity Insurance Modernization',
    harvest_id: 41222604,
    billing_type: 'Retainer',
    contracted_fees: 360_000,
    monthly: 30_000,
    prior_recognized: 240_000,
    notes: 'Retainer recognised straight-line; overage hours billed outside rev rec.',
  },
];

const round2 = (n: number) => Math.round(n * 100) / 100;

/** Billable-hour multiplier by calendar month (Jan–Dec). Holiday weeks and
 *  summer PTO are the dominant swing in a T&M month. */
const SEASONAL = [1.02, 1.06, 1.09, 1.05, 1.0, 0.95, 0.89, 0.97, 1.07, 1.09, 0.87, 0.71];

/**
 * Twelve trailing complete months of recognised revenue for eight sample
 * projects, one run per month.
 *
 * The window ends with the month before the current one — rev rec runs after a
 * month closes, so the in-progress month has nothing to recognise yet.
 */
export function generateMockRevenueData(today = new Date()): MockRevenueData {
  const rand = mulberry32(0x5eed_1234);

  // Anchor on the previous month, then walk back eleven more.
  const anchor = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const months: RevenueMonth[] = [];
  for (let i = 11; i >= 0; i--) {
    const d = new Date(anchor.getFullYear(), anchor.getMonth() - i, 1);
    months.push({
      key: lastDayOfMonth(d.getFullYear(), d.getMonth()),
      label: monthLabel(d.getFullYear(), d.getMonth()),
    });
  }

  const entries: RevenueEntry[] = [];
  const runs: RevRecRun[] = [];

  // Cumulative state per project, carried across months.
  const recognized = new Map<string, number>();
  const invoiced = new Map<string, number>();
  const pct = new Map<string, number>();
  for (const p of PROJECTS) {
    recognized.set(p.name, p.prior_recognized);
    invoiced.set(p.name, round2(p.prior_recognized * (0.9 + rand() * 0.14)));
    pct.set(p.name, p.pct_from ?? 0);
  }

  months.forEach((month, monthIndex) => {
    const runId = `run_${month.key.slice(0, 7).replace('-', '')}`;
    let runTotal = 0;

    for (const p of PROJECTS) {
      let delta: number;
      let percentComplete: number | null = null;
      let loggedHours: number;

      if (p.billing_type === 'Fixed Fee') {
        // Completion walks toward its end state with jitter, never backwards —
        // percent complete that fell would mean revenue was clawed back.
        const target =
          p.pct_from! + ((p.pct_to! - p.pct_from!) * (monthIndex + 1)) / months.length;
        // The final month lands exactly on `pct_to` — jitter that leaves a
        // project at 99% would contradict a seed note saying it closed out.
        const last = monthIndex === months.length - 1;
        // Kept small: hours barely move on a fixed-fee month while earned value
        // arrives in lumps, so pct jitter lands almost undamped on the
        // revenue-per-hour grid. Wide jitter there reads as a broken metric.
        const jittered = last ? p.pct_to! : target + (rand() - 0.5) * 0.03;
        const next = Math.min(p.pct_to!, Math.max(pct.get(p.name)!, round2(jittered)));
        const prior = pct.get(p.name)!;
        pct.set(p.name, next);
        percentComplete = next;
        delta = round2(p.contracted_fees! * (next - prior));
        loggedHours = round2(140 + (next - prior) * p.contracted_fees! / 165);
      } else if (p.billing_type === 'T&M') {
        // Holidays and summer PTO are the real shape of a T&M month, and a
        // chart of twelve near-identical bars would not show whether the
        // visualisation reads at all.
        const calendarMonth = Number(month.key.slice(5, 7)) - 1;
        loggedHours = round2(p.hours! * SEASONAL[calendarMonth] * (0.82 + rand() * 0.36));
        // Realisation, not the card rate: staffing mix, discounts, and written-
        // off hours move the effective rate a few points either way. Billing
        // hours at exactly the contract rate would peg revenue-per-hour flat
        // for every T&M project, which is the one row worth watching.
        delta = round2(loggedHours * p.rate! * (0.94 + rand() * 0.13));
      } else {
        // MSF, Hosting, Retainer: flat by contract, only small true-ups move it.
        delta = round2(p.monthly! * (0.97 + rand() * 0.06));
        loggedHours = round2(p.monthly! / 210 * (0.8 + rand() * 0.5));
      }

      const total = round2(recognized.get(p.name)! + delta);
      recognized.set(p.name, total);
      // Invoicing trails recognition by a partial month on most engagements.
      const billed = round2(invoiced.get(p.name)! + delta * (0.78 + rand() * 0.3));
      invoiced.set(p.name, Math.min(billed, total));
      runTotal += delta;

      entries.push({
        id: `${runId}_${p.harvest_id}`,
        run_id: runId,
        project_name: p.name,
        harvest_id: p.harvest_id,
        date_recognized: month.key,
        billing_type: p.billing_type,
        total_recognized_revenue: total,
        revenue_delta: delta,
        percentage_complete: percentComplete,
        logged_hours: loggedHours,
        scheduled_hours: round2(loggedHours * (0.88 + rand() * 0.3)),
        contracted_fees: p.contracted_fees,
        invoiced_to_date: invoiced.get(p.name)!,
        // Only carry the seed note on the newest month, so the Entries table
        // does not read as though the same remark were re-typed twelve times.
        ...(p.notes && monthIndex === months.length - 1 ? { notes: p.notes } : {}),
      });
    }

    // Runs fire on the 3rd of the following month, once time entry has settled.
    const closed = new Date(`${month.key}T00:00:00Z`);
    closed.setUTCDate(closed.getUTCDate() + 3);
    closed.setUTCHours(14, 20, 0, 0);

    runs.push({
      id: runId,
      date_recognized: month.key,
      month_label: month.label,
      // The newest month is still waiting on a human — the state a reviewer
      // needs to see, since every other row is already settled history.
      status: monthIndex === months.length - 1 ? 'awaiting_approval' : 'completed',
      project_count: PROJECTS.length,
      total_recognized: round2(runTotal),
      triggered_at: closed.toISOString(),
      triggered_by: monthIndex % 3 === 0 ? 'scheduler' : 'jacob.stone@frogslayer.com',
    });
  });

  // Newest run first — the Runs table reads as history, most recent at top.
  runs.reverse();

  return { months, runs, entries };
}

/** One shared instance, so the three sub-tabs can never disagree about the
 *  numbers they are each showing. */
export const MOCK_REVENUE = generateMockRevenueData();
