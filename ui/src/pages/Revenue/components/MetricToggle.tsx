import { METRICS, METRIC_ORDER } from '../metrics';
import type { MetricKey } from '../metrics';

/**
 * Which of the three questions the Overview is answering.
 *
 * A segmented control rather than three stacked sections, because these are
 * read one at a time: a second full-width grid below the first is how the page
 * grows by a screenful every time a new measure is wanted. Sits beside the
 * period and client filters, since all three are "what am I looking at".
 */
export default function MetricToggle({
  value,
  onChange,
}: {
  value: MetricKey;
  onChange: (key: MetricKey) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border border-slate-300 overflow-hidden">
      {METRIC_ORDER.map((key, i) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          className={`px-2.5 py-1.5 text-xs font-medium transition-colors ${
            i > 0 ? 'border-l border-slate-300' : ''
          } ${
            value === key
              ? 'bg-slate-900 text-white'
              : 'text-slate-600 hover:bg-slate-100'
          }`}
        >
          {METRICS[key].label}
        </button>
      ))}
    </div>
  );
}
