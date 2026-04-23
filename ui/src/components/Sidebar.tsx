import { useQuery } from '@tanstack/react-query';
import type { NavTab, Action } from '../types';
import { getActions } from '../api';

interface Props {
  activeTab: NavTab;
  setActiveTab: (tab: NavTab) => void;
}

interface NavItem {
  tab: NavTab;
  label: string;
  status: string;
}

const NAV_ITEMS: NavItem[] = [
  { tab: 'pending', label: 'Pending', status: 'proposed' },
  { tab: 'approved', label: 'Approved', status: 'approved' },
  { tab: 'rejected', label: 'Rejected', status: 'rejected' },
  { tab: 'all', label: 'All', status: 'all' },
];

function useCount(status: string, poll: boolean) {
  return useQuery<Action[], Error, number>({
    queryKey: ['actions', status],
    queryFn: () => getActions(status),
    select: (data) => data.length,
    refetchInterval: poll ? 10_000 : false,
  });
}

export default function Sidebar({ activeTab, setActiveTab }: Props) {
  const pendingCount = useCount('proposed', true);
  const approvedCount = useCount('approved', false);
  const rejectedCount = useCount('rejected', false);
  const allCount = useCount('all', false);

  const counts: Record<NavTab, number | undefined> = {
    pending: pendingCount.data,
    approved: approvedCount.data,
    rejected: rejectedCount.data,
    all: allCount.data,
  };

  return (
    <aside className="w-44 shrink-0 border-r border-gray-200 bg-white flex flex-col">
      <div className="px-4 py-3 border-b border-gray-200">
        <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Approval Inbox
        </span>
      </div>
      <nav className="flex-1 py-2">
        {NAV_ITEMS.map(({ tab, label }) => {
          const count = counts[tab];
          const isActive = activeTab === tab;
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`w-full flex items-center justify-between px-4 py-2 text-sm text-left transition-colors ${
                isActive
                  ? 'bg-gray-100 text-gray-900 font-medium'
                  : 'text-gray-500 hover:bg-gray-50 hover:text-gray-900'
              }`}
            >
              <span>{label}</span>
              {count !== undefined && (
                <span
                  className={`text-xs rounded-full px-1.5 py-0.5 min-w-[1.25rem] text-center tabular-nums ${
                    tab === 'pending' && count > 0
                      ? 'bg-gray-900 text-white'
                      : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
