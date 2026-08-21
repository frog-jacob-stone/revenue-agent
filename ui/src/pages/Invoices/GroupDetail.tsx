import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Power, Check, Loader2, Pencil } from 'lucide-react';
import { BillingTypeChip, TimingChip, Field, DrawStateChip } from './components/Bits';
import { getBillingGroup, deactivateBillingGroup, setDrawRelease } from '../../api';
import {
  money, shortDate, dateTime, drawState, drawIsOverdue,
} from '../../invoicing';

export default function GroupDetail() {
  const { groupId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: group, isLoading, error } = useQuery({
    queryKey: ['billing-group', groupId],
    queryFn: () => getBillingGroup(groupId),
    enabled: !!groupId,
  });

  const release = useMutation({
    mutationFn: ({ id, released }: { id: string; released: boolean }) =>
      setDrawRelease(id, released),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['billing-group', groupId] });
      queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
    },
  });

  const deactivate = useMutation({
    mutationFn: () => deactivateBillingGroup(groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['billing-group', groupId] });
      queryClient.invalidateQueries({ queryKey: ['billing-groups'] });
      queryClient.invalidateQueries({ queryKey: ['billing-health'] });
    },
  });

  if (isLoading) {
    return <div className="p-6 max-w-5xl mx-auto text-sm text-slate-500 animate-pulse">Loading…</div>;
  }
  if (error || !group) {
    return (
      <div className="p-6 max-w-5xl mx-auto">
        <p className="text-slate-600 text-sm">
          {(error as Error)?.message ?? 'Billing group not found.'}
        </p>
      </div>
    );
  }

  const drawsForGroup = [...(group.schedule_items ?? [])]
    .sort((a, b) => a.sequence - b.sequence);

  return (
    <div className="p-6 space-y-5 max-w-5xl mx-auto">
      <button
        className="flex items-center gap-2 text-sm text-slate-600 hover:text-slate-800 transition-colors"
        onClick={() => navigate('/invoices/groups')}
      >
        <ArrowLeft className="w-4 h-4" />
        Back to Billing Groups
      </button>

      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-semibold text-slate-900">{group.name}</h1>
            <BillingTypeChip type={group.billing_type} />
            {group.billing_type !== 'manual' && <TimingChip timing={group.billing_timing} />}
            {!group.is_active && (
              <span className="text-[10px] uppercase tracking-wide text-slate-500 border border-slate-300 rounded px-1.5 py-0.5">
                inactive
              </span>
            )}
          </div>
          <p className="text-sm text-slate-600 mt-0.5">
            {group.harvest_client_name} <span className="text-slate-400">#{group.harvest_client_id}</span>
          </p>
        </div>
        {group.is_active && (
          <div className="flex items-center gap-2">
          <button
            onClick={() => navigate(`/invoices/groups/${group.id}/edit`)}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-600 hover:bg-cyan-500/25 transition-colors"
          >
            <Pencil className="w-3.5 h-3.5" />
            Edit
          </button>
          <button
            onClick={() => deactivate.mutate()}
            disabled={deactivate.isPending}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 transition-colors"
          >
            {deactivate.isPending
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : <Power className="w-3.5 h-3.5" />}
            Deactivate
          </button>
          </div>
        )}
      </div>

      {group.billing_type === 'manual' && (
        <div className="bg-white border border-slate-200 rounded-lg px-4 py-3">
          <p className="text-xs text-slate-600 leading-relaxed">
            <span className="text-slate-800 font-medium">Manual group.</span>{' '}
            Skipped entirely during planning — no payload, no estimate, no ledger row. Its only job is to
            suppress <code className="text-slate-500">UNMAPPED_PROJECT</code> for projects invoiced by hand.
          </p>
        </div>
      )}

      {group.billing_type === 'fixed_fee_schedule' && (
        <div className="bg-white border border-slate-200 rounded-lg px-4 py-3">
          <p className="text-xs text-slate-600 leading-relaxed">
            <span className="text-slate-800 font-medium">Draw-billed group.</span>{' '}
            Skipped by the monthly run on purpose — draws are drafted one at a time from the{' '}
            <Link to="/invoices/draws" className="text-cyan-600 hover:text-cyan-700">
              Draws
            </Link>{' '}
            tab, whenever delivery is confirmed.
          </p>
        </div>
      )}

      {/* Invoice config */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Invoice configuration</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Field label="Billing type">{group.billing_type}</Field>
          <Field label="Billing timing">
            {group.billing_type === 'manual' ? (
              '—'
            ) : group.billing_type === 'fixed_fee_schedule' ? (
              <span className="text-slate-500">
                n/a <span className="text-xs">· a draw covers no period</span>
              </span>
            ) : (
              group.billing_timing
            )}
          </Field>
          <Field label="Payment term">
            {group.payment_term}
            {group.payment_term === 'custom' && (
              <span className="text-slate-500 text-xs">
                {' '}· net {group.custom_net_days} (due date computed here, not by Harvest)
              </span>
            )}
          </Field>
          <Field label="Time summary type">{group.time_summary_type ?? '—'}</Field>
          <Field label="Expenses">
            {group.include_expenses ? `included · ${group.expense_summary_type}` : 'excluded'}
          </Field>
          <Field label="Attach receipts">{group.attach_receipts ? 'yes' : 'no'}</Field>
          <Field label="Purchase order">
            {group.purchase_order ?? '—'}
            {group.requires_purchase_order && !group.purchase_order && (
              <span className="text-amber-600 text-xs"> (required but empty)</span>
            )}
          </Field>
          <Field label="Currency">{group.currency ?? 'inherit from Harvest client'}</Field>
          <Field label="Active">{group.is_active ? 'yes' : 'no'}</Field>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
          <Field label="Subject template">
            <code className="text-xs text-slate-600">{group.subject_template}</code>
          </Field>
          <Field label="Notes template">
            <span className="text-xs text-slate-600">{group.notes_template ?? '—'}</span>
          </Field>
        </div>
      </div>

      {/* Projects */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
        <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
          Harvest projects ({group.projects.length})
        </h2>
        <div className="space-y-1.5">
          {group.projects.length === 0 ? (
            <p className="text-xs text-slate-400">No projects mapped to this group.</p>
          ) : group.projects.map((p) => (
            <div key={p.harvest_project_id} className="flex items-center gap-3 border border-slate-200 rounded-lg px-3 py-2">
              <span className="text-xs text-slate-400 w-6">{p.sort_order}</span>
              <span className="text-sm text-slate-800 flex-1">{p.harvest_project_name}</span>
              <span className="text-xs text-slate-400">#{p.harvest_project_id}</span>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-slate-400">
          Order controls the <code>project_ids</code> array sent to Harvest. Every project here must belong to
          client #{group.harvest_client_id} — a mismatch is a 422 at execution time, so it is rejected when
          the group is saved.
        </p>
      </div>

      {/* Payment schedule (draws) */}
      {drawsForGroup.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <div className="flex items-start gap-2">
            <div className="flex-1">
              <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                Payment schedule
              </h2>
              <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                Each draw bills as its own invoice, when you confirm delivery. The scheduled
                date is the contract's commitment — it prompts, it never bills.
              </p>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 uppercase tracking-wide">
                  <th className="text-left py-2 font-medium w-10">#</th>
                  <th className="text-left py-2 font-medium">Description</th>
                  <th className="text-left py-2 font-medium">Scheduled</th>
                  <th className="text-right py-2 font-medium">Amount</th>
                  <th className="text-left py-2 font-medium pl-4">State</th>
                  <th className="text-right py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {drawsForGroup.map((s) => {
                  const state = drawState(s);
                  const overdue = drawIsOverdue(s);
                  return (
                    <tr key={s.id} className="border-t border-slate-200">
                      <td className="py-2.5 text-slate-500">{s.sequence}</td>
                      <td className="py-2.5 text-slate-800">{s.description}</td>
                      <td className={`py-2.5 ${overdue ? 'text-amber-700 font-medium' : 'text-slate-600'}`}>
                        {shortDate(s.scheduled_date)}
                      </td>
                      <td className="py-2.5 text-right text-slate-800 tabular-nums">
                        {money(s.amount)}
                      </td>
                      <td className="py-2.5 pl-4">
                        <div className="flex items-center gap-2">
                          <DrawStateChip state={state} overdue={overdue} />
                          {state === 'invoiced' && s.invoiced_run_id && (
                            <Link
                              to={`/invoices/runs/${s.invoiced_run_id}`}
                              className="text-emerald-600 hover:text-emerald-700"
                            >
                              {s.harvest_invoice_number ? `#${s.harvest_invoice_number}` : 'view'}
                            </Link>
                          )}
                          {state === 'in_flight' && s.live_run_id && (
                            <Link
                              to={`/invoices/runs/${s.live_run_id}`}
                              className="text-cyan-600 hover:text-cyan-700"
                            >
                              review
                            </Link>
                          )}
                          {state === 'ready' && s.released_at && (
                            <span className="text-slate-400">
                              released {dateTime(s.released_at)}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="py-2.5 text-right">
                        {state === 'pending' && (
                          <button
                            onClick={() => release.mutate({ id: s.id, released: true })}
                            disabled={release.isPending}
                            className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium bg-emerald-500/10 border border-emerald-500/50 text-emerald-700 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
                          >
                            {release.isPending
                              ? <Loader2 className="w-3 h-3 animate-spin" />
                              : <Check className="w-3 h-3" />}
                            Confirm delivery
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-slate-400">
            Confirming delivery moves a draw to Ready to bill on the{' '}
            <Link to="/invoices/draws" className="text-cyan-600 hover:text-cyan-700">
              Draws
            </Link>{' '}
            tab, where you prepare its invoice.
          </p>
        </div>
      )}

      {/* Recurring line items */}
      {group.recurring_items && group.recurring_items.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Recurring line items</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 uppercase tracking-wide">
                  <th className="text-left py-2 font-medium">Description</th>
                  <th className="text-right py-2 font-medium">Qty</th>
                  <th className="text-right py-2 font-medium">Unit price</th>
                  <th className="text-left py-2 font-medium pl-4">Effective</th>
                </tr>
              </thead>
              <tbody>
                {group.recurring_items.map((r) => {
                  const expired = r.effective_to !== null;
                  return (
                    <tr key={r.id} className={`border-t border-slate-200 ${expired ? 'opacity-50' : ''}`}>
                      <td className="py-2 text-slate-800">
                        {r.description}
                        {r.is_placeholder && (
                          <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded bg-amber-400/20 text-amber-700 font-medium align-middle">
                            placeholder
                          </span>
                        )}
                      </td>
                      <td className="py-2 text-right text-slate-600 tabular-nums">{r.quantity}</td>
                      {/* A placeholder is stored at $0, and rendering that as a
                          price reads as a free line rather than an undecided
                          one — the exact confusion is_placeholder exists to
                          prevent. */}
                      <td className="py-2 text-right tabular-nums">
                        {r.is_placeholder
                          ? <span className="text-amber-700">set each month</span>
                          : <span className="text-slate-800">{money(r.unit_price)}</span>}
                      </td>
                      <td className="py-2 pl-4 text-slate-600">
                        {r.effective_from ? shortDate(r.effective_from) : 'always'} →{' '}
                        {r.effective_to ? shortDate(r.effective_to) : 'open'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-slate-400">
            <code>{'{period_label}'}</code> is rendered at plan time. Superseded rows are kept so a fee change
            doesn't erase history. A placeholder's amount is entered on the pre-flight each month —
            until it is, the invoice can't be approved.
          </p>
        </div>
      )}
    </div>
  );
}
