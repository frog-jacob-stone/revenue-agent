import { Fragment, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { StatTile } from '../Invoices/components/Bits';
import { RevenueTypeChip } from './components/Bits';
import EmptyState from '../../components/shared/EmptyState';
import { money, dateTime } from '../../invoicing';
import {
  getRevenueConfig, setRevenueConfig, removeRevenueConfig, num,
} from '../../api';
import type { RevenueProjectConfig, RevenueType } from '../../api';

/**
 * Setup — how each project recognizes revenue.
 *
 * Replaces the three columns that were typed by hand into the Airtable Projects
 * table. Scope is billable, active, and not an excluded client's — the same
 * three conditions the run applies, from the same SQL fragment, so this screen
 * cannot say "all configured" while a run refuses to start.
 *
 * Unconfigured projects sort to the top, because they are the work: a run is
 * blocked until every one of them has a row.
 */

const TYPES: { value: RevenueType; label: string; hint: string }[] = [
  { value: 'fixed_fee', label: 'Fixed Fee', hint: 'Percent complete × contract value' },
  { value: 'time_and_materials', label: 'T&M', hint: 'Recognized as invoiced' },
  { value: 'msf', label: 'MSF', hint: 'Recognized as invoiced' },
  { value: 'hosting', label: 'Hosting', hint: 'Recognized as invoiced' },
  { value: 'retainer', label: 'Retainer', hint: 'Entered by hand each month' },
];

function EditRow({ row, onDone }: { row: RevenueProjectConfig; onDone: () => void }) {
  const [type, setType] = useState<RevenueType>(row.revenue_type ?? 'time_and_materials');
  const [fees, setFees] = useState(row.contracted_fees ?? '');
  const [notes, setNotes] = useState(row.notes ?? '');

  const save = useMutation({
    mutationFn: () =>
      setRevenueConfig(row.harvest_project_id, {
        revenue_type: type,
        // Only fixed fee has a denominator to be a percentage of; sending a
        // value for the others would store a number nothing reads.
        contracted_fees: type === 'fixed_fee' ? fees || null : null,
        notes: notes || null,
      }),
    onSuccess: onDone,
  });

  const needsFees = type === 'fixed_fee';
  const hint = TYPES.find((t) => t.value === type)?.hint;

  return (
    <tr className="bg-cyan-500/5 border-b border-slate-100">
      <td colSpan={5} className="px-4 py-3">
        <div className="flex items-end gap-3 flex-wrap">
          <label className="text-xs text-slate-600">
            <span className="block mb-1 font-medium">Recognition</span>
            <select
              value={type}
              onChange={(e) => setType(e.target.value as RevenueType)}
              className="bg-white border border-slate-300 rounded px-2 py-1.5 text-sm"
            >
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </label>
          {needsFees && (
            <label className="text-xs text-slate-600">
              <span className="block mb-1 font-medium">
                Contracted fees <span className="text-slate-400">— required</span>
              </span>
              <input
                value={fees}
                onChange={(e) => setFees(e.target.value)}
                inputMode="decimal"
                placeholder="250000.00"
                className="w-36 bg-white border border-slate-300 rounded px-2 py-1.5 text-sm tabular-nums"
              />
            </label>
          )}
          <label className="text-xs text-slate-600 flex-1 min-w-[14rem]">
            <span className="block mb-1 font-medium">Notes</span>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Why this project recognizes the way it does"
              className="w-full bg-white border border-slate-300 rounded px-2 py-1.5 text-sm"
            />
          </label>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || (needsFees && !fees.trim())}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-700 hover:bg-cyan-500/25 disabled:opacity-40"
          >
            <Check className="w-3.5 h-3.5" />
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={onDone}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100"
          >
            <X className="w-3.5 h-3.5" /> Cancel
          </button>
        </div>
        <p className="text-[11px] text-slate-500 mt-2">{hint}</p>
        {save.error && (
          <p className="text-xs text-red-700 mt-2">{(save.error as Error).message}</p>
        )}
      </td>
    </tr>
  );
}

export default function Setup() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<number | null>(null);

  const { data: rows = [], isLoading, error } = useQuery({
    queryKey: ['revenue-config'],
    queryFn: getRevenueConfig,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['revenue-config'] });
  const remove = useMutation({ mutationFn: removeRevenueConfig, onSuccess: invalidate });

  const unconfigured = rows.filter((r) => r.revenue_type == null);

  if (error) {
    return (
      <p className="text-sm text-red-700 bg-red-500/5 border border-red-500/30 rounded-lg px-4 py-3">
        {(error as Error).message}
      </p>
    );
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <StatTile label="In scope" value={rows.length} sub="billable, active, not excluded" />
        <StatTile
          label="Needs configuration"
          value={unconfigured.length}
          tone={unconfigured.length > 0 ? 'warn' : 'default'}
          sub={unconfigured.length > 0 ? 'a run is blocked until these are set' : 'all set'}
        />
        <StatTile
          label="Fixed fee"
          value={rows.filter((r) => r.revenue_type === 'fixed_fee').length}
          sub="the only type with a contract value"
        />
      </div>

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Project</th>
              <th className="text-left px-4 py-3 font-medium">Recognition</th>
              <th className="text-right px-4 py-3 font-medium">Contracted fees</th>
              <th className="text-left px-4 py-3 font-medium">Last changed</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-xs text-slate-500 animate-pulse">
                  Loading projects…
                </td>
              </tr>
            )}
            {!isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-0 py-0">
                  <EmptyState
                    title="No projects in scope"
                    description="Projects appear here after a Harvest snapshot refresh — billable, active, and not belonging to an excluded client."
                  />
                </td>
              </tr>
            )}
            {rows.map((r) => (
              // Keyed on the Fragment: the row and its edit panel are two
              // siblings for one project, so the key belongs on the pair.
              <Fragment key={r.harvest_project_id}>
                <tr
                  className={`border-b border-slate-100 last:border-0 align-top ${
                    r.revenue_type == null ? 'bg-amber-400/5' : 'hover:bg-slate-50'
                  }`}
                >
                  <td className="px-4 py-2.5">
                    <span className="text-slate-900 font-medium whitespace-nowrap">
                      {r.harvest_project_name}
                    </span>
                    <span className="block text-[11px] text-slate-400">
                      {r.client_name ?? `Harvest ${r.harvest_project_id}`}
                    </span>
                    {r.notes && (
                      <span className="block text-[11px] text-slate-500 mt-0.5 max-w-sm">
                        {r.notes}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    {r.revenue_type
                      ? <RevenueTypeChip type={r.revenue_type} />
                      : <span className="text-[11px] text-amber-700 font-medium">Not configured</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-slate-600 whitespace-nowrap">
                    {r.contracted_fees == null
                      ? <span className="text-slate-300">—</span>
                      : money(num(r.contracted_fees))}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-500 whitespace-nowrap">
                    {r.updated_at
                      ? <>{dateTime(r.updated_at)}<span className="block text-slate-400">{r.updated_by}</span></>
                      : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right whitespace-nowrap">
                    {editing !== r.harvest_project_id && (
                      <div className="flex items-center gap-1.5 justify-end">
                        <button
                          onClick={() => setEditing(r.harvest_project_id)}
                          className="px-2 py-1 rounded text-[11px] font-medium border border-slate-300 text-slate-600 hover:bg-slate-100"
                        >
                          {r.revenue_type ? 'Edit' : 'Configure'}
                        </button>
                        {r.revenue_type && (
                          <button
                            onClick={() => remove.mutate(r.harvest_project_id)}
                            disabled={remove.isPending}
                            title="Past entries are untouched — this only stops future runs including it."
                            className="px-2 py-1 rounded text-[11px] font-medium border border-slate-300 text-slate-500 hover:bg-slate-100 disabled:opacity-40"
                          >
                            Clear
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
                {editing === r.harvest_project_id && (
                  <EditRow row={r} onDone={() => { setEditing(null); invalidate(); }} />
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {remove.error && (
        <p className="text-xs text-red-700">{(remove.error as Error).message}</p>
      )}

      <p className="text-[11px] text-slate-400 leading-relaxed">
        <span className="font-medium">Recognition</span> is how revenue is earned, which is a
        different question from how a client is <em>invoiced</em> — that lives on the billing group.
        A project can be invoiced on a draw schedule and recognized percent-complete. Clearing a
        project's configuration leaves every month it already recognized untouched; it only stops
        future runs from including it, which they will then refuse to start over.
      </p>
    </div>
  );
}
