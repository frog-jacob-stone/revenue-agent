import type { RevenueMonth } from '../mockData';

export interface GridRow {
  name: string;
  /** One entry per month, in `months` order. `null` renders as a dash. */
  cells: (number | null)[];
  total: number | null;
}

interface Props {
  months: RevenueMonth[];
  rows: GridRow[];
  footer: { label: string; cells: (number | null)[]; total: number | null };
  fmt: (n: number) => string;
}

/**
 * Project × month grid with a sticky project column and a totals row.
 *
 * Shared by the revenue grid and the revenue-per-hour grid. It renders numbers
 * and does not derive them: a rate column cannot be summed the way a currency
 * column can, so each caller computes its own totals and hands them over. The
 * component never adds anything up itself.
 */
export default function MonthGrid({ months, rows, footer, fmt }: Props) {
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
              Project
            </th>
            {months.map((m) => (
              <th key={m.key} className="text-right px-3 py-3 font-medium whitespace-nowrap">
                {m.label}
              </th>
            ))}
            <th className="text-right px-4 py-3 font-medium bg-slate-50 whitespace-nowrap">
              {footer.label}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
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
