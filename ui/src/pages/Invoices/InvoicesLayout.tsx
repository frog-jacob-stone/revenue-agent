import { NavLink, Outlet } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ShieldCheck } from 'lucide-react';
import { getDraws } from '../../api';
import { drawsNeedingAttention, drawIsOverdue } from '../../invoicing';

export default function InvoicesLayout() {
  // Same cache entry the Draws screen reads, so the badge and the list can
  // never disagree about what is waiting.
  const { data: draws = [] } = useQuery({
    queryKey: ['billing-draws'],
    queryFn: () => getDraws(),
  });

  // Ready to bill plus past due — the two things a human has to act on. Not the
  // tab's total, which would just be a count of rows.
  const waiting = drawsNeedingAttention(draws);
  const late = draws.some((d) => drawIsOverdue(d));

  // Order follows the pipeline: the two things that produce invoices (Draws,
  // Billing Runs), then the record of what they produced, then configuration.
  // `Drafted` covers output from both sources, so it reads as their shared
  // endpoint rather than belonging to either one. It is "Drafted", not "Billed":
  // this system pushes a draft to Harvest and stops, and the invoice is billed
  // when a human sends it from there — an event this system never observes.
  const TABS = [
    { to: '/invoices/draws', label: 'Draws', end: false, count: waiting, late },
    { to: '/invoices/runs', label: 'Billing Runs', end: false, count: 0, late: false },
    { to: '/invoices/drafted', label: 'Drafted', end: false, count: 0, late: false },
    { to: '/invoices/groups', label: 'Billing Groups', end: false, count: 0, late: false },
  ];

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div className="flex items-center gap-3 bg-emerald-500/10 border border-emerald-500/40 rounded-lg px-4 py-2.5">
        <ShieldCheck className="w-4 h-4 text-emerald-600 flex-shrink-0" />
        <p className="text-emerald-700/90 text-xs">
          <span className="font-semibold">Drafts only, never sent.</span>{' '}
          A confirmed draw creates a Harvest <span className="font-medium">draft</span> invoice and
          stops; the system cannot send, delete, or modify one — you send it from Harvest. Monthly
          runs are still plan-only: their draft creation is not built yet and those controls are
          disabled.
        </p>
      </div>

      <div>
        <h1 className="text-xl font-semibold text-slate-900">Invoices</h1>
        <p className="text-sm text-slate-600 mt-0.5">
          Harvest draft invoices — draws drafted one at a time as delivery is confirmed,
          everything else on the monthly run.
        </p>
      </div>

      <div className="border-b border-slate-200 flex gap-1">
        {TABS.map(({ to, label, end, count, late }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
                isActive
                  ? 'border-cyan-500 text-cyan-600'
                  : 'border-transparent text-slate-600 hover:text-slate-800'
              }`
            }
          >
            {label}
            {/* Amber the moment anything is late — a count that reads as
                healthy when half of it is overdue would be worse than none. */}
            {count > 0 && (
              <span className={`inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full border text-[10px] font-semibold ${
                late
                  ? 'bg-amber-400/15 border-amber-400/50 text-amber-700'
                  : 'bg-emerald-500/15 border-emerald-500/40 text-emerald-700'
              }`}>
                {count}
              </span>
            )}
          </NavLink>
        ))}
      </div>

      <Outlet />
    </div>
  );
}
