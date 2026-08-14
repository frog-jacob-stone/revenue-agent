import { NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  LayoutDashboard, Inbox, MessageSquare,
  Settings, ChevronLeft, ChevronRight, Zap, Receipt, TrendingUp,
  FolderKanban, FileSignature,
} from 'lucide-react';
import { getApprovals, getDraws } from '../../api';
import { drawsNeedingAttention } from '../../invoicing';

interface Props {
  collapsed: boolean;
  onToggle: () => void;
}

// Agents, Audit Log, and LLM Calls live as Settings tabs rather than top-level
// nav — see pages/Settings/SettingsLayout.tsx.
const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
  // Ordered by what the business asks about, widest first: revenue earned, the
  // work that earned it, what was billed for it, and the agreements behind it.
  // Revenue and Projects are mockup/placeholder — each screen says so itself.
  { to: '/revenue', label: 'Revenue', icon: TrendingUp },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/invoices', label: 'Invoices', icon: Receipt, drawsBadge: true },
  { to: '/contracts', label: 'Contracts', icon: FileSignature },
  { to: '/chat', label: 'Chat', icon: MessageSquare },
  { to: '/inbox', label: 'Approval Inbox', icon: Inbox, inboxBadge: true },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export default function Sidebar({ collapsed, onToggle }: Props) {
  const { data: pendingCount = 0 } = useQuery({
    queryKey: ['inbox-pending-count'],
    queryFn: async () => {
      const approvals = await getApprovals({ status: 'pending' });
      return approvals.length;
    },
    refetchInterval: 15_000,
  });

  // Draws ready to bill or past due. Shares the Invoices pages' cache entry, so
  // acting on a draw updates this without a second round trip. Uncollected
  // money is worth surfacing from wherever you happen to be.
  const { data: draws = [] } = useQuery({
    queryKey: ['billing-draws'],
    queryFn: () => getDraws(),
    refetchInterval: 60_000,
  });
  const drawCount = drawsNeedingAttention(draws);

  return (
    <aside
      className={`relative flex flex-col bg-white border-r border-slate-200 transition-all duration-200 ${collapsed ? 'w-16' : 'w-56'}`}
    >
      {/* Logo */}
      <div className={`flex items-center gap-2.5 px-4 py-4 border-b border-slate-200 ${collapsed ? 'justify-center px-0' : ''}`}>
        <div className="w-7 h-7 rounded-lg bg-cyan-500/20 border border-cyan-500/40 flex items-center justify-center flex-shrink-0">
          <Zap className="w-4 h-4 text-cyan-600" />
        </div>
        {!collapsed && (
          <span className="text-slate-900 font-semibold text-sm tracking-tight">Revenue Ops</span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
        {NAV.map(({ to, label, icon: Icon, inboxBadge, drawsBadge, exact }) => {
          const badge = inboxBadge ? pendingCount : drawsBadge ? drawCount : 0;
          return (
            <NavLink
              key={to}
              to={to}
              end={exact}
              className={({ isActive }) =>
                `flex items-center gap-3 px-2.5 py-2 rounded-lg text-sm font-medium transition-colors group relative ${
                  isActive
                    ? 'bg-cyan-500/15 text-cyan-600 border border-cyan-500/40'
                    : 'text-slate-600 hover:text-slate-800 hover:bg-slate-100'
                } ${collapsed ? 'justify-center' : ''}`
              }
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {!collapsed && <span className="flex-1 truncate">{label}</span>}
              {!collapsed && badge > 0 && (
                <span className="ml-auto bg-cyan-600 text-white text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center">
                  {badge}
                </span>
              )}
              {collapsed && badge > 0 && (
                <span className="absolute top-0.5 right-0.5 w-4 h-4 bg-cyan-600 text-white text-[9px] font-bold rounded-full flex items-center justify-center">
                  {badge}
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={onToggle}
        className="flex items-center justify-center h-10 border-t border-slate-200 text-slate-500 hover:text-slate-700 hover:bg-slate-100 transition-colors"
      >
        {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
      </button>
    </aside>
  );
}
