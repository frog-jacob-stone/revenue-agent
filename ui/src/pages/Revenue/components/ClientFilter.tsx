import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, X } from 'lucide-react';
import { num } from '../../../api';
import type { RevenueClient } from '../../../api';
import { METRICS } from '../metrics';
import type { MetricKey } from '../metrics';

/**
 * Multi-select over the clients with activity in the current period.
 *
 * The options come from `/revenue/clients` rather than from the rows already on
 * screen. A faceted filter built from its own filtered output collapses: pick
 * one client and every other option disappears, leaving no way back.
 *
 * The list is the same in all three metric views — revenue *or* hours puts a
 * client in it — so switching metric changes only the figure beside each name.
 * A client vanishing out from under a selection because the reader switched to
 * hours would be worse than one option that reads as a dash.
 *
 * Empty selection means *all*, not *none*. It is the state the screen opens in,
 * and "no clients selected" showing an empty report would be a trap rather than
 * a filter.
 */

interface Props {
  clients: RevenueClient[];
  /** Selected client ids. Empty means every client. */
  value: number[];
  onChange: (ids: number[]) => void;
  /** Which measure to show beside each name — whatever is on screen. */
  metric: MetricKey;
}

export default function ClientFilter({ clients, value, onChange, metric }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on an outside click. Without it the panel covers the grid it is
  // filtering, and the only way out is to re-click the button that opened it.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const m = METRICS[metric];
  // Same pair, same metric function as everything else on the page, so the
  // number here agrees with the grid rather than being a second opinion.
  const worth = (c: RevenueClient) => {
    const v = m.value({ rev: num(c.recognized_amount), hours: num(c.logged_hours) });
    return v == null ? '—' : m.fmt(v);
  };

  const selected = new Set(value);
  const toggle = (id: number) => {
    const next = new Set(selected);
    if (!next.delete(id)) next.add(id);
    onChange([...next]);
  };

  const label =
    value.length === 0
      ? 'All clients'
      : value.length === 1
        ? clients.find((c) => c.client_id === value[0])?.client_name ?? '1 client'
        : `${value.length} clients`;

  return (
    <div className="relative" ref={ref}>
      <div className="flex items-center gap-1.5">
        <button
          onClick={() => setOpen((o) => !o)}
          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
            value.length > 0
              ? 'bg-slate-900 text-white border-slate-900'
              : 'border-slate-300 text-slate-600 hover:bg-slate-100'
          }`}
        >
          {label}
          <ChevronDown className="w-3.5 h-3.5" />
        </button>
        {value.length > 0 && (
          <button
            onClick={() => onChange([])}
            title="Show every client"
            className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute z-20 mt-1 w-80 max-h-80 overflow-y-auto bg-white border border-slate-200 rounded-xl shadow-lg py-1">
          {clients.length === 0 && (
            <p className="px-3 py-3 text-xs text-slate-500">
              No client had any activity in this period.
            </p>
          )}
          {clients.map((c) => {
            const on = selected.has(c.client_id);
            return (
              <button
                key={c.client_id}
                onClick={() => toggle(c.client_id)}
                className="flex items-center gap-2 w-full px-3 py-1.5 text-left hover:bg-slate-50"
              >
                <span
                  className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${
                    on ? 'bg-cyan-500 border-cyan-500' : 'border-slate-300'
                  }`}
                >
                  {on && <Check className="w-3 h-3 text-white" />}
                </span>
                <span className="text-xs text-slate-800 flex-1 truncate">
                  {c.client_name ?? `Client ${c.client_id}`}
                </span>
                {/* What picking this is worth, in whatever is being looked at,
                    so the list is a summary as well as a control. */}
                <span className="text-[11px] text-slate-400 tabular-nums whitespace-nowrap">
                  {worth(c)}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
