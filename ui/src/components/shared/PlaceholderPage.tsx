import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import StubBadge from './StubBadge';

interface Props {
  title: string;
  /** One line under the heading: what this tab will be for. */
  subtitle: string;
  icon: LucideIcon;
  /** What has to exist before this screen can be built. Honest, not aspirational. */
  children: ReactNode;
}

/**
 * A nav destination that exists before its feature does.
 *
 * Deliberately not `EmptyState` — that says "no items yet", which implies the
 * screen works and simply has nothing to show. These tabs have no backend, no
 * schema, and no design; saying "nothing to display" would be a lie a reviewer
 * could act on. Every claim here is that the feature is unbuilt.
 */
export default function PlaceholderPage({ title, subtitle, icon: Icon, children }: Props) {
  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">
          {title}
          <StubBadge />
        </h1>
        <p className="text-sm text-slate-600 mt-0.5">{subtitle}</p>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl px-6 py-14 text-center">
        <div className="w-12 h-12 rounded-xl bg-slate-100 border border-slate-200 flex items-center justify-center mx-auto">
          <Icon className="w-5 h-5 text-slate-400" />
        </div>
        <p className="text-slate-700 font-medium mt-4">Not built yet</p>
        <div className="text-sm text-slate-500 mt-2 max-w-xl mx-auto leading-relaxed space-y-2">
          {children}
        </div>
      </div>
    </div>
  );
}
