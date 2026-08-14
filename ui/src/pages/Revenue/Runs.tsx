import { Link } from 'react-router-dom';
import { Play } from 'lucide-react';
import StubBadge from '../../components/shared/StubBadge';
import { StatTile } from '../Invoices/components/Bits';
import { RunStatusChip } from './components/Bits';
import { money, dateTime, shortDate } from '../../invoicing';
import { MOCK_REVENUE } from './mockData';

/**
 * Runs — one rev rec run per closed month.
 *
 * There is no run detail screen and no trigger: `trigger_revenue_recognition`
 * exists as a tool but ADR-0004 removed it from every agent's `allowed_tools`,
 * and no operator-initiated endpoint has been built to replace it. The button
 * below is deliberately inert.
 */
export default function Runs() {
  const { runs } = MOCK_REVENUE;

  const open = runs.filter((r) => r.status === 'awaiting_approval').length;
  const recognized = runs.reduce((sum, r) => sum + r.total_recognized, 0);

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Runs" value={runs.length} sub="one per closed month" />
        <StatTile
          label="Awaiting approval"
          value={open}
          tone={open > 0 ? 'warn' : 'default'}
          sub={open > 0 ? 'nothing posts until approved' : 'all settled'}
        />
        <StatTile label="Recognised across runs" value={money(recognized)} />
        <StatTile
          label="Projects per run"
          value={runs[0]?.project_count ?? 0}
          sub="active engagements"
        />
      </div>

      <div className="flex items-center gap-3 bg-white border border-slate-200 rounded-lg px-4 py-3">
        <span className="text-xs text-slate-500 uppercase tracking-wide font-medium">New run</span>
        <span className="text-xs text-slate-400">
          A run reads Harvest hours and Airtable contract terms, then proposes one entry per active
          project for the closed month.
        </span>
        <button
          disabled
          title="Rev rec still runs against Airtable; there is no endpoint behind this yet."
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-600 opacity-50 cursor-not-allowed"
        >
          <Play className="w-3.5 h-3.5" />
          Run Revenue Recognition
        </button>
        <StubBadge />
      </div>

      <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">Run history</p>
      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Month</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-left px-4 py-3 font-medium">Period end</th>
              <th className="text-right px-4 py-3 font-medium">Projects</th>
              <th className="text-right px-4 py-3 font-medium">Recognised</th>
              <th className="text-left px-4 py-3 font-medium">Triggered</th>
              <th className="text-left px-4 py-3 font-medium">By</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="px-4 py-3 text-slate-900 font-medium whitespace-nowrap">
                  {run.month_label}
                </td>
                <td className="px-4 py-3"><RunStatusChip status={run.status} /></td>
                <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                  {shortDate(run.date_recognized)}
                </td>
                <td className="px-4 py-3 text-right text-slate-700 tabular-nums">
                  {run.project_count}
                </td>
                <td className="px-4 py-3 text-right text-slate-900 tabular-nums">
                  {money(run.total_recognized)}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                  {dateTime(run.triggered_at)}
                </td>
                <td className="px-4 py-3 text-xs text-slate-600">{run.triggered_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        A run proposes entries; it does not post them. Approval is a human action taken in the{' '}
        <Link to="/inbox" className="text-cyan-600 hover:text-cyan-600">approval inbox</Link>, and
        no agent can approve its own proposal. Per-run detail — the entries a single run produced,
        with their flags — is not part of this mockup; the{' '}
        <Link to="/revenue/entries" className="text-cyan-600 hover:text-cyan-600">Entries</Link> tab
        filters to one run instead.
      </p>
    </div>
  );
}
