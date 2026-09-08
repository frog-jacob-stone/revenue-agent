import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, Download, Loader2 } from 'lucide-react';
import {
  getContractClients,
  getTmFields,
  type ContractClientInput,
  type TmDraftRequest,
} from '../../api';
import { downloadJsonPost } from '../../lib/download';

/**
 * Contracts → New T&M draft.
 *
 * The form is driven by `GET /contracts/tm/fields` rather than a field list
 * written here, so the labels and ordering come from the same declaration the
 * renderer uses. A blank added to the Word template and declared in
 * `fields.py` appears here with no change to this file.
 *
 * The review panel below the fields is the load-bearing part of the screen and
 * the reason this is not just a form with a button: it names every field that
 * will land in the document as a highlighted `[REVIEW: …]` marker, *before*
 * the download. That is what makes leaving a term open a decision rather than
 * an accident, and it is ADR-0004's "the exact payload is shown before the
 * click" for a write whose payload is a document.
 */

// Every key present and empty, so each input is controlled from first render.
// The inputs themselves are generated from `GET /contracts/tm/fields`, so a new
// template blank needs no markup here — only a key in this object.
const EMPTY_CLIENT: ContractClientInput = {
  legal_entity_name: '',
  address_line1: '',
  city: '',
  state: '',
  postal_code: '',
  signatory_name: '',
  msa_effective_date: '',
};

type Mode = 'saved' | 'new';

export default function NewTM() {
  const queryClient = useQueryClient();

  const [mode, setMode] = useState<Mode>('saved');
  const [selectedId, setSelectedId] = useState('');
  const [inline, setInline] = useState<ContractClientInput>(EMPTY_CLIENT);
  const [saveClient, setSaveClient] = useState(true);
  const [engagement, setEngagement] = useState<Record<string, string>>({});

  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ filename: string; reviewFields: string[] } | null>(null);

  const { data: fields = [], isLoading: fieldsLoading, error: fieldsError } = useQuery({
    queryKey: ['contract-tm-fields'],
    queryFn: getTmFields,
    // The declaration only changes on deploy, so there is nothing to refetch.
    staleTime: Infinity,
  });

  const { data: clients = [] } = useQuery({
    queryKey: ['contract-clients'],
    queryFn: getContractClients,
  });

  const clientFields = fields.filter((f) => f.source === 'client');
  const engagementFields = fields.filter((f) => f.source === 'engagement');
  const selected = clients.find((c) => c.id === selectedId);

  /**
   * What each tag currently resolves to — the same merge the server does.
   *
   * Computed here so the review list is live as you type rather than a round
   * trip behind it. The server recomputes it authoritatively at generate time
   * and its answer wins; this is for the operator's eyes.
   */
  const resolved = useMemo(() => {
    const values: Record<string, string> = {};
    const source = mode === 'saved' ? selected : inline;
    for (const field of clientFields) {
      const key = field.client_field;
      const raw =
        key && source ? (source as unknown as Record<string, unknown>)[key] : '';
      values[field.tag] = typeof raw === 'string' ? raw : '';
    }
    for (const field of engagementFields) {
      values[field.tag] = engagement[field.tag] ?? '';
    }
    return values;
  }, [clientFields, engagementFields, mode, selected, inline, engagement]);

  const reviewLabels = fields
    .filter((f) => !(resolved[f.tag] ?? '').trim())
    .map((f) => f.label);

  const counterpartyReady =
    mode === 'saved' ? Boolean(selectedId) : Boolean(inline.legal_entity_name.trim());

  async function generate() {
    setGenerating(true);
    setError(null);
    setResult(null);

    const body: TmDraftRequest = {
      engagement: Object.fromEntries(
        engagementFields.map((f) => [f.tag, engagement[f.tag]?.trim() || null]),
      ),
      ...(mode === 'saved'
        ? { client_id: selectedId }
        : { client: trimmed(inline), save_client: saveClient }),
    };

    try {
      const outcome = await downloadJsonPost(
        '/contracts/tm/draft',
        body,
        'tm-agreement.docx',
      );
      setResult(outcome);
      if (mode === 'new' && saveClient) {
        // The counterparty is now in the repository.
        queryClient.invalidateQueries({ queryKey: ['contract-clients'] });
        setMode('saved');
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  }

  if (fieldsLoading) {
    return <p className="text-sm text-slate-500 animate-pulse">Loading template fields…</p>;
  }
  if (fieldsError) {
    return <p className="text-sm text-red-700">{(fieldsError as Error).message}</p>;
  }

  return (
    <div className="space-y-5 pb-10">
      {/* ── Counterparty ─────────────────────────────────────────────────── */}
      <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Client</h2>
          <p className="text-xs text-slate-600 mt-1">
            Pick a saved client, or enter a new one — these details go in the preamble and the
            signature block.
          </p>
        </div>

        <div className="flex gap-2">
          {(['saved', 'new'] as Mode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
                mode === m
                  ? 'bg-cyan-500/10 border-cyan-500/50 text-cyan-700'
                  : 'border-slate-300 text-slate-600 hover:bg-slate-50'
              }`}
            >
              {m === 'saved' ? 'Saved client' : 'New client'}
            </button>
          ))}
        </div>

        {mode === 'saved' ? (
          <div className="space-y-2">
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg bg-white focus:outline-none focus:ring-1 focus:ring-cyan-500"
            >
              <option value="">Select a client…</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.legal_entity_name}
                </option>
              ))}
            </select>

            {clients.length === 0 && (
              <p className="text-xs text-slate-500">
                No saved clients yet — use <span className="font-medium">New client</span>, or
                add one under{' '}
                <Link to="/contracts/clients" className="text-cyan-700 hover:underline">
                  Saved clients
                </Link>
                .
              </p>
            )}

            {selected && (
              <div className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5 space-y-1">
                {clientFields.map((field) => (
                  <ResolvedRow
                    key={field.tag}
                    label={field.label}
                    value={resolved[field.tag]}
                  />
                ))}
                <p className="text-[11px] text-slate-400 pt-1">
                  Read from the saved record.{' '}
                  <Link
                    to={`/contracts/clients/${selected.id}/edit`}
                    className="text-cyan-700 hover:underline"
                  >
                    Edit this client
                  </Link>
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {clientFields.map((field) => {
                const key = field.client_field;
                if (!key) return null;
                const required = key === 'legal_entity_name';
                return (
                  <Field
                    key={field.tag}
                    label={field.label}
                    required={required}
                    value={(inline[key] as string) ?? ''}
                    onChange={(v) => setInline({ ...inline, [key]: v })}
                  />
                );
              })}
            </div>

            <label className="flex items-center gap-2 text-xs text-slate-700">
              <input
                type="checkbox"
                checked={saveClient}
                onChange={(e) => setSaveClient(e.target.checked)}
                className="rounded border-slate-300 text-cyan-600 focus:ring-cyan-500"
              />
              Save this client so the next contract for them needs no retyping
            </label>
          </div>
        )}
      </section>

      {/* ── Engagement ───────────────────────────────────────────────────── */}
      <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">This engagement</h2>
          <p className="text-xs text-slate-600 mt-1">
            Specific to this agreement, so nothing is remembered against the client. Rates and
            role definitions are fixed in the template and are not asked for here.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {engagementFields.map((field) => (
            <Field
              key={field.tag}
              label={field.label}
              value={engagement[field.tag] ?? ''}
              onChange={(v) => setEngagement({ ...engagement, [field.tag]: v })}
            />
          ))}
        </div>
      </section>

      {/* ── Review, then generate ────────────────────────────────────────── */}
      <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        {reviewLabels.length > 0 ? (
          <div className="flex items-start gap-2 bg-amber-400/10 border border-amber-400/40 rounded-lg px-3 py-2.5">
            <AlertTriangle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
            <div className="text-xs text-amber-800 leading-relaxed">
              <p className="font-semibold">
                {reviewLabels.length} field{reviewLabels.length === 1 ? '' : 's'} will be marked
                for review
              </p>
              <p className="mt-1">
                Each appears in the document as a highlighted marker you can find with Ctrl+F.
                That is fine for a draft you intend to negotiate — just know what is open:
              </p>
              <ul className="mt-1.5 space-y-0.5">
                {reviewLabels.map((label) => (
                  <li key={label} className="font-mono text-[11px]">
                    [REVIEW: {label}]
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/40 rounded-lg px-3 py-2.5">
            <Check className="w-4 h-4 text-emerald-600 flex-shrink-0" />
            <p className="text-xs text-emerald-800">
              Every field is filled — the draft will contain no review markers.
            </p>
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            onClick={generate}
            disabled={!counterpartyReady || generating}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-cyan-500/10 border border-cyan-500/50 text-cyan-700 hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {generating ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}
            Generate &amp; download
          </button>
          {!counterpartyReady && (
            <p className="text-xs text-slate-500">
              {mode === 'saved' ? 'Select a client first.' : 'A legal entity name is required.'}
            </p>
          )}
        </div>

        {error && <p className="text-xs text-red-700">{error}</p>}

        {result && (
          <div className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5 space-y-1">
            <p className="text-xs text-slate-800">
              Downloaded <span className="font-mono">{result.filename}</span>
            </p>
            {result.reviewFields.length > 0 && (
              <p className="text-[11px] text-slate-600">
                Marked for review: {result.reviewFields.join(', ')}
              </p>
            )}
          </div>
        )}
      </section>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        The generated file is not stored — it downloads and that is the only copy. Every
        generation is recorded in the audit log with the values used and the fields left open.
      </p>
    </div>
  );
}

function trimmed(input: ContractClientInput): ContractClientInput {
  const out = { ...input } as Record<string, string | null | undefined>;
  for (const [key, value] of Object.entries(out)) {
    if (typeof value === 'string') out[key] = value.trim() || null;
  }
  // The one required field stays a string.
  out.legal_entity_name = input.legal_entity_name.trim();
  return out as unknown as ContractClientInput;
}

function Field({
  label,
  value,
  onChange,
  required = false,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
}) {
  return (
    <label className="block">
      <span className="block text-[11px] uppercase tracking-wide font-medium text-slate-500 mb-1">
        {label}
        {required && <span className="text-red-600 ml-0.5">*</span>}
      </span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        maxLength={200}
        className="w-full px-3 py-2 text-sm border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-500"
      />
    </label>
  );
}

function ResolvedRow({ label, value }: { label: string; value: string }) {
  const empty = !value.trim();
  return (
    <p className="text-xs flex gap-2">
      <span className="text-slate-500 min-w-[9rem]">{label}</span>
      <span className={empty ? 'text-amber-700 font-mono' : 'text-slate-900'}>
        {empty ? `[REVIEW: ${label}]` : value}
      </span>
    </p>
  );
}
