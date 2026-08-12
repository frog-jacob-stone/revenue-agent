import { NavLink, Outlet } from 'react-router-dom';

const TABS = [
  { to: '/settings/billing', label: 'Billing', end: false },
  { to: '/settings/agents', label: 'Agents', end: false },
  { to: '/settings/audit', label: 'Audit Log', end: false },
  { to: '/settings/llm-calls', label: 'LLM Calls', end: false },
];

export default function SettingsLayout() {
  return (
    <div className="pt-6 space-y-5">
      <div className="px-6 space-y-4 max-w-7xl mx-auto">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Settings</h1>
          <p className="text-sm text-slate-600 mt-0.5">
            Integrations, agents, and system observability
          </p>
        </div>

        <div className="border-b border-slate-200 flex gap-1 overflow-x-auto">
          {TABS.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors ${
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
      </div>

      <Outlet />
    </div>
  );
}
