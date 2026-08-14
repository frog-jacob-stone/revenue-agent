// Sample data for the Projects tab stub. NOT LIVE — same rules as
// `pages/Revenue/mockData.ts`: nothing here touches an API, and no screen
// outside `pages/Projects/` may import it.
//
// The active roster comes from the Revenue mock so a reviewer moving between
// the two tabs sees the same engagements. Dates live here because they are a
// delivery concern, not a revenue one — there is no schema for either yet
// (`harvest_projects` is a read cache of Harvest and holds no committed or
// projected end date, and no archive flag this system owns).

import { MOCK_PROJECTS } from '../Revenue/mockData';
import type { BillingType } from '../Revenue/mockData';

export interface ProjectRow {
  name: string;
  harvest_id: number;
  billing_type: BillingType;
  /** The account the project is delivered for. In Harvest a project always
   *  belongs to a client, so this is never null. */
  client_name: string;
  /** Kickoff. Every engagement has one. */
  start_date: string;
  /** The end date first committed to the client. Null for open-ended work
   *  (T&M, hosting) where no end was ever promised. */
  committed_end_date: string | null;
  /** Current delivery forecast; for an archived project, when it actually
   *  ended. Null when there is nothing to forecast — an evergreen engagement,
   *  or one nobody has re-forecast yet. */
  projected_end_date: string | null;
  archived: boolean;
}

type Detail = Omit<ProjectRow, 'name' | 'harvest_id' | 'billing_type'>;

/** Keyed by project name so a roster change surfaces as a missing key here
 *  rather than as a silently short table. */
const DETAIL: Record<string, Detail> = {
  'Meridian Health Portal': {
    client_name: 'Meridian Health Partners',
    start_date: '2025-02-03',
    committed_end_date: '2026-06-30',
    projected_end_date: '2026-11-20',
    archived: false,
  },
  'Cascade Logistics Platform': {
    client_name: 'Cascade Freight Group',
    start_date: '2024-09-16',
    committed_end_date: null,
    projected_end_date: null,
    archived: false,
  },
  // Reached 100% complete in the last month of the revenue window and closed
  // out — the one archived project that still shows revenue on the Revenue tab.
  'Northwind Retail Ops': {
    client_name: 'Northwind Retail Group',
    start_date: '2025-01-13',
    committed_end_date: '2026-05-29',
    projected_end_date: '2026-07-31',
    archived: true,
  },
  'Atlas Field Services': {
    client_name: 'Atlas Industrial Holdings',
    start_date: '2024-06-03',
    committed_end_date: '2026-05-31',
    projected_end_date: '2027-05-31',
    archived: false,
  },
  'Brightline Analytics': {
    client_name: 'Brightline Media',
    start_date: '2025-06-02',
    committed_end_date: null,
    projected_end_date: '2026-12-31',
    archived: false,
  },
  'Sable & Co Storefront': {
    client_name: 'Sable & Co',
    start_date: '2025-07-07',
    committed_end_date: '2026-10-30',
    projected_end_date: '2026-12-18',
    archived: false,
  },
  'Kestrel Manufacturing Cloud': {
    client_name: 'Kestrel Manufacturing',
    start_date: '2023-11-01',
    committed_end_date: null,
    projected_end_date: null,
    archived: false,
  },
  'Verity Insurance Modernization': {
    client_name: 'Verity Mutual',
    start_date: '2025-03-17',
    committed_end_date: '2026-09-30',
    projected_end_date: '2026-09-30',
    archived: false,
  },
};

/**
 * Engagements that ended before the revenue window opened.
 *
 * Deliberately absent from the Revenue mock's roster rather than missing from
 * it by accident: a project that closed in 2024 has no entry in a trailing
 * twelve-month view, so the two tabs are consistent, not contradictory.
 */
const ARCHIVED_ONLY: ProjectRow[] = [
  {
    name: 'Pinnacle Dealer Network',
    harvest_id: 41198442,
    billing_type: 'Fixed Fee',
    client_name: 'Pinnacle Auto Group',
    start_date: '2024-01-08',
    committed_end_date: '2025-03-31',
    projected_end_date: '2025-03-31',
    archived: true,
  },
  {
    name: 'Halcyon Benefits Portal',
    harvest_id: 41191307,
    billing_type: 'Fixed Fee',
    client_name: 'Halcyon Benefits',
    start_date: '2023-03-06',
    committed_end_date: '2024-02-29',
    projected_end_date: '2024-04-19',
    archived: true,
  },
  {
    name: 'Ridgeline Freight Audit',
    harvest_id: 41184905,
    billing_type: 'T&M',
    client_name: 'Ridgeline Freight Co',
    start_date: '2022-08-15',
    committed_end_date: null,
    projected_end_date: '2023-11-30',
    archived: true,
  },
];

const FALLBACK: Detail = {
  client_name: '—',
  start_date: '2025-01-01',
  committed_end_date: null,
  projected_end_date: null,
  archived: false,
};

export const MOCK_PROJECT_ROWS: ProjectRow[] = [
  ...MOCK_PROJECTS.map((p) => ({ ...p, ...(DETAIL[p.name] ?? FALLBACK) })),
  ...ARCHIVED_ONLY,
];

export const ACTIVE_PROJECTS = MOCK_PROJECT_ROWS.filter((p) => !p.archived);

/** Most recently ended first — the archived list is read newest-back. Undated
 *  ends sort last rather than sorting as the empty string. */
export const ARCHIVED_PROJECTS = MOCK_PROJECT_ROWS
  .filter((p) => p.archived)
  .sort((a, b) => (b.projected_end_date ?? '').localeCompare(a.projected_end_date ?? ''));

/** True when delivery is forecast past what was committed. Both are ISO dates,
 *  so a string compare is the date compare. */
export function isSlipping(p: ProjectRow) {
  return (
    p.committed_end_date != null &&
    p.projected_end_date != null &&
    p.projected_end_date > p.committed_end_date
  );
}
