import { useQuery } from '@tanstack/react-query';
import type { Action } from '../types';
import { getActions } from '../api';
import ActionCard from './ActionCard';

interface Props {
  status: string;
  isPending: boolean;
}

export default function ActionList({ status, isPending }: Props) {
  const { data, isLoading, isError, error } = useQuery<Action[]>({
    queryKey: ['actions', status],
    queryFn: () => getActions(status),
    refetchInterval: isPending ? 10_000 : false,
  });

  if (isLoading) {
    return <div className="p-6 text-sm text-gray-400">Loading…</div>;
  }

  if (isError) {
    return (
      <div className="p-6 text-sm text-red-500">
        Failed to load: {(error as Error).message}
      </div>
    );
  }

  const actions = isPending ? [...(data ?? [])].reverse() : (data ?? []);
  const emptyMessage = isPending ? 'No pending approvals.' : 'No actions.';

  if (actions.length === 0) {
    return <div className="p-6 text-sm text-gray-400">{emptyMessage}</div>;
  }

  return (
    <div className="p-6 space-y-2 max-w-3xl">
      {actions.map((action) => (
        <ActionCard key={action.id} action={action} status={status} />
      ))}
    </div>
  );
}
