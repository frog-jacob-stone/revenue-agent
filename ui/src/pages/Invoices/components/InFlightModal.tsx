import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertOctagon, Check, ExternalLink, Loader2, Undo2, X } from 'lucide-react';
import { Field } from './Bits';
import { resolveInFlight } from '../../../api';
import { money, shortDate } from '../../../invoicing';
import type { BillingRunSummary, RunItem } from '../../../invoicing';

interface Props {
  run: BillingRunSummary;
  item: RunItem;
  onClose: () => void;
}

const HARVEST_INVOICES_URL = 'https://frogslayer.harvestapp.com/invoices';

/**
 * In-flight resolution.
 *
 * A POST to Harvest never returned a verdict, so the system does not know whether
 * the invoice exists. It will not retry and it will not guess — PRD §8 escalates
 * this to a person, and this is that surface. Planning stays blocked for the
 * group (and the draw stays out of its queue) until one of the two answers below
 * is recorded.
 *
 * The invoice id is taken at face value rather than verified against the API:
 * second-guessing the human we just asked would not resolve anything, and a
 * wrong id is visible and correctable in a way a silent rejection is not.
 */
export default function InFlightModal({ run, item, onClose }: Props) {
  const queryClient = useQueryClient();
  const [invoiceId, setInvoiceId] = useState('');
  const [amount, setAmount] = useState('');

  const resolve = useMutation({
    mutationFn: (resolution: 'link' | 'failed') =>
      resolveInFlight(run.id, item.id, {
        resolution,
        ...(resolution === 'link'
          ? {
            harvest_invoice_id: Number(invoiceId),
            ...(amount ? { actual_amount: Number(amount) } : {}),
          }
          : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['billing-run', run.id] });
      queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
      queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
      queryClient.invalidateQueries({ queryKey: ['billing-in-flight'] });
      onClose();
    },
  });

  const canLink = /^\d+$/.test(invoiceId.trim());

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 backdrop-blur-sm p-6"
      onClick={onClose}
    >
      <div
        className="bg-white border border-red-500/40 rounded-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 px-5 py-4 border-b border-slate-200">
          <AlertOctagon className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <h2 className="text-sm font-semibold text-red-700">
              Unresolved in-flight row
            </h2>
            <p className="text-xs text-slate-600 mt-1 leading-relaxed">
              A POST to Harvest never returned, so the system does not know whether the invoice was
              created. It will never guess and it will never retry. Planning stays blocked for this
              group until you record what happened.
            </p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-700">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Field label="Billing group">{item.billing_group_name}</Field>
            <Field label="Run">{run.label}</Field>
            <Field label="Period">
              {item.period_start
                ? `${shortDate(item.period_start)} – ${shortDate(item.period_end)}`
                : 'none · a draw covers no month'}
            </Field>
            <Field label="Planned amount">{money(item.planned_amount)}</Field>
          </div>

          {item.error_message && (
            <p className="text-xs text-red-700/90 font-mono bg-slate-50 border border-slate-200 rounded p-2.5 leading-relaxed">
              {item.error_message}
            </p>
          )}

          <div className="bg-slate-50 border border-slate-200 rounded-lg p-4 space-y-3">
            <div>
              <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
                Step 1 — look in Harvest
              </p>
              <p className="text-xs text-slate-600 leading-relaxed mt-1">
                Search this client's invoices for one matching the planned amount, issued around
                the attempt. If it is there, the write landed and only our record is missing.
              </p>
              <a
                href={HARVEST_INVOICES_URL}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-white border border-slate-300 hover:bg-slate-100 text-slate-700 transition-colors"
              >
                Open Harvest invoices <ExternalLink className="w-3 h-3" />
              </a>
            </div>

            <div className="border-t border-slate-200 pt-3">
              <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
                Step 2 — record what you found
              </p>

              {resolve.error && (
                <p className="text-xs text-red-700 mt-2">
                  {(resolve.error as Error).message}
                </p>
              )}

              <div className="flex items-end gap-2 flex-wrap mt-2">
                <label className="text-[11px] text-slate-500">
                  <span className="block mb-1">Harvest invoice id</span>
                  <input
                    value={invoiceId}
                    onChange={(e) => setInvoiceId(e.target.value)}
                    placeholder="e.g. 41234567"
                    inputMode="numeric"
                    className="w-36 px-2 py-1.5 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  />
                </label>
                <label className="text-[11px] text-slate-500">
                  <span className="block mb-1">Amount on it (optional)</span>
                  <input
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    placeholder={String(item.planned_amount)}
                    inputMode="decimal"
                    className="w-32 px-2 py-1.5 text-xs border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-500"
                  />
                </label>

                <button
                  onClick={() => resolve.mutate('link')}
                  disabled={!canLink || resolve.isPending}
                  title={canLink ? 'Record that this invoice was created' : 'Enter the invoice id first'}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/10 border border-emerald-500/50 text-emerald-700 hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {resolve.isPending
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <Check className="w-3.5 h-3.5" />}
                  It exists — link it
                </button>

                <button
                  onClick={() => resolve.mutate('failed')}
                  disabled={resolve.isPending}
                  title="Record that no invoice was created; this becomes billable again"
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-white border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-40 transition-colors"
                >
                  <Undo2 className="w-3.5 h-3.5" />
                  Nothing was created
                </button>
              </div>

              <p className="text-[11px] text-slate-400 mt-2 leading-relaxed">
                Leaving the amount blank keeps the variance unknown rather than recording a match
                we cannot verify. A zero variance would read as a checked match.
              </p>
            </div>
          </div>

          <div className="flex items-center">
            <button
              onClick={onClose}
              className="ml-auto px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
