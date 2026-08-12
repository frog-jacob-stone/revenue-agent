import { useEffect, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Loader2, Info } from 'lucide-react';
import { getBillingSettings, updateBillingSettings } from '../../api';

const TOKENS = [
  ['{client_name}', 'the Harvest client name'],
  ['{period_label}', 'e.g. "July 2026" — monthly invoices only'],
  ['{draw_description}', 'the milestone name — draws only'],
  ['{draw_number}', 'position in the contract schedule — draws only'],
  ['{draw_count}', 'length of that schedule, for "draw 2 of 5"'],
] as const;

/**
 * Settings → Billing.
 *
 * One setting so far, and it exists because of a Harvest limitation worth stating
 * on the screen itself: the default invoice notes configured in Harvest apply
 * only to invoices created through Harvest's own UI. Its API neither applies them
 * to an API-created invoice nor exposes them for reading, so the text has to be
 * stored here and sent on every invoice. The first live draw went out with blank
 * notes because of exactly this.
 *
 * That means this is a second copy of something Harvest also stores. The two can
 * drift and nothing can detect it — hence the note in the UI rather than only in
 * a comment.
 */
export default function Billing() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['billing-settings'],
    queryFn: getBillingSettings,
  });

  const [notes, setNotes] = useState('');
  // Seeded once the fetch lands. Keyed on the server value so a save elsewhere
  // does not silently overwrite what is being typed here.
  useEffect(() => {
    if (data) setNotes(data.default_invoice_notes);
  }, [data?.default_invoice_notes]);

  const save = useMutation({
    mutationFn: () => updateBillingSettings({ default_invoice_notes: notes }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['billing-settings'], updated);
      // Every future invoice reads this, so anything showing a computed invoice
      // is now stale.
      queryClient.invalidateQueries({ queryKey: ['draw-preview'] });
    },
  });

  const dirty = data != null && notes !== data.default_invoice_notes;

  if (isLoading) {
    return (
      <div className="px-6 max-w-3xl mx-auto">
        <p className="text-sm text-slate-500 animate-pulse">Loading settings…</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-6 max-w-3xl mx-auto">
        <p className="text-sm text-red-700">{(error as Error).message}</p>
      </div>
    );
  }

  return (
    <div className="px-6 pb-10 max-w-3xl mx-auto space-y-5">
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">Default invoice notes</h2>
          <p className="text-xs text-slate-600 mt-1 leading-relaxed">
            Put on every invoice this system creates, unless the billing group has its own notes
            template — a group's template replaces this rather than adding to it.
          </p>
        </div>

        <div className="flex items-start gap-2 bg-amber-400/10 border border-amber-400/40 rounded-lg px-3 py-2.5">
          <Info className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
          <p className="text-xs text-amber-800 leading-relaxed">
            Harvest will not do this for you. The default notes you set inside Harvest apply only
            to invoices created in Harvest's own UI — its API neither applies them nor lets us read
            them. So this is a <span className="font-medium">second copy</span> of that text: if
            you change one, change the other. Leave it blank and invoices go out with no notes,
            which is what happened to the first one.
          </p>
        </div>

        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={8}
          spellCheck
          placeholder={'Remit to:\nFrogslayer LLC\n…\n\nQuestions: ar@frogslayer.com'}
          className="w-full px-3 py-2 text-sm font-mono border border-slate-300 rounded-lg focus:outline-none focus:ring-1 focus:ring-cyan-500 leading-relaxed"
        />

        <div>
          <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium mb-1.5">
            Available tokens
          </p>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
            {TOKENS.map(([token, meaning]) => (
              <div key={token} className="flex items-baseline gap-2 text-xs">
                <dt>
                  <code className="text-cyan-700">{token}</code>
                </dt>
                <dd className="text-slate-500">{meaning}</dd>
              </div>
            ))}
          </dl>
          <p className="text-[11px] text-slate-400 mt-1.5">
            A token with nothing to say renders empty — <code>{'{draw_number}'}</code> on a
            monthly invoice is blank, not literal. Anything misspelled is left as-is, so it shows
            up on the invoice rather than vanishing silently.
          </p>
        </div>

        {save.error && (
          <p className="text-xs text-red-700">{(save.error as Error).message}</p>
        )}

        <div className="flex items-center gap-3 pt-1 border-t border-slate-200">
          <p className="text-[11px] text-slate-500">
            Takes effect on the next invoice created. Nothing already in Harvest changes.
          </p>
          {save.isSuccess && !dirty && (
            <span className="flex items-center gap-1 text-xs text-emerald-700">
              <Check className="w-3.5 h-3.5" /> Saved
            </span>
          )}
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="ml-auto mt-3 flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/10 border border-cyan-500/50 text-cyan-700 hover:bg-cyan-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {save.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {dirty ? 'Save' : 'Saved'}
          </button>
        </div>
      </div>

      <p className="text-[11px] text-slate-400 leading-relaxed">
        Harvest credentials and your Harvest web address are deployment configuration, not
        settings — they live in environment variables and are never served to this page.
      </p>
    </div>
  );
}
