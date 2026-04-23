import { User, Circle } from 'lucide-react';

export default function TopBar() {
  return (
    <header className="h-12 flex items-center justify-between px-5 border-b border-slate-800 bg-slate-950/80 backdrop-blur flex-shrink-0">
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Circle className="w-2 h-2 fill-emerald-400 text-emerald-400" />
        <span className="text-emerald-400 font-medium">System operational</span>
        <span className="text-slate-600">·</span>
        <span>5 agents active</span>
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
