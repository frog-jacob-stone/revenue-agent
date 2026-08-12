import { User, LogOut } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { listAgents } from '../../api';
import { useAuth } from '../../auth/AuthProvider';

export default function TopBar() {
  // There used to be a green/red "System operational" pill here, derived from
  // whether this same query errored. It was not a health check — a slow network
  // or one 500 from /agents rendered "System degraded" across an otherwise fine
  // app, and nothing was actually being probed. A status indicator that cannot
  // distinguish "the system is down" from "this one fetch failed" is worse than
  // no indicator, because people trust it. Liveness is the platform's job
  // (/healthz and /readyz drive the Container Apps probes).
  const { data: agents, isFetched } = useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
    refetchInterval: 30_000,
  });

  const { session, signOut } = useAuth();
  const navigate = useNavigate();

  const activeCount = isFetched ? (agents?.filter((a) => a.is_active).length ?? 0) : '—';
  const displayName = session?.user?.email ?? 'Signed in';

  async function onSignOut() {
    await signOut();
    navigate('/login', { replace: true });
  }

  return (
    <header className="h-12 flex items-center justify-between px-5 border-b border-slate-200 bg-white/80 backdrop-blur flex-shrink-0">
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <span>{activeCount} agents active</span>
      </div>
      <div className="flex items-center gap-3 text-sm text-slate-600">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-slate-200 flex items-center justify-center">
            <User className="w-3.5 h-3.5" />
          </div>
          <span>{displayName}</span>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          title="Sign out"
          className="p-1.5 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-100"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
