// Recognition periods are months, not dates. The API sends an ISO
// first-of-month string; the screens need "Jan 2026".

export interface Period {
  /** The ISO first-of-month, as the API sends it. Used as a map key. */
  key: string;
  /** "Jan 2026". */
  label: string;
}

/**
 * "2026-01-01" -> "Jan 2026".
 *
 * Components are parsed by hand rather than fed to `new Date(iso)`, which would
 * read the string as UTC midnight and render as December for anyone west of
 * Greenwich. Same reason `shortDate` in `invoicing.ts` does it this way.
 */
export function monthLabel(iso: string): string {
  const [y, m] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString('en-US', {
    month: 'short',
    year: 'numeric',
  });
}

export function toPeriod(iso: string): Period {
  return { key: iso.slice(0, 10), label: monthLabel(iso) };
}
