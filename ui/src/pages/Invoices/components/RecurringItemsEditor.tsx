import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Plus, Trash2, ArrowUp, ArrowDown, CalendarRange, X } from 'lucide-react';
import { getInvoiceItemCategories } from '../../../api';
import { money, monthLabel } from '../../../invoicing';
import type { RecurringItemInput } from '../../../invoicing';

interface ProjectChoice {
  harvest_project_id: number;
  name: string;
}

interface Props {
  items: RecurringItemInput[];
  onChange: (items: RecurringItemInput[]) => void;
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

function blankItem(projectId: number): RecurringItemInput {
  return {
    harvest_project_id: projectId,
    description: '',
    quantity: 1,
    unit_price: 0,
    kind: 'Service',
    is_placeholder: false,
    sort_order: 0,
    effective_from: null,
    effective_to: null,
  };
}

/** Plain-English summary of a line's effective window. */
function scheduleSummary(item: RecurringItemInput): string {
  const { effective_from: from, effective_to: to } = item;
  if (!from && !to) return 'Billed every month';
  if (from && to) return `${monthLabel(from)} → ${monthLabel(to)} only`;
  if (from) return `From ${monthLabel(from)} onward`;
  return `Through ${monthLabel(to!)}`;
}

/** `2026-08-01` ⇄ `2026-08` for the month input. */
const toMonthInput = (iso: string | null) => (iso ? iso.slice(0, 7) : '');
const fromMonthInput = (v: string) => (v ? `${v}-01` : null);

/**
 * Line-item editor for `recurring_monthly` groups.
 *
 * Each line targets its own project, so one invoice can carry hosting against
 * the hosting project and a service fee against a different one. `kind` is the
 * Harvest invoice item category, validated against the account's own list at
 * save time and again at plan time.
 *
 * Lines whose amount is only knowable after the fact — hosting pass-through, a
 * tooling fee that's a percentage of it — are marked **entered in Harvest**.
 * They're created at $0 so the draft carries the right scaffolding, and are
 * excluded from the pre-flight total rather than dragging it down silently.
 */
export default function RecurringItemsEditor({ items, onChange, projects }: Props) {
  // Effective dates are collapsed by default: most lines bill every month, and
  // an unexplained pair of date inputs reads as required when it isn't.
  const [datesOpen, setDatesOpen] = useState<Set<number>>(new Set());

  const { data: categories = [] } = useQuery({
    queryKey: ['invoice-item-categories'],
    queryFn: getInvoiceItemCategories,
  });

  const update = (i: number, patch: Partial<RecurringItemInput>) =>
    onChange(items.map((it, idx) => (idx === i ? { ...it, ...patch } : it)));

  const remove = (i: number) => onChange(items.filter((_, idx) => idx !== i));

  const toggleDates = (i: number) =>
    setDatesOpen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const move = (i: number, delta: number) => {
    const next = [...items];
    const j = i + delta;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next.map((it, idx) => ({ ...it, sort_order: idx + 1 })));
  };

  const fixedTotal = items
    .filter((it) => !it.is_placeholder)
    .reduce((s, it) => s + it.quantity * it.unit_price, 0);
  const placeholderCount = items.filter((it) => it.is_placeholder).length;

  if (projects.length === 0) {
    return (
      <p className="text-xs text-slate-400">
        Select at least one project first — every line item has to target one.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {items.length === 0 && (
        <p className="text-xs text-slate-400">
          No line items yet. A recurring group with none is skipped at plan time.
        </p>
      )}

      {items.map((item, i) => {
        const lineTotal = item.quantity * item.unit_price;
        const showDates = datesOpen.has(i);
        const scheduled = !!(item.effective_from || item.effective_to);

        return (
          <div
            key={i}
            className={`border rounded-lg p-3 space-y-2.5 ${
              item.is_placeholder
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
                    onChange={(e) => update(i, { description: e.target.value })}
                    placeholder="e.g. Hosting — {period_label}"
                  />
                </div>

                <div className="grid grid-cols-2 md:grid-cols-12 gap-2">
                  <div className="col-span-2 md:col-span-4">
                    <FieldLabel>Project</FieldLabel>
                    <select
                      className={inputCls}
                      value={item.harvest_project_id}
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

                  <div className="col-span-2 md:col-span-3">
                    <FieldLabel>Fee type</FieldLabel>
                    <select
                      className={inputCls}
                      value={item.kind}
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

                  <div className="md:col-span-2">
                    <FieldLabel>Quantity</FieldLabel>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      className={`${inputCls} text-right tabular-nums`}
                      value={item.quantity}
                      onChange={(e) => update(i, { quantity: Number(e.target.value) })}
                    />
                  </div>

                  <div className="md:col-span-3">
                    <FieldLabel>
                      {item.is_placeholder ? 'Price per unit' : 'Price per unit ($)'}
                    </FieldLabel>
                    {item.is_placeholder ? (
                      <div className="w-full border border-dashed border-amber-400/60 rounded px-2 py-1 text-sm text-amber-700 bg-amber-50/60">
                        set in Harvest
                      </div>
                    ) : (
                      <div className="relative">
                        <span className="absolute left-2 top-1/2 -translate-y-1/2 text-sm text-slate-400">
                          $
                        </span>
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          className={`${inputCls} pl-5 text-right tabular-nums`}
                          value={item.unit_price}
                          onChange={(e) => update(i, { unit_price: Number(e.target.value) })}
                        />
                      </div>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex flex-col gap-1 pt-5">
                <button
                  onClick={() => move(i, -1)}
                  disabled={i === 0}
                  title="Move up"
                  className="text-slate-400 hover:text-slate-700 disabled:opacity-25"
                >
                  <ArrowUp className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => move(i, 1)}
                  disabled={i === items.length - 1}
                  title="Move down"
                  className="text-slate-400 hover:text-slate-700 disabled:opacity-25"
                >
                  <ArrowDown className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => remove(i)}
                  title="Remove line"
                  className="text-slate-400 hover:text-red-600"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <div className="pl-6 space-y-2">
              <label className="flex items-start gap-1.5 text-[11px] text-slate-600">
                <input
                  type="checkbox"
                  checked={item.is_placeholder}
                  onChange={(e) => update(i, {
                    is_placeholder: e.target.checked,
                    ...(e.target.checked ? { unit_price: 0 } : {}),
                  })}
                  className="w-3.5 h-3.5 rounded accent-amber-500 mt-0.5"
                />
                <span>
                  Amount varies — I'll enter it in Harvest
                  <span className="block text-slate-400">
                    The line is created at $0 with this description, fee type, and project,
                    ready for you to fill in on the draft. Excluded from the estimate.
                  </span>
                </span>
              </label>

              <div className="flex items-center gap-3 flex-wrap">
                <button
                  onClick={() => toggleDates(i)}
                  className={`inline-flex items-center gap-1.5 text-[11px] rounded px-1.5 py-0.5 transition-colors ${
                    scheduled
                      ? 'text-cyan-700 bg-cyan-500/10 hover:bg-cyan-500/20'
                      : 'text-slate-500 hover:text-slate-700 hover:bg-slate-100'
                  }`}
                  title="When this line applies"
                >
                  <CalendarRange className="w-3 h-3" />
                  {scheduleSummary(item)}
                </button>

                {!item.is_placeholder && lineTotal > 0 && (
                  <span className="ml-auto text-[11px] text-slate-500 tabular-nums">
                    {item.quantity} × {money(item.unit_price)} ={' '}
                    <span className="font-semibold text-slate-800">{money(lineTotal)}</span>
                    <span className="text-slate-400"> / month</span>
                  </span>
                )}
              </div>

              {showDates && (
                <div className="border border-slate-200 rounded-lg bg-white p-2.5 space-y-2">
                  <p className="text-[11px] text-slate-500 leading-relaxed">
                    Which months this line is billed in. Leave both blank and it bills
                    every month. Set a last month to retire a fee without deleting it —
                    the old row stays on record and simply stops applying.
                  </p>
                  <div className="flex items-end gap-3 flex-wrap">
                    <label className="text-[11px] text-slate-600">
                      <span className="block mb-0.5">First month billed</span>
                      <input
                        type="month"
                        value={toMonthInput(item.effective_from)}
                        onChange={(e) =>
                          update(i, { effective_from: fromMonthInput(e.target.value) })}
                        className="bg-white border border-slate-300 rounded px-1.5 py-1 text-xs text-slate-700"
                      />
                    </label>
                    <label className="text-[11px] text-slate-600">
                      <span className="block mb-0.5">Last month billed</span>
                      <input
                        type="month"
                        value={toMonthInput(item.effective_to)}
                        onChange={(e) =>
                          update(i, { effective_to: fromMonthInput(e.target.value) })}
                        className="bg-white border border-slate-300 rounded px-1.5 py-1 text-xs text-slate-700"
                      />
                    </label>
                    {scheduled && (
                      <button
                        onClick={() =>
                          update(i, { effective_from: null, effective_to: null })}
                        className="inline-flex items-center gap-1 text-[11px] text-slate-500 hover:text-slate-700 pb-1"
                      >
                        <X className="w-3 h-3" />
                        Clear — bill every month
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        );
      })}

      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={() => onChange([
            ...items,
            { ...blankItem(projects[0].harvest_project_id), sort_order: items.length + 1 },
          ])}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Add line item
        </button>

        {items.length > 0 && (
          <span className="ml-auto text-xs text-slate-600">
            Fixed monthly total{' '}
            <span className="font-semibold text-slate-900 tabular-nums">{money(fixedTotal)}</span>
            {placeholderCount > 0 && (
              <span className="text-amber-600">
                {' '}+ {placeholderCount} entered in Harvest
              </span>
            )}
          </span>
        )}
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Each line targets its own project, so one invoice can span several — hosting against the
        hosting project, a service fee against another. <code>{'{period_label}'}</code> and{' '}
        <code>{'{client_name}'}</code> are replaced at plan time, so “Hosting —{' '}
        {'{period_label}'}” becomes “Hosting — August 2026”.
      </p>
    </div>
  );
}
