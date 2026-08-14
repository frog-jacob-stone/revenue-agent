import { useState } from 'react';
import { Archive, FlaskConical } from 'lucide-react';
import StubBadge from '../../components/shared/StubBadge';
import { shortDate } from '../../invoicing';
import { ACTIVE_PROJECTS, ARCHIVED_PROJECTS, isSlipping } from './mockData';

/**
 * Projects — a stub, not a feature.
 *
 * Five columns and nothing behind them. The only project data this system has
 * is `harvest_projects`, a read cache of Harvest's list, which holds neither a
 * committed nor a projected end date — so every date below is invented. See
 * "Revenue Reporting & Project Tracking" in PROGRESS.md.
 *
 * Active work is the list; archived is a separate view you switch to. A closed
 * engagement is a lookup, not something you scan past to see what is running.
 */
export default function ProjectList() {
  const [showArchived, setShowArchived] = useState(false);
  const rows = showArchived ? ARCHIVED_PROJECTS : ACTIVE_PROJECTS;

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div className="flex items-start gap-3 bg-amber-400/10 border border-amber-400/40 rounded-lg px-4 py-2.5">
        <FlaskConical className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
        <p className="text-amber-800 text-xs">
          <span className="font-semibold">Sample data — not live.</span>{' '}
          Every client and date below is fabricated. Nothing in this system records a committed end
          date, a projected end date, or an archive state; the only project data it holds is a read
          cache of Harvest's list. This is a stub of the eventual screen, here so its shape can be
          reviewed.
        </p>
      </div>

      <div>
        <h1 className="text-xl font-semibold text-slate-900">
          Projects
          <StubBadge />
        </h1>
        <p className="text-sm text-slate-600 mt-0.5">
          Engagements and their delivery dates — what was committed against what is forecast.
        </p>
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        {/* Says what is on screen, not what exists — the two differ now that
            the button swaps the list rather than extending it. */}
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          {showArchived
            ? `${ARCHIVED_PROJECTS.length} archived`
            : `${ACTIVE_PROJECTS.length} active`}
        </p>
        <button
          onClick={() => setShowArchived((v) => !v)}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
            showArchived
              ? 'bg-slate-900 text-white border-slate-900'
              : 'border-slate-300 text-slate-600 hover:bg-slate-100'
          }`}
        >
          <Archive className="w-3.5 h-3.5" />
          {showArchived
            ? `See active (${ACTIVE_PROJECTS.length})`
            : `See archived (${ARCHIVED_PROJECTS.length})`}
        </button>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Client</th>
              <th className="text-left px-4 py-3 font-medium">Start</th>
              <th className="text-left px-4 py-3 font-medium">Committed end</th>
              <th className="text-left px-4 py-3 font-medium">Projected end</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-sm text-slate-500">
                  {showArchived ? 'Nothing archived.' : 'No active projects.'}
                </td>
              </tr>
            ) : rows.map((p) => (
              <tr
                key={p.harvest_id}
                className="border-b border-slate-100 last:border-0 hover:bg-slate-50"
              >
                <td className="px-4 py-3 text-slate-900 font-medium whitespace-nowrap">
                  {p.name}
                </td>
                <td className="px-4 py-3 text-slate-600 whitespace-nowrap">{p.client_name}</td>
                <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                  {shortDate(p.start_date)}
                </td>
                <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                  {p.committed_end_date == null
                    ? <span className="text-slate-300">—</span>
                    : shortDate(p.committed_end_date)}
                </td>
                {/* Amber when the forecast is past what was committed. The two
                    columns only earn their place together if the gap between
                    them is visible. Archived work is history — its slip is not
                    something to act on. */}
                <td className={`px-4 py-3 whitespace-nowrap ${
                  isSlipping(p) && !p.archived ? 'text-amber-700 font-medium' : 'text-slate-600'
                }`}>
                  {p.projected_end_date == null
                    ? <span className="text-slate-300">—</span>
                    : shortDate(p.projected_end_date)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        A dash means no date exists rather than a date of zero: open-ended work (T&amp;M, hosting)
        never had a committed end, and not every engagement has been re-forecast. Archived
        engagements that closed before the trailing twelve months show no revenue on the Revenue
        tab — they are absent from that window, not missing from it. Building this for real needs a
        project record this system owns; Harvest has no field for either end date.
      </p>
    </div>
  );
}
