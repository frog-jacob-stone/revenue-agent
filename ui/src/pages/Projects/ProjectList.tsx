import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Archive, RefreshCw } from 'lucide-react';
import { getProjects, refreshProjects } from '../../api';
import { shortDate, dateTime } from '../../invoicing';
import type { ProjectSummary } from '../../api';

/**
 * Projects — the engagement roster.
 *
 * Two sources, and the columns say which is which. Start and end come from
 * Harvest, where both are editable and often stale. Projected end comes from
 * Forecast: the last day a person is actually booked. The gap between the last
 * two is the point of showing them together — a project booked months past its
 * Harvest end date has slipped, whether or not anyone updated Harvest.
 *
 * Active work is the list; archived is a separate view you switch to.
 */

/** Slipping when the schedule runs past the planned end. Both are ISO dates,
 *  so a string compare is the date compare. Archived work is history — its
 *  slip is not something to act on. */
function isSlipping(p: ProjectSummary) {
  return (
    p.is_active &&
    p.ends_on != null &&
    p.projected_end_date != null &&
    p.projected_end_date > p.ends_on
  );
}

export default function ProjectList() {
  const [showArchived, setShowArchived] = useState(false);
  const queryClient = useQueryClient();

  const { data: rows = [], isLoading, error } = useQuery({
    queryKey: ['projects', showArchived ? 'archived' : 'active'],
    queryFn: () => getProjects(showArchived),
  });

  // Refreshes Harvest *and* Forecast. Refreshing only one would leave half of
  // every row stale while looking like the page had updated.
  const refresh = useMutation({
    mutationFn: refreshProjects,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      // The Harvest snapshot feeds the billing screens too.
      queryClient.invalidateQueries({ queryKey: ['harvest-clients'] });
      queryClient.invalidateQueries({ queryKey: ['harvest-projects'] });
      queryClient.invalidateQueries({ queryKey: ['billing-health'] });
    },
  });

  const syncedAt = rows.reduce<string | null>(
    (latest, p) => (latest == null || p.synced_at > latest ? p.synced_at : latest),
    null,
  );
  const slipping = rows.filter(isSlipping).length;

  // Archived work has already ended, so a forecast of when it will end is
  // noise — and a stale one at that, since Forecast keeps the bookings long
  // after delivery stops. The column is dropped rather than blanked: four
  // columns of history read better than five with one dead.
  const showProjected = !showArchived;
  const columnCount = showProjected ? 5 : 4;

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Projects</h1>
        <p className="text-sm text-slate-600 mt-0.5">
          Billable engagements — planned dates from Harvest, delivery forecast from Forecast.
        </p>
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          {isLoading ? '—' : `${rows.length} ${showArchived ? 'archived' : 'active'}`}
          {slipping > 0 && (
            <span className="ml-2 text-amber-700 normal-case tracking-normal font-normal">
              · {slipping} booked past the planned end
            </span>
          )}
        </p>
        <div className="flex items-center gap-2">
          {/* Nothing schedules either sync, so both caches are only as fresh
              as the last time someone pressed this. Around 7s against the live
              account — the Harvest half costs a request per billable active
              project, which the rate limiter pipelines. */}
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            title="Re-read Harvest and Forecast"
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-40 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refresh.isPending ? 'animate-spin' : ''}`} />
            {refresh.isPending ? 'Refreshing…' : 'Refresh'}
          </button>
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
      </div>

      {refresh.error && (
        <p className="text-xs text-red-700">{(refresh.error as Error).message}</p>
      )}
      {/* A refresh can half-succeed: Harvest commits, then Forecast is
          unconfigured or down. The call returns 200 because the Harvest data
          really did update, so the shortfall has to be said out loud or the
          projected-end column would look merely empty. */}
      {refresh.data?.forecast_error && (
        <p className="text-xs text-amber-800 bg-amber-400/10 border border-amber-400/40 rounded-lg px-3 py-2">
          Harvest updated, but the forecast did not: {refresh.data.forecast_error} Projected end
          dates are as of the last successful sync.
        </p>
      )}

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Client</th>
              <th className="text-left px-4 py-3 font-medium">Start</th>
              <th className="text-left px-4 py-3 font-medium">End date</th>
              {showProjected && (
                <th className="text-left px-4 py-3 font-medium">Projected end</th>
              )}
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td
                  colSpan={columnCount}
                  className="px-4 py-10 text-center text-xs text-slate-500 animate-pulse"
                >
                  Loading projects…
                </td>
              </tr>
            )}
            {!isLoading && error && (
              <tr>
                <td colSpan={columnCount} className="px-4 py-10 text-center text-sm text-red-700">
                  {(error as Error).message}
                </td>
              </tr>
            )}
            {!isLoading && !error && rows.length === 0 && (
              <tr>
                <td colSpan={columnCount} className="px-4 py-10 text-center text-sm text-slate-500">
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
                  <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                    {shortDate(p.starts_on)}
                  </td>
                  <td className="px-4 py-3 text-slate-600 whitespace-nowrap">
                    {shortDate(p.ends_on)}
                  </td>
                  {/* Amber when the schedule runs past the planned end. The two
                      columns only earn their place together if the gap between
                      them is visible. */}
                  {showProjected && (
                    <td
                      className={`px-4 py-3 whitespace-nowrap ${
                        isSlipping(p) ? 'text-amber-700 font-medium' : 'text-slate-600'
                      }`}
                    >
                      {shortDate(p.projected_end_date)}
                    </td>
                  )}
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        {showProjected && (
          <>
            <span className="font-medium text-slate-500">Projected end</span> is the last day
            someone is booked in Forecast — placeholder bookings with no person against them
            are not counted. A dash means nobody is scheduled, which is normal for hosting and
            support retainers, and does not mean the work has stopped.{' '}
          </>
        )}
        Start and end come from Harvest and are blank where Harvest has no date; both are
        editable there, so an end date tracks the current plan rather than what was committed.
        {!showProjected && ' Archived work carries no projected end — it has already ended.'}{' '}
        Read from the last snapshot{syncedAt ? `, ${dateTime(syncedAt)}` : ''} — not live.
      </p>
    </div>
  );
}
