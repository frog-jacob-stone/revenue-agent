/**
 * The window the Overview reports on: two month pickers, nothing else.
 *
 * Opens on the current calendar year, not a trailing twelve months. Those
 * answer different questions — "how are we doing this year" against "what has
 * the last year looked like" — and the year is the one with a budget attached.
 *
 * Deliberately no year shortcut buttons. A row of them grows by one every
 * January, so the control gets steadily wider forever while the oldest chips
 * become the least useful things on the screen. Two date inputs express any
 * window, including the years, at a fixed size.
 */

/** Both ends are `YYYY-MM`, the value an `<input type="month">` holds. */
export interface Range {
  from: string;
  to: string;
}

/** January to December of the current year. */
export function currentYearRange(): Range {
  const year = new Date().getFullYear();
  return { from: `${year}-01`, to: `${year}-12` };
}

/** ISO first-of-month bounds, as the API wants them. `to` is the first of the
 *  last month — the server compares against first-of-month periods, so a
 *  December bound is `-12-01`, not `-12-31`. */
export function bounds(range: Range): { date_from: string; date_to: string } {
  return { date_from: `${range.from}-01`, date_to: `${range.to}-01` };
}

function monthName(month: string): string {
  const [y, m] = month.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString('en-US', {
    month: 'short',
    year: 'numeric',
  });
}

export function rangeLabel(range: Range): string {
  if (range.from === range.to) return monthName(range.from);
  // A whole calendar year reads as the year, which is what it will be most of
  // the time — "Jan 2026 – Dec 2026" is four times the width of "2026".
  const [fromYear, fromMonth] = range.from.split('-');
  const [toYear, toMonth] = range.to.split('-');
  if (fromYear === toYear && fromMonth === '01' && toMonth === '12') return fromYear;
  return `${monthName(range.from)} – ${monthName(range.to)}`;
}

interface Props {
  value: Range;
  onChange: (range: Range) => void;
}

export default function RangeFilter({ value, onChange }: Props) {
  const set = (next: Partial<Range>) => {
    const merged = { ...value, ...next };
    // Keep the ends ordered rather than rejecting the input: someone widening a
    // range naturally moves whichever end is nearer, and a validation error
    // here would be a lecture about which box to type in first.
    if (merged.from > merged.to) {
      if (next.from) merged.to = merged.from;
      else merged.from = merged.to;
    }
    onChange(merged);
  };

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs text-slate-500 uppercase tracking-wide font-medium mr-1">
        Period
      </span>
      <input
        type="month"
        value={value.from}
        onChange={(e) => set({ from: e.target.value })}
        className="bg-white border border-slate-300 text-slate-700 text-xs rounded px-2 py-1.5"
      />
      <span className="text-xs text-slate-400">to</span>
      <input
        type="month"
        value={value.to}
        onChange={(e) => set({ to: e.target.value })}
        className="bg-white border border-slate-300 text-slate-700 text-xs rounded px-2 py-1.5"
      />
    </div>
  );
}
