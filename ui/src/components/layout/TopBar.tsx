import { User, Circle, LogOut } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { listAgents } from '../../api';
import { useAuth } from '../../auth/AuthProvider';

export default function TopBar() {
  const { data: agents, isError, isFetched } = useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
    refetchInterval: 30_000,
  });

  const { session, signOut } = useAuth();
  const navigate = useNavigate();

  const activeCount = isFetched ? (agents?.filter((a) => a.is_active).length ?? 0) : '—';
  const operational = !isError;
  const displayName = session?.user?.email ?? 'Signed in';

  async function onSignOut() {
    await signOut();
    navigate('/login', { replace: true });
  }

  return (
    <header className="h-12 flex items-center justify-between px-5 border-b border-slate-800 bg-slate-950/80 backdrop-blur flex-shrink-0">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Circle className={`w-2 h-2 fill-current ${operational ? 'text-emerald-400' : 'text-red-400'}`} />
        <span className={`font-medium ${operational ? 'text-emerald-400' : 'text-red-400'}`}>
          {operational ? 'System operational' : 'System degraded'}
        </span>
        <span className="text-slate-600">·</span>
        <span>{activeCount} agents active</span>
      </div>
      <div className="flex items-center gap-3 text-sm text-slate-400">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center">
            <User className="w-3.5 h-3.5" />
          </div>
          <span>{displayName}</span>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          title="Sign out"
          className="p-1.5 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
