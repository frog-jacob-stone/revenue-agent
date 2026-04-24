import { User, Circle } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { listAgents } from '../../api';

export default function TopBar() {
  const { data: agents, isError, isFetched } = useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
    refetchInterval: 30_000,
  });

  const activeCount = isFetched ? (agents?.filter((a) => a.is_active).length ?? 0) : '—';
  const operational = !isError;

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
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <div className="w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center">
          <User className="w-3.5 h-3.5" />
        </div>
        <span>Jacob Stone</span>
      </div>
    </header>
  );
}
