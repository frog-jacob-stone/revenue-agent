import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Check, AlertTriangle, ChevronRight, ChevronDown, Loader2, Undo2, AlertOctagon,
  CalendarClock, ExternalLink, FileText,
} from 'lucide-react';
import { StatTile, DrawStateChip, Field } from './components/Bits';
import { FlagRow } from './components/FlagChip';
import {
  ApiError, getDraws, setDrawRelease, getDrawPreview, invoiceDraw,
  getInFlightItems, resolveInFlight, getBillingHealth,
} from '../../api';
import {
  money, shortDate, dateTime, drawState, drawIsOverdue, harvestInvoiceUrl,
} from '../../invoicing';
import type {
  Draw, DrawInvoiceResult, InFlightItem, UnknownWriteDetail,
} from '../../invoicing';

/** A 502 from the write means the outcome is unknown, not that it failed. */
function unknownOutcome(err: unknown): UnknownWriteDetail | null {
  if (err instanceof ApiError && err.status === 502) {
    const d = err.detail;
    if (d && typeof d === 'object' && 'remedy' in d) return d as UnknownWriteDetail;
  }
  return null;
}

function byClient(draws: Draw[]): [string, Draw[]][] {
  const groups = new Map<string, Draw[]>();
  for (const d of draws) {
    const key = d.harvest_client_name ?? 'Unknown client';
    groups.set(key, [...(groups.get(key) ?? []), d]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/**
 * A draw that is ready to bill, reviewable in place.
 *
 * The invoice is computed on expand and thrown away on collapse — there is no
 * "prepare" step and nothing is written until the Harvest draft is created.
 * That keeps the only two states that exist honest: this draw is billable, or
 * it has been drafted. Nothing in between.
 */
function ReadyCard({
  draw, busy, onWithdraw, onCreate, creating, createError,
}: {
  draw: Draw;
  busy: boolean;
  onWithdraw: () => void;
  onCreate: () => void;
  creating: boolean;
  createError: Error | null;
}) {
  const [open, setOpen] = useState(false);

  // Deferred until the row is opened: pricing every ready draw up front would
  // cost a query each to show something nobody asked to see.
  //
  // Never served from cache. The issue and due dates are computed from *today*,
  // so a preview held from yesterday shows dates the create will not use — and a
  // stale due date is the one number on this card a client would notice. The
  // preview is a pure server-side computation, so refetching is nearly free.
  const { data: preview, isLoading, error } = useQuery({
    queryKey: ['draw-preview', draw.id],
    queryFn: () => getDrawPreview(draw.id),
    enabled: open,
    staleTime: 0,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
  });

  const blocking = preview?.flags.filter((f) => f.severity === 'error') ?? [];

  return (
    <div className={`bg-white border-y border-r rounded-xl overflow-hidden transition-colors border-l-4 ${
      blocking.length > 0
        ? 'border-red-500/40 border-l-red-500'
        : 'border-slate-200 border-l-emerald-500'
    }`}>
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          className="flex-1 flex items-center gap-3 text-left min-w-0"
          onClick={() => setOpen(!open)}
        >
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-slate-900">{draw.description}</span>
              <DrawStateChip state="ready" />
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              {draw.harvest_project_name} · scheduled {shortDate(draw.scheduled_date)} ·
              delivery confirmed {dateTime(draw.released_at)}
              {draw.released_by && ` by ${draw.released_by}`}
            </p>
          </div>

          <div className="text-right flex-shrink-0">
            <p className="text-sm font-semibold text-slate-900 tabular-nums">{money(draw.amount)}</p>
            <p className="text-[11px] text-slate-400">{draw.kind}</p>
          </div>

          {open
            ? <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
            : <ChevronRight className="w-4 h-4 text-slate-400 flex-shrink-0" />}
        </button>

        <button
          onClick={onWithdraw}
          disabled={busy}
          title="Withdraw delivery confirmation — moves this back to awaiting delivery"
          className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-medium text-slate-500 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-40 transition-colors flex-shrink-0"
        >
          <Undo2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {open && (
        <div className="border-t border-slate-200 px-4 py-4 space-y-5 bg-slate-50">
          {isLoading && (
            <p className="text-xs text-slate-500 animate-pulse">Building the invoice…</p>
          )}
          {error && (
            <p className="text-xs text-red-700">{(error as Error).message}</p>
          )}

          {preview && (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Field label="Harvest client">{preview.harvest_client_name ?? '—'}</Field>
                <Field label="Issue / due if created now">
                  {shortDate(preview.issue_date)} → {shortDate(preview.due_date)}
                  <span className="block text-[11px] text-slate-400 mt-0.5">
                    dated the day you create it, not when you opened this
                  </span>
                </Field>
                <Field label="Payment term">{preview.payment_term ?? '—'}</Field>
                <Field label="Subject">{preview.subject}</Field>
                <Field label="Service period">
                  <span className="text-slate-500">
                    none <span className="text-xs">· a draw covers no month</span>
                  </span>
                </Field>
                <Field label="Config">
                  <Link
                    to={`/invoices/groups/${preview.billing_group_id}`}
                    className="text-cyan-600 hover:text-cyan-700 text-xs"
                  >
                    {preview.billing_group_name}
                  </Link>
                </Field>
              </div>

              <div>
                <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium mb-2">
                  Line items
                </p>
                <div className="border border-slate-200 rounded-lg overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="bg-white text-slate-500 uppercase tracking-wide">
                        <th className="text-left px-3 py-2 font-medium">Description</th>
                        <th className="text-right px-3 py-2 font-medium">Qty</th>
                        <th className="text-right px-3 py-2 font-medium">Rate</th>
                        <th className="text-right px-3 py-2 font-medium">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preview.estimated_line_items.map((li, i) => (
                        <tr key={i} className="border-t border-slate-200">
                          <td className="px-3 py-2">
                            <span className="text-slate-800">{li.label}</span>
                            {li.detail && (
                              <span className="text-slate-400 block text-[11px]">{li.detail}</span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-right text-slate-600 tabular-nums">
                            {li.quantity} {li.unit}
                          </td>
                          <td className="px-3 py-2 text-right text-slate-600 tabular-nums">
                            {money(li.unit_price)}
                          </td>
                          <td className="px-3 py-2 text-right text-slate-800 tabular-nums">
                            {money(li.amount)}
                          </td>
                        </tr>
                      ))}
                      <tr className="border-t border-slate-300 bg-white">
                        <td className="px-3 py-2 text-slate-600 font-medium" colSpan={3}>Total</td>
                        <td className="px-3 py-2 text-right text-slate-900 font-semibold tabular-nums">
                          {money(preview.amount)}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="text-[11px] text-slate-400 mt-1.5">
                  A draw always bills its scheduled amount. To change it, edit the schedule on the
                  billing group.
                </p>
              </div>

              {preview.notes && (
                <Field label="Notes">
                  <span className="text-xs text-slate-600">{preview.notes}</span>
                </Field>
              )}

              {preview.flags.length > 0 && (
                <div className="space-y-2">
                  <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
                    Flags
                  </p>
                  {preview.flags.map((f, i) => <FlagRow key={`${f.code}-${i}`} flag={f} />)}
                </div>
              )}

              <details>
                <summary className="text-[11px] text-slate-500 uppercase tracking-wide font-medium cursor-pointer hover:text-slate-600">
                  Payload — exact POST body
                </summary>
                <pre className="mt-2 text-xs text-emerald-700 bg-slate-50 border border-slate-200 rounded-lg p-3 overflow-x-auto font-mono leading-relaxed">
                  {JSON.stringify(preview.planned_payload, null, 2)}
                </pre>
              </details>

              {createError && !unknownOutcome(createError) && (
                <div className="bg-red-500/10 border border-red-500/40 rounded-lg px-3 py-2">
                  <p className="text-xs text-red-700">
                    <span className="font-medium">Not created.</span> {createError.message}
                  </p>
                  <p className="text-[11px] text-slate-500 mt-0.5">
                    Nothing was written to Harvest. Fix the cause and try again.
                  </p>
                </div>
              )}

              {/* No success banner here on purpose. A created draw becomes
                  `invoiced`, leaves the ready list, and unmounts this card —
                  which is precisely how the first live invoice was created with
                  no visible confirmation. The confirmation lives on the page,
                  and the record lives in Drafted below. */}

              <div className="flex items-center gap-3 pt-1 border-t border-slate-200">
                {blocking.length > 0 && (
                  <p className="flex items-center gap-1.5 text-xs text-red-700">
                    <AlertOctagon className="w-3.5 h-3.5" />
                    Resolve the error flag before creating this invoice
                  </p>
                )}
                {blocking.length === 0 && (
                  <p className="text-[11px] text-slate-500">
                    Creates a <span className="font-medium">draft</span> in Harvest. Nothing is
                    sent to the client — you send it from Harvest.
                  </p>
                )}
                <button
                  onClick={onCreate}
                  disabled={busy || creating || blocking.length > 0 || !preview.billable}
                  title={
                    blocking.length > 0
                      ? 'Resolve the error flag first'
                      : 'Create the Harvest draft invoice for this draw'
                  }
                  className="ml-auto mt-3 flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/10 border border-emerald-500/50 text-emerald-700 hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {creating
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : <FileText className="w-3.5 h-3.5" />}
                  {creating ? 'Creating…' : 'Create draft in Harvest'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Resolving one in-flight row.
 *
 * A POST never returned, so an invoice may or may not exist in Harvest. The
 * system will not retry and will not guess — PRD §8 sends this to a person. Two
 * answers, and both are statements about the outside world rather than requests:
 * link the invoice that was created, or record that none was.
 *
 * The invoice id is taken at face value. Verifying it against the API would be
 * second-guessing the human we just asked, and a wrong id is visible and
 * correctable in a way that a silently rejected one is not.
 */
function InFlightRow({ item, baseUri }: { item: InFlightItem; baseUri: string }) {
  const queryClient = useQueryClient();
  const [invoiceId, setInvoiceId] = useState('');
  const [amount, setAmount] = useState('');

  const resolve = useMutation({
    mutationFn: (resolution: 'link' | 'failed') =>
      resolveInFlight(item.billing_run_id, item.billing_run_item_id, {
        resolution,
        ...(resolution === 'link'
          ? {
            harvest_invoice_id: Number(invoiceId),
            ...(amount ? { actual_amount: Number(amount) } : {}),
          }
          : {}),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
      queryClient.invalidateQueries({ queryKey: ['billing-in-flight'] });
      queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
    },
  });

  const canLink = /^\d+$/.test(invoiceId.trim());

  return (
    <div className="border border-red-500/30 rounded-lg bg-white px-3 py-3 space-y-3">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-sm text-slate-900 font-medium">
          {item.draw_description ?? item.billing_group_name}
        </span>
        <span className="text-xs text-slate-500">
          {item.harvest_client_name} · planned {money(item.planned_amount)} · attempted{' '}
          {dateTime(item.created_at)}
        </span>
      </div>

      <p className="text-xs text-slate-600 leading-relaxed">
        Check Harvest for an invoice matching this client and amount around that time.
        {harvestInvoiceUrl(baseUri) && (
          <>
            {' '}
            <a
              href={harvestInvoiceUrl(baseUri)!}
              target="_blank"
              rel="noreferrer"
              className="text-cyan-600 hover:text-cyan-700 inline-flex items-center gap-0.5"
            >
              Open Harvest invoices <ExternalLink className="w-3 h-3" />
            </a>
          </>
        )}
      </p>

      {resolve.error && (
        <p className="text-xs text-red-700">{(resolve.error as Error).message}</p>
      )}

      <div className="flex items-end gap-2 flex-wrap">
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
          title="Record that no invoice was created; this draw becomes billable again"
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-40 transition-colors"
        >
          <Undo2 className="w-3.5 h-3.5" />
          Nothing was created
        </button>
      </div>

      <p className="text-[11px] text-slate-400">
        Leaving the amount blank keeps the variance unknown rather than recording a match we
        cannot verify.
      </p>
    </div>
  );
}

function PendingRow({
  draw, busy, onConfirm,
}: {
  draw: Draw;
  busy: boolean;
  onConfirm: () => void;
}) {
  const overdue = drawIsOverdue(draw);
  return (
    <div className={`flex items-center gap-3 border rounded-xl px-4 py-3 ${
      overdue ? 'border-amber-400/40 bg-amber-400/5' : 'border-slate-200 bg-white'
    }`}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm text-slate-800">{draw.description}</span>
          <DrawStateChip state="pending" overdue={overdue} />
        </div>
        <p className="text-xs text-slate-500 mt-0.5">
          {draw.harvest_client_name} · {draw.harvest_project_name} ·{' '}
          <span className={overdue ? 'text-amber-700 font-medium' : undefined}>
            scheduled {shortDate(draw.scheduled_date)}
          </span>
        </p>
      </div>

      <p className="text-sm text-slate-600 tabular-nums flex-shrink-0">{money(draw.amount)}</p>

      {/* A slipped date is the other half of this row's job: either the work
          landed, or the schedule was wrong. Both answers live one click away. */}
      <Link
        to={`/invoices/groups/${draw.billing_group_id}/edit`}
        title={`Edit the payment schedule on ${draw.billing_group_name}`}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors flex-shrink-0"
      >
        <CalendarClock className="w-3.5 h-3.5" />
        Re-schedule
      </Link>

      <button
        onClick={onConfirm}
        disabled={busy}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/10 border border-emerald-500/50 text-emerald-700 hover:bg-emerald-500/20 disabled:opacity-50 transition-colors flex-shrink-0"
      >
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        Confirm delivery
      </button>
    </div>
  );
}

/**
 * The draw queue.
 *
 * Draws are event-driven — a milestone is accepted on the 12th and drafted on
 * the 12th — so they never ride the monthly run. This screen is always open:
 * confirm delivery when work is accepted, expand to check the invoice, then
 * create the draft.
 */
export default function Draws() {
  const queryClient = useQueryClient();

  const { data: draws = [], isLoading, error } = useQuery({
    queryKey: ['billing-draws'],
    queryFn: () => getDraws(),
  });

  // Sourced from the ledger rather than derived from the draws list: the monthly
  // run will produce in-flight rows too, and this panel is where they surface.
  const { data: inFlightItems = [] } = useQuery({
    queryKey: ['billing-in-flight'],
    queryFn: getInFlightItems,
  });

  // Only for the Harvest base URI, which lives on the health payload because no
  // Harvest endpoint exposes it.
  //
  // Deliberately NOT the health strip's `['billing-health']` key. That one is
  // fetched with include_time=true; caching a time-less report under it would
  // make the strip on the Groups page briefly report a clean bill of health that
  // is not true. `include_time=false` issues no Harvest requests, so the separate
  // fetch is cheap.
  const { data: health } = useQuery({
    queryKey: ['billing-health', 'structural'],
    queryFn: () => getBillingHealth(false),
    staleTime: 5 * 60 * 1000,
  });
  const baseUri = health?.snapshot.harvest_base_uri ?? '';

  const release = useMutation({
    mutationFn: ({ id, released }: { id: string; released: boolean }) =>
      setDrawRelease(id, released),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
      // A draw's release state changes what its group page shows, and the
      // monthly run's DRAW_OVERDUE flags are computed from the same rows.
      queryClient.invalidateQueries({ queryKey: ['billing-group'] });
    },
  });

  // Keyed by draw id so a result or error stays attached to the row it belongs
  // to — several draws can be drafted in one sitting, and a message under the
  // wrong one is worse than none.
  const [results, setResults] = useState<Record<string, DrawInvoiceResult>>({});
  const [errors, setErrors] = useState<Record<string, Error>>({});
  const [creatingId, setCreatingId] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (id: string) => invoiceDraw(id),
    onMutate: (id) => {
      setCreatingId(id);
      setErrors((prev) => {
        const { [id]: _drop, ...rest } = prev;
        return rest;
      });
    },
    onSuccess: (result, id) => {
      setResults((prev) => ({ ...prev, [id]: result }));
      queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
      queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
      queryClient.invalidateQueries({ queryKey: ['billing-group'] });
    },
    onError: (err: Error, id) => {
      setErrors((prev) => ({ ...prev, [id]: err }));
      // An unknown outcome moves the draw to in_flight, and a 4xx frees it —
      // either way the queue on screen is now stale.
      queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
      queryClient.invalidateQueries({ queryKey: ['billing-in-flight'] });
    },
    onSettled: () => setCreatingId(null),
  });

  const busy = release.isPending;
  const failure = release.error as Error | null;
  // Shown page-level rather than in the card: the card it belongs to is about to
  // leave the ready list, taking the message with it.
  const unknown = Object.entries(errors)
    .map(([id, err]) => ({ id, detail: unknownOutcome(err) }))
    .filter((e): e is { id: string; detail: UnknownWriteDetail } => e.detail !== null);

  const ready = draws.filter((d) => drawState(d) === 'ready');
  // Overdue first, then by scheduled date — the ones you're most likely to
  // have forgotten sit at the top.
  const pending = draws
    .filter((d) => drawState(d) === 'pending')
    .sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date));
  const overdue = pending.filter((d) => drawIsOverdue(d));
  // Most recently drafted first. This is the ledger read back, not local state —
  // it survives a reload, a different browser, and next month.
  const drafted = draws
    .filter((d) => drawState(d) === 'invoiced')
    .sort((a, b) => (b.invoiced_at ?? '').localeCompare(a.invoiced_at ?? ''));

  const readyValue = ready.reduce((s, d) => s + d.amount, 0);
  const pendingValue = pending.reduce((s, d) => s + d.amount, 0);
  const draftedValue = drafted.reduce((s, d) => s + (d.invoiced_amount ?? d.amount), 0);
  // The just-created set, for the confirmation banner. Ordered by the ledger
  // rather than by click order so a reload shows the same thing.
  const justCreated = drafted.filter((d) => results[d.id]);

  if (isLoading) {
    return <p className="text-sm text-slate-500 animate-pulse">Loading draws…</p>;
  }
  if (error) {
    return <p className="text-sm text-red-700">{(error as Error).message}</p>;
  }

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <StatTile
          label="Ready to bill"
          value={ready.length}
          sub={money(readyValue)}
          tone={ready.length > 0 ? 'good' : 'default'}
        />
        <StatTile
          label="Awaiting delivery"
          value={pending.length}
          sub={money(pendingValue)}
        />
        <StatTile
          label="Past scheduled date"
          value={overdue.length}
          sub={overdue.length > 0 ? 'confirm or re-schedule' : 'nothing late'}
          tone={overdue.length > 0 ? 'warn' : 'default'}
        />
      </div>

      {failure && (
        <div className="bg-red-500/10 border border-red-500/40 rounded-lg px-4 py-2.5">
          <p className="text-xs text-red-700">{failure.message}</p>
        </div>
      )}

      {/* Confirmation of what this session created.
          Lives here rather than in the card because a drafted draw becomes
          `invoiced`, drops out of the ready list, and unmounts its card — which
          is how the first live invoice got created with nothing on screen to
          confirm it. Dismissible, and the durable record is Drafted below. */}
      {justCreated.length > 0 && (
        <div className="bg-emerald-500/10 border border-emerald-500/40 rounded-xl px-4 py-3 space-y-1.5">
          <div className="flex items-center gap-2">
            <Check className="w-4 h-4 text-emerald-700 flex-shrink-0" />
            <p className="text-sm text-emerald-800 font-medium">
              {justCreated.length === 1
                ? 'Draft created in Harvest'
                : `${justCreated.length} drafts created in Harvest`}
            </p>
            <button
              onClick={() => setResults({})}
              className="ml-auto text-xs text-emerald-700 hover:text-emerald-900"
            >
              Dismiss
            </button>
          </div>
          {justCreated.map((d) => {
            const r = results[d.id];
            const url = harvestInvoiceUrl(baseUri, d.harvest_invoice_id);
            return (
              <p key={d.id} className="text-xs text-slate-700 leading-relaxed">
                <span className="font-medium">{r.harvest_invoice_number}</span> —{' '}
                {d.harvest_client_name} · {d.description} · {money(r.actual_amount)} ·
                issued {shortDate(r.issue_date)}, due{' '}
                <span className="font-medium">{shortDate(r.due_date)}</span>
                {r.variance !== 0 && (
                  <span className="text-amber-700">
                    {' '}· Harvest returned {money(r.actual_amount)} against a planned{' '}
                    {money(r.planned_amount)}
                  </span>
                )}
                {url && (
                  <>
                    {' · '}
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-emerald-700 hover:text-emerald-900 inline-flex items-center gap-0.5 font-medium"
                    >
                      Open in Harvest <ExternalLink className="w-3 h-3" />
                    </a>
                  </>
                )}
              </p>
            );
          })}
          <p className="text-[11px] text-slate-500">
            It is a <span className="font-medium">draft</span> — nothing has been sent. Send it
            from Harvest.{' '}
            <Link
              to="/invoices/drafted"
              className="text-cyan-600 hover:text-cyan-700 inline-flex items-center gap-0.5"
            >
              Drafted <ChevronRight className="w-3 h-3" />
            </Link>
          </p>
        </div>
      )}

      {/* The just-happened case: this request's POST did not return. Shown
          separately from the queue below because the operator is standing right
          here and needs to know that a click they just made is unresolved. */}
      {unknown.length > 0 && (
        <div className="bg-red-500/10 border border-red-500/40 rounded-xl p-4 space-y-1">
          <p className="text-sm text-red-700 font-medium flex items-center gap-2">
            <AlertOctagon className="w-4 h-4" /> The outcome of that write is unknown
          </p>
          {unknown.map(({ id, detail }) => (
            <p key={id} className="text-xs text-slate-700 leading-relaxed">
              {detail.message} {detail.remedy}
            </p>
          ))}
        </div>
      )}

      {/* Only reachable if a create half-completed. Loud on purpose: the draw is
          out of the queue and a Harvest invoice may or may not exist. */}
      {inFlightItems.length > 0 && (
        <div className="bg-red-500/10 border border-red-500/40 rounded-xl p-4 space-y-3">
          <div>
            <p className="text-sm text-red-700 font-medium flex items-center gap-2">
              <AlertOctagon className="w-4 h-4" /> Unresolved in-flight writes
              ({inFlightItems.length})
            </p>
            <p className="text-xs text-slate-600 mt-1 leading-relaxed">
              A POST to Harvest never returned, so the system does not know whether the invoice
              was created. It will not retry and it will not guess. These stay locked until you
              say which happened.
            </p>
          </div>
          {inFlightItems.map((item) => (
            <InFlightRow
              key={item.billing_run_item_id}
              item={item}
              baseUri={baseUri}
            />
          ))}
        </div>
      )}

      {/* Ready to bill */}
      <div className="space-y-2">
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          Ready to bill ({ready.length}) — expand to check the invoice before creating it
        </p>
        {ready.length === 0 ? (
          <div className="bg-white border border-slate-200 rounded-xl px-4 py-10 text-center">
            <p className="text-sm text-slate-600">Nothing is waiting to be drafted.</p>
            <p className="text-xs text-slate-400 mt-1">
              Confirm delivery on a draw below and it lands here.
            </p>
          </div>
        ) : (
          byClient(ready).map(([client, rows]) => (
            <div key={client} className="space-y-2">
              <p className="text-xs text-slate-600 font-medium pt-1">{client}</p>
              {rows.map((d) => (
                <ReadyCard
                  key={d.id}
                  draw={d}
                  busy={busy}
                  onWithdraw={() => release.mutate({ id: d.id, released: false })}
                  onCreate={() => create.mutate(d.id)}
                  creating={creatingId === d.id}
                  createError={errors[d.id] ?? null}
                />
              ))}
            </div>
          ))
        )}
      </div>

      {/* Awaiting delivery */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
            Awaiting delivery ({pending.length})
          </p>
          {overdue.length > 0 && (
            <p className="flex items-center gap-1.5 text-xs text-amber-700">
              <AlertTriangle className="w-3.5 h-3.5" />
              {overdue.length} past {overdue.length === 1 ? 'its' : 'their'} scheduled date —
              confirm delivery, or update the schedule on the group
            </p>
          )}
        </div>
        {pending.length === 0 ? (
          <div className="bg-white border border-slate-200 rounded-xl px-4 py-8 text-center">
            <p className="text-sm text-slate-600">Every scheduled draw has been delivered.</p>
          </div>
        ) : (
          pending.map((d) => (
            <PendingRow
              key={d.id}
              draw={d}
              busy={busy}
              onConfirm={() => release.mutate({ id: d.id, released: true })}
            />
          ))
        )}
      </div>

      {/* Drafted draws live on the Drafted tab, not here. The ledger records both
          kinds of invoice, and "what have we drafted" is not a question about the
          draw queue — this screen is the work still to do. */}
      {drafted.length > 0 && (
        <p className="text-xs text-slate-500">
          {drafted.length} {drafted.length === 1 ? 'draw has' : 'draws have'} been drafted
          {draftedValue > 0 && <> — {money(draftedValue)}</>}.{' '}
          <Link
            to="/invoices/drafted?kind=draw"
            className="text-cyan-600 hover:text-cyan-700 inline-flex items-center gap-0.5"
          >
            See what was created <ChevronRight className="w-3 h-3" />
          </Link>
        </p>
      )}

      <p className="text-[11px] text-slate-400 leading-relaxed">
        A scheduled date is what the contract commits to — it prompts, it never bills. Draws are
        drafted one at a time, always for the scheduled amount; to change an amount or a date, edit
        the schedule on its{' '}
        <Link to="/invoices/groups" className="text-cyan-600 hover:text-cyan-700 inline-flex items-center gap-0.5">
          billing group <ChevronRight className="w-3 h-3" />
        </Link>
      </p>
    </div>
  );
}
