import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Archive } from 'lucide-react';
import { getProjects } from '../../api';
import { shortDate, dateTime } from '../../invoicing';

/**
 * Projects — the engagement roster, read from the Harvest snapshot cache.
 *
 * Active work is the list; archived is a separate view you switch to. A closed
 * engagement is a lookup, not something you scan past to see what is running,
 * so the toggle swaps the result set rather than extending it.
 *
 * Only four columns, because only four are honest. Harvest has no concept of a
 * committed end date — `ends_on` is editable and moves when a project slips —
 * so "End date" is what it says. A projected end arrives with forecasting.
 */
export default function ProjectList() {
  const [showArchived, setShowArchived] = useState(false);

  // The flag belongs in the key: it changes the request, so the two lists must
  // cache separately or switching would serve the wrong set.
  const { data: rows = [], isLoading, error } = useQuery({
    queryKey: ['projects', showArchived ? 'archived' : 'active'],
    queryFn: () => getProjects(showArchived),
  });

  // Every row carries the same sync time in practice; taking the max avoids
  // depending on that.
  const syncedAt = rows.reduce<string | null>(
    (latest, p) => (latest == null || p.synced_at > latest ? p.synced_at : latest),
    null,
  );

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Projects</h1>
        <p className="text-sm text-slate-600 mt-0.5">
          Billable engagements and their delivery dates, from Harvest.
        </p>
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        {/* Describes what is on screen, not what exists — the button swaps the
            list, so the other side's count is not fetched and not shown. */}
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          {isLoading ? '—' : `${rows.length} ${showArchived ? 'archived' : 'active'}`}
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
          {showArchived ? 'See active' : 'See archived'}
        </button>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Client</th>
              <th className="text-left px-4 py-3 font-medium">Start</th>
              <th className="text-left px-4 py-3 font-medium">End date</th>
            </tr>
          </thead>
          {/* Loading and error render in-table rather than replacing the page,
              so the toggle stays put instead of vanishing the moment it is
              clicked. */}
          <tbody>
            {isLoading && (
              <tr>
                <td
                  colSpan={4}
                  className="px-4 py-10 text-center text-xs text-slate-500 animate-pulse"
                >
                  Loading projects…
                </td>
              </tr>
            )}
            {!isLoading && error && (
              <tr>
                <td colSpan={4} className="px-4 py-10 text-center text-sm text-red-700">
                  {(error as Error).message}
                </td>
              </tr>
            )}
            {!isLoading && !error && rows.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-10 text-center text-sm text-slate-500">
                  {showArchived ? 'Nothing archived.' : 'No active projects.'}
                  <span className="block text-xs text-slate-400 mt-1">
                    Projects appear here after a Harvest snapshot refresh.
                  </span>
                </td>
              </tr>
            )}
            {!isLoading &&
              !error &&
              rows.map((p) => (
                <tr
                  key={p.harvest_id}
                  className="border-b border-slate-100 last:border-0 hover:bg-slate-50"
                >
                  <td className="px-4 py-3 text-slate-900 font-medium whitespace-nowrap">
                    {p.name}
                  </td>
                  <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                    {p.client_name ?? <span className="text-slate-300">—</span>}
                  </td>
                  {/* shortDate already renders null as an em dash and rebuilds
                      the date locally, so a date-only value cannot slip a day. */}
                  <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                    {shortDate(p.starts_on)}
                  </td>
                  <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                    {shortDate(p.ends_on)}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Start and end come from Harvest and are blank where Harvest has no date; both are
        editable there, so an end date tracks the current plan rather than what was committed.
        Read from the Harvest snapshot{syncedAt ? `, last synced ${dateTime(syncedAt)}` : ''} —
        not live. Projected end arrives with forecasting.
      </p>
    </div>
  );
}
