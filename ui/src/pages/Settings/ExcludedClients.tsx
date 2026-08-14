import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Info, X, Plus } from 'lucide-react';
import {
  getClientExclusions,
  getHarvestClients,
  excludeClient,
  unexcludeClient,
} from '../../api';
import { dateTime } from '../../invoicing';
import type { ExcludedClient } from '../../api';

/**
 * Settings → Excluded clients.
 *
 * Our own company is a Harvest client. So its projects — Time Off, R&D,
 * internal products — arrive through the same sync as real engagements, and
 * some of them are flagged billable, so no automatic rule catches them.
 *
 * Exclusion is keyed on the client, not the project: one row covers every
 * present and future project underneath it, which is the difference between
 * setting this once and maintaining a list forever.
 */
export default function ExcludedClients() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState('');
  const [reason, setReason] = useState('');

  const { data: excluded = [], isLoading, error } = useQuery({
    queryKey: ['client-exclusions'],
    queryFn: getClientExclusions,
  });

  // The default (already-filtered) list: a client that is excluded should not
  // be offered for exclusion again. Same key the group-create form uses.
  const { data: clients = [] } = useQuery({
    queryKey: ['harvest-clients', 'selectable'],
    queryFn: () => getHarvestClients(),
  });

  /** Every write returns the full list, so the cache is replaced rather than
   *  patched. Anything reading a project roster is now wrong. */
  const onWritten = (rows: ExcludedClient[]) => {
    queryClient.setQueryData(['client-exclusions'], rows);
    queryClient.invalidateQueries({ queryKey: ['projects'] });
    queryClient.invalidateQueries({ queryKey: ['billing-health'] });
    // The billing-group form's client and project pickers hide excluded
    // clients, so both are now stale. Prefix-invalidated: this covers the
    // 'selectable' and 'all' variants at once.
    queryClient.invalidateQueries({ queryKey: ['harvest-clients'] });
    queryClient.invalidateQueries({ queryKey: ['harvest-projects'] });
  };

  const add = useMutation({
    mutationFn: () => excludeClient(Number(selected), reason.trim() || null),
    onSuccess: (rows) => {
      onWritten(rows);
      setSelected('');
      setReason('');
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => unexcludeClient(id),
    onSuccess: onWritten,
  });

  const excludedIds = new Set(excluded.map((e) => e.harvest_client_id));
  const available = clients.filter((c) => !excludedIds.has(c.harvest_id));

  if (isLoading) {
    return (
      <div className="px-6 max-w-3xl mx-auto">
        <p className="text-sm text-slate-500 animate-pulse">Loading exclusions…</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-6 max-w-3xl mx-auto">
        <p className="text-sm text-red-700">{(error as Error).message}</p>
      </div>
    );
  }

  return (
    <div className="px-6 pb-10 max-w-3xl mx-auto space-y-5">
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Excluded clients</h2>
          <p className="text-xs text-slate-600 mt-1 leading-relaxed">
            Harvest clients that are not clients. Their projects are hidden from the Projects
            tab and skipped by billing config reconciliation — one entry covers every project
            under that client, including ones created later.
          </p>
        </div>

        <div className="flex items-start gap-2 bg-amber-400/10 border border-amber-400/40 rounded-lg px-3 py-2.5">
          <Info className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-amber-800 leading-relaxed">
            This hides work from reporting; it does not stop it billing. Existing billing groups
            keep working and stay editable, so excluding a client you actually invoice would
            quietly remove it from the roster while its invoices continue. Nothing here touches
            Harvest.
          </p>
        </div>

        {excluded.length === 0 ? (
          <p className="text-sm text-slate-500 py-6 text-center">
            No clients excluded.
            <span className="block text-xs text-slate-400 mt-1">
              Every Harvest client currently counts as a client.
            </span>
          </p>
        ) : (
          <ul className="divide-y divide-slate-100 border-y border-slate-100">
            {excluded.map((e) => (
              <li key={e.harvest_client_id} className="py-2.5 flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-slate-900 font-medium">
                    {/* An exclusion outlives the snapshot row it names. */}
                    {e.client_name ?? (
                      <span className="text-slate-500 italic">
                        Unknown client #{e.harvest_client_id}
                      </span>
                    )}
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      {e.project_count} project{e.project_count === 1 ? '' : 's'} hidden
                    </span>
                  </p>
                  {e.reason && (
                    <p className="text-xs text-slate-600 mt-0.5">{e.reason}</p>
                  )}
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    {e.excluded_by} · {dateTime(e.excluded_at)}
                  </p>
                </div>
                <button
                  onClick={() => remove.mutate(e.harvest_client_id)}
                  disabled={remove.isPending}
                  title="Stop excluding this client"
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-40 transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="space-y-2 pt-1">
          <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
            Exclude a client
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="flex-1 px-3 py-2 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <option value="">Select a client…</option>
              {available.map((c) => (
                <option key={c.harvest_id} value={c.harvest_id}>
                  {c.name}
                </option>
              ))}
            </select>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              placeholder="Reason (optional) — e.g. this is us, not a client"
              className="flex-1 px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-500"
            />
            <button
              onClick={() => add.mutate()}
              disabled={!selected || add.isPending}
              className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-cyan-500/10 border border-cyan-500/50 text-cyan-700 hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {add.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Plus className="w-3.5 h-3.5" />
              )}
              Exclude
            </button>
          </div>
          {(add.error || remove.error) && (
            <p className="text-xs text-red-700">
              {((add.error ?? remove.error) as Error).message}
            </p>
          )}
        </div>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Takes effect immediately and is reversible — removing an exclusion brings the client's
        projects straight back. Every change is recorded in the audit log with who made it and
        why.
      </p>
    </div>
  );
}
