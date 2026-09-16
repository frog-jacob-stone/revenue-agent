import { NavLink, Outlet } from 'react-router-dom';

const TABS = [
  { to: '/revenue/overview', label: 'Overview' },
  { to: '/revenue/runs', label: 'Runs' },
  { to: '/revenue/entries', label: 'Entries' },
  { to: '/revenue/setup', label: 'Setup' },
];

/**
 * Revenue — recognized revenue, read from Postgres.
 *
 * Live as of migration `0040`: `revenue_entries` is the ledger and
 * `GET /revenue/*` serves it. The mockup this replaced is gone, banner and all.
 *
 * The runner is live too: planning, reviewing and finalizing a month is
 * operator-initiated (ADR-0004), with no approval row because the operator
 * reads the exact entries on the run detail screen and the Finalize click is
 * the authorization. Finalizing writes nothing outside Postgres.
 *
 * The ledger stores each month's own amount; cumulative figures are summed at
 * read time. Every rollup under this tab therefore adds `recognized_amount`,
 * never `cumulative_recognized`, which already contains every earlier month.
 */
export default function RevenueLayout() {
  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
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
