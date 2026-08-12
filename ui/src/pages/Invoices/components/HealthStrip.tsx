import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, RefreshCw, CheckCircle2, Loader2 } from 'lucide-react';
import { FlagRow } from './FlagChip';
import { getBillingHealth, refreshHarvestSnapshot } from '../../../api';
import { money, dateTime } from '../../../invoicing';

// Config health lives on the Billing Groups page rather than in its own tab:
// every problem it reports is fixed by editing a group, which is this page's job.
export default function HealthStrip() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: health, isLoading } = useQuery({
    queryKey: ['billing-health'],
    queryFn: () => getBillingHealth(true),
  });

  const refresh = useMutation({
    mutationFn: refreshHarvestSnapshot,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['billing-health'] });
      queryClient.invalidateQueries({ queryKey: ['billing-groups'] });
    },
  });

  if (isLoading || !health) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl px-4 py-2.5">
        <span className="text-xs text-slate-500 animate-pulse">Checking config health…</span>
      </div>
    );
  }

  const errors = health.counts?.error ?? 0;
  const warnings = health.counts?.warning ?? 0;
  const atRisk = health.unmapped_projects.filter((p) => p.uninvoiced_hours > 0);
  const atRiskValue = atRisk.reduce((s, p) => s + p.estimated_value, 0);
  const clean = errors === 0 && warnings === 0 && health.unmapped_projects.length === 0;

  return (
    <div className={`bg-white border rounded-xl overflow-hidden ${
      errors > 0 ? 'border-red-500/40' : warnings > 0 ? 'border-amber-400/40' : 'border-slate-200'
    }`}>
      <div className="flex items-center gap-3 px-4 py-2.5">
        <button onClick={() => setOpen(!open)} className="flex items-center gap-3 flex-1 text-left min-w-0">
          {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
          {clean ? (
            <span className="flex items-center gap-2 text-xs text-emerald-600">
              <CheckCircle2 className="w-3.5 h-3.5" />
              Config is clean — every billable project maps to exactly one active group.
            </span>
          ) : (
            <span className="flex items-center gap-3 text-xs flex-wrap">
              {health.unmapped_projects.length > 0 && (
                <span className={atRisk.length > 0 ? 'text-red-600 font-medium' : 'text-amber-600'}>
                  {health.unmapped_projects.length} unmapped project
                  {health.unmapped_projects.length === 1 ? '' : 's'}
                  {atRiskValue > 0 && ` · ${money(atRiskValue)} at risk`}
                </span>
              )}
              {errors > 0 && <span className="text-red-600">{errors} error{errors === 1 ? '' : 's'}</span>}
              {warnings > 0 && <span className="text-amber-600">{warnings} warning{warnings === 1 ? '' : 's'}</span>}
              <span className="text-slate-400">· reconciled {dateTime(health.snapshot.fetched_at)}</span>
            </span>
          )}
        </button>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 transition-colors flex-shrink-0"
        >
          {refresh.isPending
            ? <Loader2 className="w-3 h-3 animate-spin" />
            : <RefreshCw className="w-3 h-3" />}
          {refresh.isPending ? 'Refreshing…' : 'Refresh & validate'}
        </button>
      </div>

      {open && (
        <div className="border-t border-slate-200 px-4 py-4 space-y-5 bg-slate-50">
          <div>
            <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">Unmapped projects</p>
            <p className="text-xs text-slate-500 mt-1 mb-2 leading-relaxed max-w-2xl">
              Active billable Harvest projects belonging to no active billing group. This is the failure mode
              that silently loses revenue — assign each to a group, or to a{' '}
              <code className="text-slate-600">manual</code> group if it's invoiced by hand.
            </p>
            {health.unmapped_projects.length === 0 ? (
              <p className="text-xs text-emerald-600">Every billable project is mapped.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-slate-500 uppercase tracking-wide">
                      <th className="text-left py-2 font-medium">Project</th>
                      <th className="text-left py-2 font-medium">Client</th>
                      <th className="text-right py-2 font-medium">Uninvoiced hrs</th>
                      <th className="text-right py-2 font-medium">Est. value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {health.unmapped_projects.map((p) => (
                      <tr key={p.harvest_project_id} className="border-t border-slate-200">
                        <td className="py-2.5">
                          <span className="text-slate-800">{p.harvest_project_name}</span>
                          <span className="text-slate-400"> #{p.harvest_project_id}</span>
                          {!p.is_active && (
                            <span className="ml-2 text-[10px] uppercase tracking-wide text-amber-600/70">
                              archived
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 text-slate-600">{p.harvest_client_name}</td>
                        <td className={`py-2.5 text-right tabular-nums ${p.uninvoiced_hours > 0 ? 'text-amber-600' : 'text-slate-400'}`}>
                          {p.uninvoiced_hours}
                        </td>
                        <td className={`py-2.5 text-right tabular-nums ${p.estimated_value > 0 ? 'text-slate-800' : 'text-slate-400'}`}>
                          {money(p.estimated_value)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {health.flags.length > 0 && (
            <div className="space-y-2">
              <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
                Configuration flags
              </p>
              {health.flags.map((f, i) => <FlagRow key={`${f.code}-${i}`} flag={f} />)}
            </div>
          )}

          <p className="text-[11px] text-slate-400 leading-relaxed">
            Harvest snapshot: {health.snapshot.clients} clients, {health.snapshot.projects} projects,
            invoice item categories{' '}
            <span className="text-slate-500">
              {health.snapshot.invoice_item_categories.join(', ') || '—'}
            </span>{' '}
            — fetched {dateTime(health.snapshot.fetched_at)}. Categories are validated against every
            fixed-fee line item's <code>kind</code> at plan time, so an invalid category surfaces here
            rather than as a 422 mid-execution.
          </p>
        </div>
      )}
    </div>
  );
}
