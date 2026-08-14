import { NavLink, Outlet } from 'react-router-dom';
import { FlaskConical } from 'lucide-react';

const TABS = [
  { to: '/revenue/overview', label: 'Overview' },
  { to: '/revenue/runs', label: 'Runs' },
  { to: '/revenue/entries', label: 'Entries' },
];

/**
 * Revenue — a mockup, not a feature.
 *
 * Recognised revenue currently lives only in Airtable, computed by
 * `app/services/revenue.py::calc_revenue`. There is no Postgres table, no API,
 * and nothing in `api.ts` to call. Every figure under this tab comes from
 * `mockData.ts` and exists so the shape of the eventual report can be reviewed
 * before the backend is built.
 *
 * The banner is rendered once here rather than per-tab: the claim "this is not
 * real" has to hold on all three screens, and three copies is three chances for
 * one to be dropped.
 */
export default function RevenueLayout() {
  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div className="flex items-start gap-3 bg-amber-400/10 border border-amber-400/40 rounded-lg px-4 py-2.5">
        <FlaskConical className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
        <p className="text-amber-800 text-xs">
          <span className="font-semibold">Sample data — not live.</span>{' '}
          Every number on this tab is fabricated. Revenue recognition still runs against Airtable
          and has no API behind it yet; this is a mockup of the eventual report, here so its shape
          can be reviewed before it is built. Nothing here can be trusted, exported, or acted on.
        </p>
      </div>

      <div>
        <h1 className="text-xl font-semibold text-slate-900">Revenue</h1>
        <p className="text-sm text-slate-600 mt-0.5">
          Recognised revenue by month — what was earned, as distinct from what was invoiced.
        </p>
      </div>

      <div className="border-b border-slate-200 flex gap-1">
        {TABS.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                isActive
                  ? 'border-cyan-500 text-cyan-600'
                  : 'border-transparent text-slate-600 hover:text-slate-800'
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </div>

      <Outlet />
    </div>
  );
}
