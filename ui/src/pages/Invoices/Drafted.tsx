import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, FileText, AlertOctagon } from 'lucide-react';
import { StatTile } from './components/Bits';
import {
  getCreatedInvoices, getCreatedInvoiceTotals, getBillingHealth,
} from '../../api';
import {
  money, shortDate, dateTime, harvestInvoiceUrl,
} from '../../invoicing';
import type { CreatedInvoice } from '../../invoicing';

type KindFilter = 'all' | 'draw' | 'monthly';
type StatusFilter = 'created' | 'failed';

const KINDS: { value: KindFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'draw', label: 'Draws' },
  { value: 'monthly', label: 'Monthly runs' },
];

/** What the row is *for*. A draw is identified by its milestone, a monthly
 *  invoice by the period it covers — they are not the same question. */
function What({ row }: { row: CreatedInvoice }) {
  return (
    <>
      <span className="text-slate-800">
        {row.draw_description ?? row.billing_group_name ?? '—'}
      </span>
      <span className="block text-[11px] text-slate-400">
        {row.kind === 'draw' ? (
          <>
            {row.billing_group_name}
            {row.draw_sequence != null && ` · draw ${row.draw_sequence}`}
          </>
        ) : row.period_start ? (
          <>
            {shortDate(row.period_start)} – {shortDate(row.period_end)}
          </>
        ) : (
          row.billing_type ?? ''
        )}
      </span>
    </>
  );
}

/**
 * Drafted — every invoice this system created in Harvest.
 *
 * Named for what actually happened. This system creates *drafts* and stops; the
 * invoice is billed when a human sends it from Harvest, which is an event this
 * system never sees. Calling the tab "Billed" claimed a state we do not observe.
 *
 * Its own tab rather than a section on Draws because the ledger records both
 * kinds: a draw drafted the day delivery was confirmed, and a monthly run's
 * invoices. Asking "what have we drafted?" is not a question about runs.
 *
 * This reads the ledger, which is the record — it survives a reload, a different
 * browser, and next month. Nothing here is re-read from Harvest: every figure is
 * what Harvest returned at creation, so a draft edited afterwards (overages,
 * added lines) will not match what the client was finally sent.
 */
export default function Drafted() {
  // `?kind=draw` so the link from the Draws tab lands pre-filtered on the thing
  // that was just drafted, rather than on everything.
  const [params] = useSearchParams();
  const initialKind = params.get('kind');
  const [kind, setKind] = useState<KindFilter>(
    initialKind === 'draw' || initialKind === 'monthly' ? initialKind : 'all',
  );
  const [status, setStatus] = useState<StatusFilter>('created');

  const { data: rows = [], isLoading, error } = useQuery({
    queryKey: ['billing-invoices', kind, status],
    queryFn: () => getCreatedInvoices({
      status,
      ...(kind === 'all' ? {} : { kind }),
    }),
  });

  // Totals come from the server rather than the page, so they describe
  // everything drafted rather than whatever the current filter fetched.
  const { data: totals } = useQuery({
    queryKey: ['billing-invoice-totals'],
    queryFn: () => getCreatedInvoiceTotals(),
  });

  const { data: health } = useQuery({
    queryKey: ['billing-health', 'structural'],
    queryFn: () => getBillingHealth(false),
    staleTime: 5 * 60 * 1000,
  });
  const baseUri = health?.snapshot.harvest_base_uri ?? '';

  if (isLoading) {
    return <p className="text-sm text-slate-500 animate-pulse">Loading invoices…</p>;
  }
  if (error) {
    return <p className="text-sm text-red-700">{(error as Error).message}</p>;
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile
          label="Invoices created"
          value={totals?.count ?? 0}
          sub={money(totals?.total_amount ?? 0)}
          tone={(totals?.count ?? 0) > 0 ? 'good' : 'default'}
        />
        <StatTile label="From draws" value={totals?.draw_count ?? 0} />
        <StatTile
          label="From monthly runs"
          value={totals?.monthly_count ?? 0}
          sub={(totals?.monthly_count ?? 0) === 0 ? 'execution not built yet' : undefined}
        />
        <StatTile
          label="Amount unconfirmed"
          value={totals?.unverified_count ?? 0}
          sub={
            (totals?.unverified_count ?? 0) > 0
              ? 'linked by hand, counted at planned'
              : 'all amounts from Harvest'
          }
          tone={(totals?.unverified_count ?? 0) > 0 ? 'warn' : 'default'}
        />
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-1">
          {KINDS.map(({ value, label }) => (
            <button
              key={value}
              onClick={() => setKind(value)}
              className={`px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                kind === value
                  ? 'bg-slate-900 text-white'
                  : 'text-slate-600 hover:bg-slate-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Failed attempts are history worth seeing, but they are not billing —
            nothing exists in Harvest for them, so they are off by default. */}
        <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
          <input
            type="checkbox"
            checked={status === 'failed'}
            onChange={(e) => setStatus(e.target.checked ? 'failed' : 'created')}
            className="w-3.5 h-3.5 rounded accent-cyan-600"
          />
          Show failed attempts instead
        </label>
      </div>

      {status === 'failed' && (
        <div className="flex items-start gap-2 bg-amber-400/10 border border-amber-400/40 rounded-lg px-4 py-2.5">
          <AlertOctagon className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-amber-800">
            These attempts were <span className="font-medium">refused by Harvest</span>. No invoice
            exists for any of them, and the draw or group is billable again.
          </p>
        </div>
      )}

      {rows.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl px-4 py-12 text-center">
          <p className="text-sm text-slate-600">
            {status === 'failed'
              ? 'No failed attempts. Good.'
              : 'Nothing has been drafted yet.'}
          </p>
          {status === 'created' && (
            <p className="text-xs text-slate-400 mt-1">
              Confirm delivery on a draw and create its draft — it appears here with its Harvest
              invoice number.
            </p>
          )}
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
                <th className="text-left px-4 py-3 font-medium">Invoice</th>
                <th className="text-left px-4 py-3 font-medium">Client</th>
                <th className="text-left px-4 py-3 font-medium">For</th>
                <th className="text-left px-4 py-3 font-medium">Issued / due</th>
                <th className="text-right px-4 py-3 font-medium">Amount</th>
                <th className="text-right px-4 py-3 font-medium">Variance</th>
                <th className="text-right px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const url = harvestInvoiceUrl(baseUri, row.harvest_invoice_id);
                return (
                  <tr
                    key={row.billing_run_item_id}
                    className="border-b border-slate-100 last:border-0 hover:bg-slate-50"
                  >
                    <td className="px-4 py-3">
                      <span className="text-slate-900 font-medium tabular-nums">
                        {row.harvest_invoice_number || '—'}
                      </span>
                      <span className="block text-[11px] text-slate-400">
                        {row.kind === 'draw' ? 'draw' : 'monthly run'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-700">
                      {row.harvest_client_name ?? '—'}
                    </td>
                    <td className="px-4 py-3"><What row={row} /></td>
                    <td className="px-4 py-3 text-slate-600 text-xs whitespace-nowrap">
                      {shortDate(row.issue_date)} → {shortDate(row.due_date)}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-900">
                      {money(row.actual_amount ?? row.planned_amount)}
                      {row.actual_amount == null && (
                        <span
                          className="block text-[11px] text-amber-700"
                          title="Linked by hand without recording the amount, so this is the planned figure."
                        >
                          planned
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums text-xs">
                      {row.variance == null ? (
                        <span className="text-slate-400">—</span>
                      ) : row.variance === 0 ? (
                        <span className="text-slate-400">0</span>
                      ) : (
                        <span className="text-amber-700">
                          {row.variance > 0 ? '+' : ''}{money(row.variance)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right text-xs text-slate-500 whitespace-nowrap">
                      {dateTime(row.created_at)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 justify-end">
                        <Link
                          to={`/invoices/runs/${row.billing_run_id}`}
                          title="The run record for this invoice"
                          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors"
                        >
                          <FileText className="w-3 h-3" />
                          Record
                        </Link>
                        {/* Omitted when HARVEST_BASE_URI is unset — a guessed
                            subdomain would link to a 404. */}
                        {url && (
                          <a
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1 px-2 py-1 rounded text-[11px] font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors"
                          >
                            Harvest <ExternalLink className="w-3 h-3" />
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {rows.length > 0 && !baseUri && (
        <p className="text-[11px] text-amber-700">
          Set <code>HARVEST_BASE_URI</code> to link these straight through to Harvest. No API
          exposes your account's web address, so it has to be configured.
        </p>
      )}

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Every figure here is what Harvest returned when the invoice was created. Drafts are freely
        edited in Harvest before sending, so an amount changed there — a retainer overage, an added
        line — will not be reflected. This is the amount created, not the amount sent.
      </p>
    </div>
  );
}
