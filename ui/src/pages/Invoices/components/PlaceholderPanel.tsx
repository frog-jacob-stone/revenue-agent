import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Undo2, Ban } from 'lucide-react';
import { setPlaceholderResolution, clearPlaceholderResolution } from '../../../api';
import { money, placeholderLines } from '../../../invoicing';
import type {
  BillingRunDetail, EstimatedLineItem, RunItem,
} from '../../../invoicing';

interface Props {
  run: BillingRunDetail;
  item: RunItem;
}

/**
 * Deciding the placeholders on one planned invoice.
 *
 * A placeholder is a line whose description, category, and project are fixed in
 * config but whose amount is only knowable after the fact — hosting
 * pass-through, a percentage-based tooling fee, a retainer overage. This is
 * where the amount gets decided, before the Harvest draft exists.
 *
 * Two answers, both of them decisions:
 *
 *   - **an amount** — bills the line at that price this month;
 *   - **omit** — drops it from this month's invoice and leaves the template
 *     alone, so it comes back next month asking again.
 *
 * There is deliberately no third option and no override. An undecided
 * placeholder blocks approval, and the reason it exists at all is to be
 * impossible to forget; a "skip for now" button would be the forgetting.
 *
 * A row is therefore in exactly one of two modes, and the mode is **derived
 * from the server's `placeholder_state`** rather than tracked locally:
 *
 *   - undecided → Qty and Rate inputs, a note field, `Save` and `Omit`;
 *   - decided (either way) → the values read-only, and `Undo`.
 *
 * Revisiting a decision is Undo first, then decide again. That is one more click
 * than editing in place, and worth it: the earlier version carried a local
 * `editing` flag alongside the server state, which let them disagree. `Enter
 * amount` on an omitted row set the flag but the render guard also required
 * `!omitted`, so the button existed and did nothing. With the mode derived,
 * that class of bug cannot be written.
 *
 * Styled as a sibling of the Estimated line items table below it, not as a
 * warning. Entering a monthly amount is a step in the process, not a fault, and
 * the alarm palette belongs to the things that mean something went wrong. The
 * step cannot be skipped past regardless of how calm it looks, because approval
 * is blocked until every row here is decided — so the colour has no work to do.
 *
 * An omitted line stays on screen, struck through. It costs a row and it is the
 * whole point: a reminder that vanishes when you decline it is not a reminder.
 */
export default function PlaceholderPanel({ run, item }: Props) {
  const lines = placeholderLines(item);
  if (lines.length === 0) return null;

  const decided = lines.filter((li) => li.placeholder_state !== 'unresolved').length;

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
          Placeholders
        </p>
        <p className="text-[11px] text-slate-500 tabular-nums">
          {decided} of {lines.length} decided
        </p>
      </div>

      <div className="border border-slate-200 rounded-lg overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-white text-slate-500 uppercase tracking-wide">
              <th className="text-left px-3 py-2 font-medium">Description</th>
              <th className="text-right px-3 py-2 font-medium w-20">Qty</th>
              <th className="text-right px-3 py-2 font-medium w-28">Rate</th>
              <th className="text-right px-3 py-2 font-medium w-24">Amount</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <PlaceholderRow
                key={line.recurring_line_item_id}
                run={run}
                item={item}
                line={line}
              />
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 mt-1.5">
        Amounts are decided here each month, before the Harvest draft is created. Omitting
        leaves the line configured, so it comes back next month. This invoice can't be
        approved until every row is decided.
      </p>
    </div>
  );
}

const CELL_INPUT =
  'w-full border border-slate-300 rounded px-1.5 py-0.5 text-xs text-right tabular-nums';

function PlaceholderRow({
  run, item, line,
}: {
  run: BillingRunDetail;
  item: RunItem;
  line: EstimatedLineItem;
}) {
  const state = line.placeholder_state ?? 'unresolved';
  const lineId = line.recurring_line_item_id!;
  // The row's mode, straight off the server. No local `editing` flag to fall
  // out of step with it.
  const decided = state !== 'unresolved';
  const omitted = state === 'omitted';

  const [rate, setRate] = useState('');
  const [qty, setQty] = useState(String(line.quantity ?? 1));
  const [note, setNote] = useState('');

  const queryClient = useQueryClient();
  const onSuccess = (fresh: BillingRunDetail) => {
    queryClient.setQueryData(['billing-run', run.id], fresh);
    queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
  };

  const save = useMutation({
    mutationFn: () => setPlaceholderResolution(run.id, item.id, lineId, {
      resolution: 'amount',
      unit_price: Number(rate),
      quantity: Number(qty),
      ...(note.trim() ? { note: note.trim() } : {}),
    }),
    onSuccess,
  });

  const omit = useMutation({
    mutationFn: () => setPlaceholderResolution(run.id, item.id, lineId, {
      resolution: 'omitted',
      ...(note.trim() ? { note: note.trim() } : {}),
    }),
    onSuccess,
  });

  const clear = useMutation({
    mutationFn: () => clearPlaceholderResolution(run.id, item.id, lineId),
    // Undo means start over, so the inputs come back empty rather than
    // pre-filled with a figure that is no longer recorded anywhere.
    onSuccess: (fresh) => {
      setRate('');
      setNote('');
      onSuccess(fresh);
    },
  });

  const busy = save.isPending || omit.isPending || clear.isPending;
  const failure = (save.error ?? omit.error ?? clear.error) as Error | null;
  // A rate of 0 is legitimate — a pass-through that genuinely cost nothing this
  // month, which is different from omitting the line entirely.
  const rateValid = rate.trim() !== '' && Number.isFinite(Number(rate)) && Number(rate) >= 0;

  return (
    <>
      <tr className="border-t border-slate-200">
        <td className="px-3 py-2">
          <span className={omitted ? 'text-slate-400 line-through' : 'text-slate-800'}>
            {line.label}
          </span>
          <span className="text-slate-400 block text-[11px]">
            {line.kind}
            {omitted && ' · omitted for this month'}
          </span>
        </td>

        {!decided ? (
          <>
            <td className="px-3 py-2">
              <input
                type="number"
                step="0.01"
                min="0"
                value={qty}
                disabled={busy}
                onChange={(e) => setQty(e.target.value)}
                className={CELL_INPUT}
              />
            </td>
            <td className="px-3 py-2">
              <div className="relative">
                <span className="absolute left-1.5 top-1/2 -translate-y-1/2 text-xs text-slate-400">
                  $
                </span>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  autoFocus
                  value={rate}
                  disabled={busy}
                  placeholder="0.00"
                  onChange={(e) => setRate(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && rateValid && !busy) save.mutate();
                  }}
                  className={`${CELL_INPUT} pl-4`}
                />
              </div>
            </td>
            <td className="px-3 py-2 text-right text-slate-400 tabular-nums">
              {rateValid ? money(Number(qty) * Number(rate)) : '—'}
            </td>
          </>
        ) : (
          <>
            <td className={`px-3 py-2 text-right tabular-nums ${omitted ? 'text-slate-300 line-through' : 'text-slate-600'}`}>
              {line.quantity}
            </td>
            <td className={`px-3 py-2 text-right tabular-nums ${omitted ? 'text-slate-300' : 'text-slate-600'}`}>
              {omitted ? '—' : money(line.unit_price)}
            </td>
            <td className={`px-3 py-2 text-right tabular-nums ${omitted ? 'text-slate-300' : 'text-slate-800'}`}>
              {omitted ? '—' : money(line.amount)}
            </td>
          </>
        )}

        <td className="px-3 py-2">
          <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
            {decided ? (
              <button
                onClick={() => clear.mutate()}
                disabled={busy}
                title="Withdraw this decision. The line goes back to needing one — an amount or an omit — and blocks approval again."
                className="flex items-center gap-1 px-2 py-0.5 rounded border border-slate-300 text-slate-700 font-medium hover:bg-slate-100 disabled:opacity-40 transition-colors"
              >
                {clear.isPending
                  ? <Loader2 className="w-3 h-3 animate-spin" />
                  : <Undo2 className="w-3 h-3" />}
                Undo
              </button>
            ) : (
              <>
                <button
                  onClick={() => save.mutate()}
                  disabled={!rateValid || busy}
                  className="flex items-center gap-1 px-2 py-0.5 rounded border border-slate-300 text-slate-700 font-medium hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {save.isPending
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Check className="w-3 h-3" />}
                  Save
                </button>
                <button
                  onClick={() => omit.mutate()}
                  disabled={busy}
                  title="No charge for this line this month. The line stays configured and comes back next month."
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-slate-500 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-40 transition-colors"
                >
                  {omit.isPending
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Ban className="w-3 h-3" />}
                  Omit
                </button>
              </>
            )}
          </div>
        </td>
      </tr>

      {/* The note is worth most on an omit, where the record would otherwise be
          indistinguishable from a line that never existed — but it doesn't earn
          a column of its own, so it spans one while the row is being decided. */}
      {!decided && (
        <tr>
          <td colSpan={5} className="px-3 pb-2 -mt-1">
            <input
              type="text"
              value={note}
              disabled={busy}
              placeholder="Note (optional) — e.g. checked Harvest, no overage this month"
              onChange={(e) => setNote(e.target.value)}
              className="w-full border border-slate-200 rounded px-2 py-0.5 text-[11px] text-slate-600 placeholder:text-slate-400"
            />
          </td>
        </tr>
      )}

      {failure && (
        <tr>
          <td colSpan={5} className="px-3 pb-2">
            <p className="text-[11px] text-red-700">{failure.message}</p>
          </td>
        </tr>
      )}
    </>
  );
}
