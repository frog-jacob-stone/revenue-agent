import { useMemo, useState } from 'react';
import { ArrowDown, ArrowUp } from 'lucide-react';
import type { Period } from '../periods';

export interface GridRow {
  name: string;
  /** One entry per month, in `months` order. `null` renders as a dash. */
  cells: (number | null)[];
  total: number | null;
}

interface Props {
  months: Period[];
  rows: GridRow[];
  footer: { label: string; cells: (number | null)[]; total: number | null };
  fmt: (n: number) => string;
}

type SortKey = 'name' | 'total';

/**
 * Project × month grid with a sticky project column and a totals row.
 *
 * Shared by the revenue grid and the revenue-per-hour grid. It renders numbers
 * and does not derive them: a rate column cannot be summed the way a currency
 * column can, so each caller computes its own totals and hands them over. The
 * component never adds anything up itself.
 *
 * Sorting is local to each grid rather than lifted, so the two can be ordered
 * independently — "who earned the most" and "who earns the most per hour" are
 * different questions and sorting one by the other's answer helps nobody. Both
 * open on project name, so in the default state a project still sits on the
 * same line in each.
 */
export default function MonthGrid({ months, rows, footer, fmt }: Props) {
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: 'name',
    desc: false,
  });

  const sorted = useMemo(() => {
    const dir = sort.desc ? -1 : 1;
    return [...rows].sort((a, b) => {
      if (sort.key === 'name') return dir * a.name.localeCompare(b.name);
      // Nulls last in both directions: a project with no rate is missing a
      // denominator, not sitting at the bottom of the range, and floating it
      // to the top on one click would read as the worst performer.
      if (a.total == null || b.total == null) {
        return a.total == null ? (b.total == null ? 0 : 1) : -1;
      }
      return dir * (a.total - b.total);
    });
  }, [rows, sort]);

  // Clicking the column you are already on flips direction; a new column
  // starts ascending for a name and descending for a number, which is what
  // each is nearly always wanted in first.
  const toggle = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key === 'total' }));

  const Arrow = sort.desc ? ArrowDown : ArrowUp;
  const sortable = (key: SortKey, label: string, align: string) => (
    <button
      onClick={() => toggle(key)}
      className={`flex items-center gap-1 w-full ${align} hover:text-slate-800 transition-colors ${
        sort.key === key ? 'text-slate-800' : ''
      }`}
    >
      {label}
      {sort.key === key && <Arrow className="w-3 h-3" />}
    </button>
  );

  const cell = (v: number | null) =>
    v == null || v === 0 ? <span className="text-slate-300">—</span> : fmt(v);

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
            {/* Sticky so the project name stays readable while the twelve
                month columns scroll under it. */}
            <th className="sticky left-0 z-10 bg-white text-left px-4 py-3 font-medium min-w-[200px]">
              {sortable('name', 'Project', 'justify-start')}
            </th>
            {months.map((m) => (
              <th key={m.key} className="text-right px-3 py-3 font-medium whitespace-nowrap">
                {m.label}
              </th>
            ))}
            <th className="text-right px-4 py-3 font-medium bg-slate-50 whitespace-nowrap">
              {sortable('total', footer.label, 'justify-end')}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.name} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 group">
              <td className="sticky left-0 z-10 bg-white group-hover:bg-slate-50 px-4 py-2.5 text-slate-900 font-medium whitespace-nowrap">
                {row.name}
              </td>
              {row.cells.map((v, i) => (
                <td
                  key={months[i]?.key ?? i}
                  className="px-3 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap"
                >
                  {cell(v)}
                </td>
              ))}
              <td className="px-4 py-2.5 text-right tabular-nums font-medium text-slate-900 bg-slate-50 whitespace-nowrap">
                {cell(row.total)}
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-slate-200 bg-slate-50 text-slate-900 font-semibold">
            <td className="sticky left-0 z-10 bg-slate-50 px-4 py-3 text-xs uppercase tracking-wide">
              {footer.label}
            </td>
            {footer.cells.map((v, i) => (
              <td
                key={months[i]?.key ?? i}
                className="px-3 py-3 text-right tabular-nums whitespace-nowrap"
              >
                {cell(v)}
              </td>
            ))}
            <td className="px-4 py-3 text-right tabular-nums bg-slate-100 whitespace-nowrap">
              {cell(footer.total)}
            </td>
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
