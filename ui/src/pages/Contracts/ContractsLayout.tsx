import { NavLink, Outlet } from 'react-router-dom';
import { FileText } from 'lucide-react';

/**
 * Contracts — draft generation and the saved-client repository.
 *
 * Two tabs, in the order the work happens: produce a draft, and maintain the
 * identity records that make producing the next one faster.
 *
 * Deliberately narrow. This does not model a signed contract, its commercial
 * terms, or its lifecycle — payment terms and draw schedules live in billing
 * group config and `contracted_fees` lives in the Airtable revenue ledger, and
 * nothing here tries to unify them. This is a document generator.
 */
const TABS = [
  { to: '/contracts/new', label: 'New T&M draft' },
  { to: '/contracts/clients', label: 'Saved clients' },
];

export default function ContractsLayout() {
  return (
    <div className="p-6 space-y-5 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 bg-cyan-500/10 border border-cyan-500/40 rounded-lg px-4 py-2.5">
        <FileText className="w-4 h-4 text-cyan-600 flex-shrink-0" />
        <p className="text-cyan-800/90 text-xs">
          <span className="font-semibold">Drafts only.</span>{' '}
          Generating fills the blanks in the Word template and downloads it — nothing is stored,
          sent, or filed anywhere. Anything you leave empty appears in the document as a
          highlighted <code className="font-mono">[REVIEW: …]</code> marker rather than a silent
          gap.
        </p>
      </div>

      <div>
        <h1 className="text-xl font-semibold text-slate-900">Contracts</h1>
        <p className="text-sm text-slate-600 mt-0.5">
          Draft a time &amp; materials agreement from the standard template, and keep the client
          details you would otherwise retype.
        </p>
      </div>

      <div className="border-b border-slate-200 flex gap-1">
        {TABS.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
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
