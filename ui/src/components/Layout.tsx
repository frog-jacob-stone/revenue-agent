import type { NavTab } from '../types';
import Sidebar from './Sidebar';
import ActionList from './ActionList';

interface Props {
  activeTab: NavTab;
  setActiveTab: (tab: NavTab) => void;
}

const TAB_STATUS: Record<NavTab, string> = {
  pending: 'proposed',
  approved: 'approved',
  rejected: 'rejected',
  all: 'all',
};

export default function Layout({ activeTab, setActiveTab }: Props) {
  return (
    <div className="flex h-screen bg-gray-50 text-gray-900">
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />
      <main className="flex-1 overflow-auto">
        <ActionList
          status={TAB_STATUS[activeTab]}
          isPending={activeTab === 'pending'}
        />
      </main>
    </div>
  );
}
