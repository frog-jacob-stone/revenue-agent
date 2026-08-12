import { useQuery } from '@tanstack/react-query';
import { Plus, Trash2, ArrowUp, ArrowDown, Lock } from 'lucide-react';
import { getInvoiceItemCategories } from '../../../api';
import { money, shortDate, isoDate, drawState, drawIsOverdue } from '../../../invoicing';
import type { DrawInput } from '../../../invoicing';
import { DrawStateChip } from './Bits';

interface ProjectChoice {
  harvest_project_id: number;
  name: string;
}

interface Props {
  items: DrawInput[];
  onChange: (items: DrawInput[]) => void;
  projects: ProjectChoice[];
}

const inputCls =
  'w-full bg-white border border-slate-300 rounded px-2 py-1 text-sm text-slate-800 ' +
  'placeholder:text-slate-400 outline-none focus:border-cyan-500/60';

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="block text-[10px] text-slate-500 uppercase tracking-wide font-medium mb-0.5">
      {children}
    </span>
  );
}

/** Same day, one month on. Clamps to the end of a shorter month, so 31 Jan
 *  gives 28 Feb rather than rolling into March. */
function aMonthLater(iso: string): string {
  const [y, m, day] = iso.slice(0, 10).split('-').map(Number);
  const lastOfNext = new Date(y, m + 1, 0).getDate();
  return isoDate(new Date(y, m, Math.min(day, lastOfNext)));
}

function blankDraw(projectId: number, prev: DrawInput | undefined, seq: number): DrawInput {
  return {
    id: null,
    harvest_project_id: prev?.harvest_project_id ?? projectId,
    description: '',
    amount: 0,
    kind: prev?.kind ?? 'Service',
    // Draws march forward, so the obvious default for draw N+1 is a month after
    // draw N. Saves retyping a date on every row of a schedule.
    scheduled_date: prev ? aMonthLater(prev.scheduled_date) : isoDate(new Date()),
    sequence: seq,
  };
}

/**
 * Payment schedule editor for `fixed_fee_schedule` groups.
 *
 * The schedule is the contract's commitment and what the team works against —
 * but a scheduled date never bills anything by itself. Every draw waits for a
 * human to confirm delivery, which happens on the group page or on the Draws
 * tab, not here. When a date slips, you come back and edit it.
 *
 * A draw that has already been invoiced is locked: its amount and description
 * are on a real invoice, and rewriting them here would rewrite history. A draw
 * with an invoice being created in Harvest is locked too, one step earlier —
 * execution has begun against these exact values.
 */
export default function DrawScheduleEditor({ items, onChange, projects }: Props) {
  const { data: categories = [] } = useQuery({
    queryKey: ['invoice-item-categories'],
    queryFn: getInvoiceItemCategories,
  });

  const update = (i: number, patch: Partial<DrawInput>) =>
    onChange(items.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));

  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i));

  const move = (i: number, delta: number) => {
    const next = [...items];
    const j = i + delta;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next.map((it, idx) => ({ ...it, sequence: idx + 1 })));
  };

  const contractTotal = items.reduce((s, it) => s + it.amount, 0);
  const draftedTotal = items
    .filter((it) => it.invoiced_run_id)
    .reduce((s, it) => s + it.amount, 0);

  if (projects.length === 0) {
    return (
      <p className="text-xs text-slate-400">
        Select at least one project first — every draw has to be attributed to one.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {items.length === 0 && (
        <p className="text-xs text-slate-400">
          No draws yet. Add the contract's payment schedule — each draw becomes billable
          when you confirm delivery, not when its date arrives.
        </p>
      )}

      {items.map((item, i) => {
        const marks = {
          released_at: item.released_at ?? null,
          invoiced_run_id: item.invoiced_run_id ?? null,
          live_run_id: item.live_run_id ?? null,
        };
        const state = drawState(marks);
        const overdue = drawIsOverdue({ ...marks, scheduled_date: item.scheduled_date });
        // Both locked states mean the same thing here: an invoice payload was
        // already built from these values, so editing them in place would
        // describe an invoice nobody agreed to. The server refuses it too.
        const locked = state === 'invoiced' || state === 'in_flight';

        return (
          <div
            key={item.id ?? `new-${i}`}
            className={`border rounded-lg p-3 space-y-2.5 ${
              locked
                ? 'border-slate-200 bg-slate-100/70'
                : overdue
                  ? 'border-amber-400/40 bg-amber-50/40'
                  : 'border-slate-200 bg-slate-50/60'
            }`}
          >
            <div className="flex items-start gap-2">
              <span className="text-[11px] text-slate-400 w-4 pt-5">{i + 1}</span>
              <div className="flex-1 space-y-2">
                <div>
                  <FieldLabel>Description — appears on the invoice</FieldLabel>
                  <input
                    className={inputCls}
                    value={item.description}
                    disabled={locked}
                    onChange={(e) => update(i, { description: e.target.value })}
                    placeholder="e.g. Draw 3 of 5 — UAT sign-off"
                  />
                </div>

                <div className="grid grid-cols-2 md:grid-cols-12 gap-2">
                  <div className="col-span-2 md:col-span-4">
                    <FieldLabel>Project</FieldLabel>
                    <select
                      className={inputCls}
                      value={item.harvest_project_id}
                      disabled={locked}
                      onChange={(e) =>
                        update(i, { harvest_project_id: Number(e.target.value) })}
                    >
                      {projects.map((p) => (
                        <option key={p.harvest_project_id} value={p.harvest_project_id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="col-span-1 md:col-span-3">
                    <FieldLabel>Fee type</FieldLabel>
                    <select
                      className={inputCls}
                      value={item.kind}
                      disabled={locked}
                      onChange={(e) => update(i, { kind: e.target.value })}
                    >
                      {categories.length === 0 && (
                        <option value={item.kind}>{item.kind}</option>
                      )}
                      {categories.map((c) => (
                        <option key={c.harvest_id} value={c.name}>{c.name}</option>
                      ))}
                    </select>
                  </div>

                  <div className="col-span-1 md:col-span-3">
                    <FieldLabel>Amount ($)</FieldLabel>
                    <div className="relative">
                      <span className="absolute left-2 top-1/2 -translate-y-1/2 text-sm text-slate-400">
                        $
                      </span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        className={`${inputCls} pl-5 text-right tabular-nums`}
                        value={item.amount}
                        disabled={locked}
                        onChange={(e) => update(i, { amount: Number(e.target.value) })}
                      />
                    </div>
                  </div>

                  <div className="col-span-2 md:col-span-2">
                    <FieldLabel>Scheduled date</FieldLabel>
                    <input
                      type="date"
                      className={inputCls}
                      value={item.scheduled_date.slice(0, 10)}
                      disabled={locked}
                      onChange={(e) => update(i, { scheduled_date: e.target.value })}
                    />
                  </div>
                </div>
              </div>

              <div className="flex flex-col gap-1 pt-5">
                <button
                  type="button"
                  onClick={() => move(i, -1)}
                  disabled={i === 0 || locked}
                  title="Move up"
                  className="text-slate-400 hover:text-slate-700 disabled:opacity-25"
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => move(i, 1)}
                  disabled={i === items.length - 1 || locked}
                  title="Move down"
                  className="text-slate-400 hover:text-slate-700 disabled:opacity-25"
                >
                  <ArrowDown className="w-3.5 h-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => remove(i)}
                  disabled={locked}
                  title={
                    state === 'invoiced'
                      ? 'Invoiced draws cannot be removed'
                      : locked
                        ? 'An invoice is being created from this draw'
                        : 'Remove draw'
                  }
                  className="text-slate-400 hover:text-red-600 disabled:opacity-25 disabled:hover:text-slate-400"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <div className="pl-6 flex items-center gap-2 flex-wrap">
              <DrawStateChip state={state} overdue={overdue} />
              <span className="text-[11px] text-slate-500">
                {state === 'invoiced'
                  ? 'Drafted — locked. Amount and description are on a real Harvest invoice.'
                  : state === 'in_flight'
                    ? 'Locked — an invoice is being created in Harvest from these values.'
                    : state === 'ready'
                      ? 'Delivery confirmed. Waiting to be drafted.'
                      : overdue
                        ? `Scheduled ${shortDate(item.scheduled_date)} — past due and not yet delivered.`
                        : `Scheduled ${shortDate(item.scheduled_date)}. Bills when you confirm delivery.`}
              </span>
              {locked && <Lock className="w-3 h-3 text-slate-400" />}
            </div>
          </div>
        );
      })}

      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={() => onChange([
            ...items,
            blankDraw(projects[0].harvest_project_id, items[items.length - 1], items.length + 1),
          ])}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Add draw
        </button>

        {items.length > 0 && (
          <span className="ml-auto text-xs text-slate-600">
            Contract total{' '}
            <span className="font-semibold text-slate-900 tabular-nums">
              {money(contractTotal)}
            </span>
            {draftedTotal > 0 && (
              <span className="text-slate-400">
                {' '}· {money(draftedTotal)} drafted,{' '}
                {money(contractTotal - draftedTotal)} remaining
              </span>
            )}
          </span>
        )}
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        The scheduled date is what the contract commits to — it drives the overdue prompt and
        nothing else. No draw bills until someone confirms delivery, on the group page or on the{' '}
        <span className="text-slate-500">Draws</span> tab. If a date slips, come back and
        change it.
      </p>
    </div>
  );
}
