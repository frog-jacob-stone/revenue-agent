import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Loader2, Save } from 'lucide-react';
import {
  createContractClient,
  getContractClients,
  getTmFields,
  updateContractClient,
  type ContractClientInput,
} from '../../api';

/**
 * Contracts → Saved clients → add / edit.
 *
 * Labels come from `GET /contracts/tm/fields` rather than being written here,
 * so the form asks for a field by the same name the contract calls it. The
 * fields shown are exactly the `source: 'client'` ones, which is what keeps
 * this screen from turning into a CRM record by accretion: a field appears here
 * only because a contract template has a blank for it.
 */
const EMPTY: ContractClientInput = {
  legal_entity_name: '',
  address_line1: '',
  city: '',
  state: '',
  postal_code: '',
  signatory_name: '',
  msa_effective_date: '',
};

export default function ClientForm() {
  const { clientId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const editing = Boolean(clientId);

  const [values, setValues] = useState<ContractClientInput>(EMPTY);
  const [error, setError] = useState<string | null>(null);

  const { data: fields = [] } = useQuery({
    queryKey: ['contract-tm-fields'],
    queryFn: getTmFields,
    staleTime: Infinity,
  });

  // Reads the list rather than a by-id endpoint: the list is already cached
  // from the screen you navigated in from, so the form renders populated
  // instead of blank-then-filled.
  const { data: clients = [], isLoading } = useQuery({
    queryKey: ['contract-clients'],
    queryFn: getContractClients,
  });

  const existing = clients.find((c) => c.id === clientId);

  useEffect(() => {
    if (!existing) return;
    setValues({
      legal_entity_name: existing.legal_entity_name,
      address_line1: existing.address_line1 ?? '',
      city: existing.city ?? '',
      state: existing.state ?? '',
      postal_code: existing.postal_code ?? '',
      signatory_name: existing.signatory_name ?? '',
      msa_effective_date: existing.msa_effective_date ?? '',
    });
  }, [existing]);

  const save = useMutation({
    mutationFn: () => {
      // Empty string, not null: PATCH treats `""` as "clear this" and null as
      // "leave alone", and a field the operator emptied on screen must clear.
      const body = Object.fromEntries(
        Object.entries(values).map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v]),
      ) as unknown as ContractClientInput;
      return editing && clientId
        ? updateContractClient(clientId, body)
        : createContractClient(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contract-clients'] });
      navigate('/contracts/clients');
    },
    onError: (e) => setError((e as Error).message),
  });

  const clientFields = fields.filter((f) => f.source === 'client');

  if (editing && isLoading) {
    return <p className="text-sm text-slate-500 animate-pulse">Loading client…</p>;
  }
  if (editing && !existing) {
    return (
      <div className="space-y-3">
        <p className="text-sm text-red-700">No such saved client.</p>
        <Link to="/contracts/clients" className="text-sm text-cyan-700 hover:underline">
          Back to saved clients
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4 pb-10">
      <Link
        to="/contracts/clients"
        className="inline-flex items-center gap-1.5 text-xs text-slate-600 hover:text-slate-900"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        Saved clients
      </Link>

      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            {editing ? 'Edit saved client' : 'Add a saved client'}
          </h2>
          <p className="text-xs text-slate-600 mt-1">
            Only the legal entity name is required. Anything left empty appears in a draft as a
            highlighted review marker rather than a silent gap, so a half-known prospect is worth
            saving.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {clientFields.map((field) => {
            const key = field.client_field;
            if (!key) return null;
            const required = key === 'legal_entity_name';
            return (
              <label key={field.tag} className="block">
                <span className="block text-[11px] uppercase tracking-wide font-medium text-slate-500 mb-1">
                  {field.label}
                  {required && <span className="text-red-600 ml-0.5">*</span>}
                </span>
                <input
                  value={(values[key] as string) ?? ''}
                  onChange={(e) => setValues({ ...values, [key]: e.target.value })}
                  maxLength={200}
                  className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-500"
                />
              </label>
            );
          })}
        </div>

        {error && <p className="text-xs text-red-700">{error}</p>}

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={() => {
              setError(null);
              save.mutate();
            }}
            disabled={!values.legal_entity_name.trim() || save.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-cyan-500/10 border border-cyan-500/50 text-cyan-700 hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {save.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {editing ? 'Save changes' : 'Add client'}
          </button>
          {!values.legal_entity_name.trim() && (
            <p className="text-xs text-slate-500">A legal entity name is required.</p>
          )}
        </div>
      </div>
    </div>
  );
}
