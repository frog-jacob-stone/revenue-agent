import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, Inbox, Bot, ScrollText, MessageSquare,
  BookOpen, BarChart3, Settings, ChevronLeft, ChevronRight,
  Zap,
} from 'lucide-react';
import { APPROVAL_ITEMS } from '../../mocks';

interface Props {
  collapsed: boolean;
  onToggle: () => void;
}

const pendingCount = APPROVAL_ITEMS.filter((i) => i.status === 'pending').length;

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
  { to: '/inbox', label: 'Approval Inbox', icon: Inbox, badge: pendingCount },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/audit', label: 'Audit Log', icon: ScrollText },
  { to: '/chat', label: 'Chat', icon: MessageSquare },
  { to: '/knowledge', label: 'Knowledge Base', icon: BookOpen },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: Settings },
];

export default function Sidebar({ collapsed, onToggle }: Props) {
  return (
    <aside
      className={`relative flex flex-col bg-slate-900 border-r border-slate-800 transition-all duration-200 ${collapsed ? 'w-16' : 'w-56'}`}
    >
      {/* Logo */}
      <div className={`flex items-center gap-2.5 px-4 py-4 border-b border-slate-800 ${collapsed ? 'justify-center px-0' : ''}`}>
        <div className="w-7 h-7 rounded-lg bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center flex-shrink-0">
          <Zap className="w-4 h-4 text-cyan-400" />
        </div>
        {!collapsed && (
          <span className="text-slate-100 font-semibold text-sm tracking-tight">Revenue Ops</span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
        {NAV.map(({ to, label, icon: Icon, badge, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className={({ isActive }) =>
              `flex items-center gap-3 px-2.5 py-2 rounded-lg text-sm font-medium transition-colors group relative ${
                isActive
                  ? 'bg-cyan-500/15 text-cyan-400 border border-cyan-500/20'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
              } ${collapsed ? 'justify-center' : ''}`
            }
          >
            <Icon className="w-4 h-4 flex-shrink-0" />
            {!collapsed && <span className="flex-1 truncate">{label}</span>}
            {!collapsed && badge != null && badge > 0 && (
              <span className="ml-auto bg-cyan-500 text-slate-900 text-[10px] font-bold rounded-full w-5 h-5 flex items-center justify-center">
                {badge}
              </span>
            )}
            {collapsed && badge != null && badge > 0 && (
              <span className="absolute top-0.5 right-0.5 w-4 h-4 bg-cyan-500 text-slate-900 text-[9px] font-bold rounded-full flex items-center justify-center">
                {badge}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={onToggle}
        className="flex items-center justify-center h-10 border-t border-slate-800 text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-colors"
      >
        {collapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
      </button>
    </aside>
  );
}
