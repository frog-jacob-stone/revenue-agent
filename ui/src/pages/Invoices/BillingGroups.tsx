import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Search, ChevronRight, Plus } from 'lucide-react';
import { BillingTypeChip, TimingChip } from './components/Bits';
import HealthStrip from './components/HealthStrip';
import { getBillingGroups } from '../../api';
import type { BillingType } from '../../invoicing';

export default function BillingGroups() {
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [type, setType] = useState<BillingType | ''>('');
  const [includeInactive, setIncludeInactive] = useState(false);

  const { data: groups = [], isLoading } = useQuery({
    queryKey: ['billing-groups', type, includeInactive],
    queryFn: () => getBillingGroups({
      billing_type: type || undefined,
      include_inactive: includeInactive,
    }),
  });

  // A client may own any number of billing groups — one per invoice it should
  // receive. Surface that in the table so the split is legible.
  const groupsPerClient = groups.reduce<Record<number, string[]>>((acc, g) => {
    (acc[g.harvest_client_id] ??= []).push(g.id);
    return acc;
  }, {});

  const rows = groups.filter((g) => {
    if (!q) return true;
    const hay = `${g.name} ${g.harvest_client_name ?? ''} ${g.projects.map((p) => p.harvest_project_name).join(' ')}`;
    return hay.toLowerCase().includes(q.toLowerCase());
  });

  return (
    <div className="space-y-4">
      <HealthStrip />

      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 bg-white border border-slate-200 rounded-lg px-3 py-1.5 flex-1 max-w-sm">
          <Search className="w-3.5 h-3.5 text-slate-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search groups, clients, projects…"
            className="bg-transparent text-sm text-slate-800 placeholder:text-slate-400 outline-none flex-1"
          />
        </div>
        <select
          value={type}
          onChange={(e) => setType(e.target.value as BillingType | '')}
          className="bg-slate-100 border border-slate-300 text-slate-700 text-xs rounded px-2 py-1.5"
        >
          <option value="">All billing types</option>
          <option value="time_and_materials">Time &amp; materials</option>
          <option value="fixed_fee_schedule">Fixed fee schedule</option>
          <option value="recurring_monthly">Recurring monthly</option>
          <option value="manual">Manual</option>
        </select>
        <label className="flex items-center gap-1.5 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
            className="w-3.5 h-3.5 rounded accent-cyan-600"
          />
          Show inactive
        </label>
        <button
          onClick={() => navigate('/invoices/groups/new')}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-600 hover:bg-cyan-500/25 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          New billing group
        </button>
      </div>

      {/* Table */}
      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Billing group</th>
              <th className="text-left px-4 py-3 font-medium">Client</th>
              <th className="text-left px-4 py-3 font-medium">Type</th>
              <th className="text-left px-4 py-3 font-medium">Timing</th>
              <th className="text-left px-4 py-3 font-medium">Terms</th>
              <th className="text-left px-4 py-3 font-medium">Projects</th>
              <th className="w-8 px-3 py-3" />
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-xs text-slate-500 animate-pulse">
                  Loading…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center">
                  {groups.length === 0 ? (
                    <>
                      <p className="text-sm text-slate-500">No billing groups configured yet.</p>
                      <p className="text-xs text-slate-400 mt-1">
                        Until a billable project is in a group, nothing invoices it.
                      </p>
                      <button
                        onClick={() => navigate('/invoices/groups/new')}
                        className="inline-flex items-center gap-2 mt-3 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-600 hover:bg-cyan-500/25 transition-colors"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Create the first one
                      </button>
                    </>
                  ) : (
                    <span className="text-sm text-slate-500">No billing groups match.</span>
                  )}
                </td>
              </tr>
            ) : rows.map((g, i) => (
              <tr
                key={g.id}
                onClick={() => navigate(`/invoices/groups/${g.id}`)}
                className={`hover:bg-slate-50 cursor-pointer transition-colors ${i < rows.length - 1 ? 'border-b border-slate-200' : ''}`}
              >
                <td className="px-4 py-3">
                  <span className="text-slate-900">{g.name}</span>
                  {!g.is_active && (
                    <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-400">inactive</span>
                  )}
                </td>
                <td className="px-4 py-3 text-xs">
                  <span className="text-slate-600">{g.harvest_client_name}</span>
                  {(groupsPerClient[g.harvest_client_id]?.length ?? 0) > 1 && (
                    <span
                      className="block text-[10px] text-violet-700/80 mt-0.5"
                      title="This client receives more than one invoice — one per billing group."
                    >
                      group {groupsPerClient[g.harvest_client_id].indexOf(g.id) + 1} of{' '}
                      {groupsPerClient[g.harvest_client_id].length}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3"><BillingTypeChip type={g.billing_type} /></td>
                <td className="px-4 py-3">
                  {g.billing_type === 'manual'
                    ? <span className="text-slate-400 text-xs">—</span>
                    : <TimingChip timing={g.billing_timing} />}
                </td>
                <td className="px-4 py-3 text-slate-600 text-xs">
                  {g.payment_term === 'custom' ? `custom · net ${g.custom_net_days}` : g.payment_term}
                </td>
                <td className="px-4 py-3 text-slate-600 text-xs">
                  {g.projects.length === 1
                    ? g.projects[0].harvest_project_name
                    : `${g.projects.length} projects`}
                </td>
                <td className="px-3 py-3 text-slate-400"><ChevronRight className="w-4 h-4" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-slate-400 leading-relaxed">
        A billing group is the unit that produces exactly one Harvest invoice. Every billable Harvest project must
        map to exactly one active group — including projects invoiced by hand, which map to a <code className="text-slate-600">manual</code> group
        so they stop raising <code className="text-slate-600">UNMAPPED_PROJECT</code> every month.
      </p>
    </div>
  );
}
