import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { deleteContractClient, getContractClients } from '../../api';
import EmptyState from '../../components/shared/EmptyState';

/**
 * Contracts → Saved clients.
 *
 * The repository that makes the second contract for a client faster than the
 * first. Identity only — entity name, address, signatory — because that is
 * what a contract preamble and signature block need and this is not a CRM.
 *
 * A row here has no relationship to a Harvest client and no `harvest_client_id`
 * to join on. That is deliberate: the case this exists for is a prospect who
 * is being sent a contract precisely because they are not a client yet.
 */
export default function ClientList() {
  const queryClient = useQueryClient();

  const { data: clients = [], isLoading, error } = useQuery({
    queryKey: ['contract-clients'],
    queryFn: getContractClients,
  });

  const remove = useMutation({
    mutationFn: deleteContractClient,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['contract-clients'] }),
  });

  if (isLoading) {
    return <p className="text-sm text-slate-500 animate-pulse">Loading saved clients…</p>;
  }
  if (error) {
    return <p className="text-sm text-red-700">{(error as Error).message}</p>;
  }

  return (
    <div className="space-y-4 pb-10">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-600">
          {clients.length} saved client{clients.length === 1 ? '' : 's'}. Only the legal entity
          name is required — anything missing shows as a review marker in the draft.
        </p>
        <Link
          to="/contracts/clients/new"
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium bg-cyan-500/10 border border-cyan-500/50 text-cyan-700 hover:bg-cyan-500/20 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Add client
        </Link>
      </div>

      {clients.length === 0 ? (
        <EmptyState
          title="No saved clients"
          description="Add one here, or tick “Save this client” when you generate a draft for a new one."
        />
      ) : (
        <div className="bg-white border border-slate-200 rounded-xl divide-y divide-slate-100">
          {clients.map((c) => (
            <div key={c.id} className="p-4 flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-slate-900">{c.legal_entity_name}</p>
                <p className="text-xs text-slate-600 mt-0.5">
                  {addressLine(c) || <span className="text-slate-400 italic">No address</span>}
                </p>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  {c.signatory_name ? `Signs: ${c.signatory_name}` : 'No signatory recorded'}
                </p>
              </div>
              <div className="flex items-center gap-1.5">
                <Link
                  to={`/contracts/clients/${c.id}/edit`}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  Edit
                </Link>
                <button
                  onClick={() => {
                    if (
                      window.confirm(
                        `Forget ${c.legal_entity_name}? Drafts already generated are unaffected — ` +
                          `this only removes the saved details.`,
                      )
                    ) {
                      remove.mutate(c.id);
                    }
                  }}
                  disabled={remove.isPending}
                  className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium border border-slate-300 text-slate-600 hover:bg-red-50 hover:text-red-700 hover:border-red-300 disabled:opacity-40 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {remove.error && (
        <p className="text-xs text-red-700">{(remove.error as Error).message}</p>
      )}
    </div>
  );
}

function addressLine(c: {
  address_line1: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
}): string {
  const cityState = [c.city, c.state].filter(Boolean).join(', ');
  return [c.address_line1, cityState, c.postal_code].filter(Boolean).join(' · ');
}
