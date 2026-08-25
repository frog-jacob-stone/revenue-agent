import { User, LogOut } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../../auth/AuthProvider';

export default function TopBar() {
  // Nothing lives on the left any more. This bar previously held an "N agents
  // active" count and, before that, a green/red "System operational" pill. Both
  // were removed for the same reason: they polled /agents every 30s to render a
  // number nobody acts on, and the pill could not distinguish "the system is
  // down" from "this one fetch failed". Agent state belongs on the Agents tab;
  // liveness is the platform's job (/healthz and /readyz drive the Container
  // Apps probes).
  const { session, signOut } = useAuth();
  const navigate = useNavigate();

  const displayName = session?.user?.email ?? 'Signed in';

  async function onSignOut() {
    await signOut();
    navigate('/login', { replace: true });
  }

  return (
    <header className="h-12 flex items-center justify-end px-5 border-b border-slate-200 bg-white/80 backdrop-blur flex-shrink-0">
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
