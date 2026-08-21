import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Save, Loader2, AlertOctagon, Search, Lock } from 'lucide-react';
import {
  getHarvestClients, getHarvestProjects, getBillingGroup,
  createBillingGroup, updateBillingGroup,
} from '../../api';
import type { HarvestProjectOption } from '../../api';
import RecurringItemsEditor from './components/RecurringItemsEditor';
import DrawScheduleEditor from './components/DrawScheduleEditor';
import { money } from '../../invoicing';
import type {
  BillingGroupInput, BillingType, BillingTiming, PaymentTerm,
  SummaryType, ExpenseSummaryType, RecurringItemInput, DrawInput,
} from '../../invoicing';

const BILLING_TYPES: { value: BillingType; label: string; hint: string }[] = [
  { value: 'time_and_materials', label: 'Time & materials',
    hint: 'Line items come from uninvoiced time and expenses in Harvest.' },
  { value: 'fixed_fee_schedule', label: 'Fixed fee schedule',
    hint: 'A contract payment schedule. Each draw bills as its own invoice when you confirm delivery — never on the monthly run.' },
  { value: 'recurring_monthly', label: 'Recurring monthly',
    hint: 'The same line items every month — retainers, hosting, management fees. Can span several projects on one invoice.' },
  { value: 'manual', label: 'Manual',
    hint: 'Invoiced by hand. Produces nothing; exists to stop UNMAPPED_PROJECT firing every month.' },
];

const PAYMENT_TERMS: PaymentTerm[] = [
  'upon receipt', 'net 15', 'net 30', 'net 45', 'net 60', 'custom',
];

const EMPTY: BillingGroupInput = {
  name: '',
  harvest_client_id: 0,
  billing_type: 'time_and_materials',
  billing_timing: 'arrears',
  // `custom` is the house default: the due date is computed here rather than
  // by Harvest. Net 10 is the standard term; it also keeps the form valid out
  // of the box, since the check constraint refuses a custom term with no net
  // days.
  payment_term: 'custom',
  custom_net_days: 10,
  time_summary_type: 'people',
  include_expenses: false,
  expense_summary_type: 'category',
  attach_receipts: false,
  subject_template: '{client_name} — {period_label}',
  notes_template: null,
  purchase_order: null,
  requires_purchase_order: false,
  currency: null,
  projects: [],
};

function Label({ children, hint }: { children: React.ReactNode; hint?: string }) {
  return (
    <div className="mb-1">
      <span className="text-[11px] text-slate-600 uppercase tracking-wide font-medium">{children}</span>
      {hint && <span className="block text-[11px] text-slate-400 normal-case mt-0.5">{hint}</span>}
    </div>
  );
}

const inputCls =
  'w-full bg-slate-100 border border-slate-300 rounded px-2.5 py-1.5 text-sm text-slate-800 ' +
  'placeholder:text-slate-400 outline-none focus:border-cyan-500/50';

export default function GroupForm() {
  const { groupId } = useParams();
  const isEdit = !!groupId;
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [form, setForm] = useState<BillingGroupInput>(EMPTY);
  const [picked, setPicked] = useState<number[]>([]);
  const [lineItems, setLineItems] = useState<RecurringItemInput[]>([]);
  const [draws, setDraws] = useState<DrawInput[]>([]);
  const [projectQuery, setProjectQuery] = useState('');
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof BillingGroupInput>(k: K, v: BillingGroupInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  // Excluded clients are not offered for new config, but an existing group
  // must keep rendering its own client even if it was excluded afterwards —
  // this select is editable, and a missing option would blank the field and
  // let a save wipe it. The flag is in the key because the two calls fetch
  // different sets; sharing one key would let either masquerade as the other.
  const { data: clients = [] } = useQuery({
    queryKey: ['harvest-clients', isEdit ? 'all' : 'selectable'],
    queryFn: () => getHarvestClients({ include_excluded: isEdit }),
  });

  const {
    data: existing, isLoading: loadingGroup, error: loadError,
  } = useQuery({
    queryKey: ['billing-group', groupId],
    queryFn: () => getBillingGroup(groupId!),
    enabled: isEdit,
  });

  // Hydrate once per loaded group. Guarded on the id rather than on `existing`
  // alone: react-query refetches on window focus, and re-running this would
  // silently throw away edits in progress.
  const hydrated = useRef<string | null>(null);
  useEffect(() => {
    if (!existing || hydrated.current === existing.id) return;
    hydrated.current = existing.id;
    setForm({
      name: existing.name,
      harvest_client_id: existing.harvest_client_id,
      billing_type: existing.billing_type,
      billing_timing: existing.billing_timing,
      payment_term: existing.payment_term,
      custom_net_days: existing.custom_net_days,
      time_summary_type: existing.time_summary_type,
      include_expenses: existing.include_expenses,
      expense_summary_type: existing.expense_summary_type,
      attach_receipts: existing.attach_receipts,
      subject_template: existing.subject_template,
      notes_template: existing.notes_template,
      purchase_order: existing.purchase_order,
      requires_purchase_order: existing.requires_purchase_order,
      currency: existing.currency,
    });
    setPicked(existing.projects.map((p) => p.harvest_project_id));
    // Line items keep their id for the same reason draws do: placeholder
    // resolutions are keyed on it, so dropping it here would make saving an
    // unrelated fee discard the amounts already entered for this month.
    setLineItems((existing.recurring_items ?? []).map((r) => ({
      id: r.id,
      harvest_project_id: r.harvest_project_id,
      description: r.description,
      quantity: r.quantity,
      unit_price: r.unit_price,
      kind: r.kind,
      is_placeholder: r.is_placeholder,
      sort_order: r.sort_order,
      effective_from: r.effective_from,
      effective_to: r.effective_to,
    })));
    // Draws keep their id: release state and billing history hang off it, so a
    // save must update in place rather than replace the set.
    setDraws((existing.schedule_items ?? []).map((d) => ({
      id: d.id,
      harvest_project_id: d.harvest_project_id,
      description: d.description,
      amount: d.amount,
      kind: d.kind,
      scheduled_date: d.scheduled_date,
      sequence: d.sequence,
      released_at: d.released_at,
      invoiced_run_id: d.invoiced_run_id,
      live_run_id: d.live_run_id,
    })));
  }, [existing]);

  const { data: projects = [] } = useQuery({
    // `groupId` already distinguishes the edit key from the create one, so the
    // include_excluded flag needs no separate entry.
    queryKey: ['harvest-projects', form.harvest_client_id, groupId],
    queryFn: () => getHarvestProjects({
      client_id: form.harvest_client_id,
      exclude_group_id: groupId,
      include_excluded: isEdit,
    }),
    enabled: form.harvest_client_id > 0,
  });

  // Switching client invalidates the picked projects — every project in a group
  // must belong to that group's client, or Harvest 422s at creation. This is a
  // consequence of the user's choice, so it lives in the change handler: as an
  // effect on `harvest_client_id` it also fired on the 0 → saved-client jump
  // during hydration, wiping the very projects the form had just loaded.
  const pickClient = (harvestClientId: number) => {
    if (harvestClientId === form.harvest_client_id) return;
    setForm((f) => ({ ...f, harvest_client_id: harvestClientId }));
    setPicked([]);
    setLineItems([]);
    setDraws([]);
  };

  const visibleProjects = useMemo(() => {
    if (!projectQuery) return projects;
    const q = projectQuery.toLowerCase();
    return projects.filter((p) => p.name.toLowerCase().includes(q));
  }, [projects, projectQuery]);

  const save = useMutation({
    mutationFn: async () => {
      const body: BillingGroupInput = {
        ...form,
        // Clear fields the chosen billing type doesn't use, so stale values
        // from a type switch never reach the payload builder.
        time_summary_type: form.billing_type === 'time_and_materials'
          ? form.time_summary_type : null,
        expense_summary_type: form.include_expenses ? form.expense_summary_type : null,
        custom_net_days: form.payment_term === 'custom' ? form.custom_net_days : null,
        projects: picked.map((id, i) => ({ harvest_project_id: id, sort_order: i + 1 })),
        // Only recurring groups carry line items; sending them for other types
        // would persist config the planner will never read.
        recurring_items: isRecurring
          ? lineItems.map((it, i) => ({ ...it, sort_order: i + 1 }))
          : [],
        // Same rule for draws: only fixed-fee groups carry a schedule.
        schedule_items: isFixedFee
          ? draws.map((d, i) => ({ ...d, sequence: i + 1 }))
          : [],
      };
      return isEdit ? updateBillingGroup(groupId!, body) : createBillingGroup(body);
    },
    onSuccess: (g) => {
      queryClient.invalidateQueries({ queryKey: ['billing-groups'] });
      queryClient.invalidateQueries({ queryKey: ['billing-health'] });
      queryClient.invalidateQueries({ queryKey: ['billing-group', g.id] });
      navigate(`/invoices/groups/${g.id}`);
    },
    onError: (e: Error) => setError(e.message),
  });

  const isTM = form.billing_type === 'time_and_materials';
  const isManual = form.billing_type === 'manual';
  const isRecurring = form.billing_type === 'recurring_monthly';
  const isFixedFee = form.billing_type === 'fixed_fee_schedule';

  // Line items can only target projects the group actually holds.
  const pickedProjects = picked
    .map((id) => projects.find((p) => p.harvest_id === id))
    .filter((p): p is HarvestProjectOption => !!p)
    .map((p) => ({ harvest_project_id: p.harvest_id, name: p.name }));
  const canSave =
    form.name.trim().length > 0 &&
    form.harvest_client_id > 0 &&
    picked.length > 0 &&
    (form.payment_term !== 'custom' || !!form.custom_net_days) &&
    (!isRecurring || lineItems.every((it) => it.description.trim().length > 0));

  // Never render the form over a group that hasn't loaded — an empty form in
  // edit mode looks like a group with no config, and saving it would be
  // destructive.
  if (isEdit && (loadingGroup || loadError || hydrated.current !== groupId)) {
    return (
      <div className="p-6 max-w-4xl mx-auto">
        {loadError ? (
          <p className="text-sm text-red-700">
            Could not load this billing group: {(loadError as Error).message}
          </p>
        ) : (
          <p className="text-sm text-slate-500 animate-pulse">Loading billing group…</p>
        )}
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5 max-w-4xl mx-auto">
      <button
        className="flex items-center gap-2 text-sm text-slate-600 hover:text-slate-800 transition-colors"
        onClick={() => navigate(isEdit ? `/invoices/groups/${groupId}` : '/invoices/groups')}
      >
        <ArrowLeft className="w-4 h-4" />
        {isEdit ? 'Back to group' : 'Back to Billing Groups'}
      </button>

      <div>
        <h1 className="text-xl font-semibold text-slate-900">
          {isEdit ? 'Edit billing group' : 'New billing group'}
        </h1>
        <p className="text-sm text-slate-600 mt-0.5">
          A billing group produces exactly one Harvest invoice. One client, one or more projects.
          A client can have as many groups as it should receive invoices.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-3 bg-red-500/10 border border-red-500/40 rounded-lg px-4 py-3">
          <AlertOctagon className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
          <p className="text-red-700 text-sm">{error}</p>
        </div>
      )}

      {/* Identity */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Identity</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Label hint="How it reads in the pre-flight, e.g. “Acme — Platform + Mobile”.">Name</Label>
            <input
              className={inputCls}
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="Client — what's on this invoice"
            />
          </div>
          <div>
            <Label>Harvest client</Label>
            <select
              className={inputCls}
              value={form.harvest_client_id || ''}
              onChange={(e) => pickClient(Number(e.target.value))}
            >
              <option value="">Select a client…</option>
              {clients.map((c) => (
                <option key={c.harvest_id} value={c.harvest_id}>
                  {c.name} ({c.billable_project_count} billable)
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Projects */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
            Projects ({picked.length} selected)
          </h2>
          {projects.length > 6 && (
            <div className="flex items-center gap-2 bg-slate-100 border border-slate-300 rounded px-2 py-1">
              <Search className="w-3 h-3 text-slate-500" />
              <input
                value={projectQuery}
                onChange={(e) => setProjectQuery(e.target.value)}
                placeholder="Filter…"
                className="bg-transparent text-xs text-slate-800 outline-none w-32"
              />
            </div>
          )}
        </div>

        {form.harvest_client_id === 0 ? (
          <p className="text-xs text-slate-400">Select a client first.</p>
        ) : visibleProjects.length === 0 ? (
          <p className="text-xs text-slate-400">
            No billable projects for this client in the Harvest snapshot.
          </p>
        ) : (
          <div className="space-y-1.5 max-h-80 overflow-y-auto">
            {visibleProjects.map((p: HarvestProjectOption) => {
              const claimed = !!p.billing_group_id;
              const checked = picked.includes(p.harvest_id);
              return (
                <label
                  key={p.harvest_id}
                  className={`flex items-center gap-3 border rounded-lg px-3 py-2 transition-colors ${
                    claimed
                      ? 'border-slate-200 bg-slate-50 cursor-not-allowed'
                      : checked
                      ? 'border-cyan-500/50 bg-cyan-500/5 cursor-pointer'
                      : 'border-slate-200 hover:border-slate-300 cursor-pointer'
                  }`}
                >
                  <input
                    type="checkbox"
                    disabled={claimed}
                    checked={checked}
                    onChange={() => setPicked((prev) =>
                      prev.includes(p.harvest_id)
                        ? prev.filter((x) => x !== p.harvest_id)
                        : [...prev, p.harvest_id],
                    )}
                    className="w-4 h-4 rounded accent-cyan-600 disabled:opacity-30"
                  />
                  <div className="min-w-0 flex-1">
                    <span className={`text-sm ${claimed ? 'text-slate-500' : 'text-slate-800'}`}>
                      {p.name}
                    </span>
                    <span className="text-slate-400 text-xs"> #{p.harvest_id}</span>
                    {claimed && (
                      <span className="flex items-center gap-1 text-[11px] text-amber-600/80 mt-0.5">
                        <Lock className="w-3 h-3" />
                        already in “{p.billing_group_name}”
                      </span>
                    )}
                  </div>
                  {p.is_fixed_fee && (
                    <span className="text-[10px] uppercase tracking-wide text-violet-700/80">fixed fee</span>
                  )}
                  {p.hourly_rate != null && (
                    <span className="text-xs text-slate-500 tabular-nums">{money(p.hourly_rate)}/h</span>
                  )}
                </label>
              );
            })}
          </div>
        )}
        <p className="text-[11px] text-slate-400">
          A project belongs to at most one active group — that's the double-billing guard. Claimed
          projects are shown locked rather than hidden; free the other group first to move one.
        </p>
      </div>

      {/* Billing behaviour */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
        <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Billing behaviour</h2>

        <div>
          <Label>Billing type</Label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {BILLING_TYPES.map((t) => (
              <label
                key={t.value}
                className={`border rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                  form.billing_type === t.value
                    ? 'border-cyan-500/50 bg-cyan-500/5'
                    : 'border-slate-200 hover:border-slate-300'
                }`}
              >
                <div className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="billing_type"
                    checked={form.billing_type === t.value}
                    onChange={() => set('billing_type', t.value)}
                    className="accent-cyan-600"
                  />
                  <span className="text-sm text-slate-800">{t.label}</span>
                </div>
                <p className="text-[11px] text-slate-500 mt-1 ml-5 leading-relaxed">{t.hint}</p>
              </label>
            ))}
          </div>
        </div>

        {!isManual && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Timing answers "which month's work does this invoice cover?" A
                draw covers no month — it's a contract event billed the day it's
                confirmed — so the question doesn't apply to fixed-fee groups. */}
            {!isFixedFee && (
              <div>
                <Label hint="Arrears bills last month; advance bills this month.">Timing</Label>
                <select
                  className={inputCls}
                  value={form.billing_timing}
                  onChange={(e) => set('billing_timing', e.target.value as BillingTiming)}
                >
                  <option value="arrears">arrears</option>
                  <option value="advance">advance</option>
                </select>
              </div>
            )}
            <div>
              <Label hint="Enum terms let Harvest compute the due date.">Payment term</Label>
              <select
                className={inputCls}
                value={form.payment_term}
                onChange={(e) => set('payment_term', e.target.value as PaymentTerm)}
              >
                {PAYMENT_TERMS.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            {form.payment_term === 'custom' && (
              <div>
                <Label hint="Due date is computed here, not by Harvest.">Net days</Label>
                <input
                  type="number"
                  min={0}
                  className={inputCls}
                  value={form.custom_net_days ?? ''}
                  onChange={(e) => set('custom_net_days',
                    e.target.value === '' ? null : Number(e.target.value))}
                />
              </div>
            )}
          </div>
        )}

        {isTM && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <Label hint="How Harvest groups the time line items.">Time summary</Label>
              <select
                className={inputCls}
                value={form.time_summary_type ?? 'people'}
                onChange={(e) => set('time_summary_type', e.target.value as SummaryType)}
              >
                {(['project', 'task', 'people', 'detailed'] as SummaryType[]).map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div>
              <Label>Expenses</Label>
              <label className="flex items-center gap-2 text-sm text-slate-700 py-1.5">
                <input
                  type="checkbox"
                  checked={form.include_expenses}
                  onChange={(e) => set('include_expenses', e.target.checked)}
                  className="w-4 h-4 rounded accent-cyan-600"
                />
                Include billable expenses
              </label>
            </div>
            {form.include_expenses && (
              <div>
                <Label>Expense summary</Label>
                <select
                  className={inputCls}
                  value={form.expense_summary_type ?? 'category'}
                  onChange={(e) => set('expense_summary_type', e.target.value as ExpenseSummaryType)}
                >
                  {(['project', 'category', 'people', 'detailed'] as ExpenseSummaryType[]).map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Recurring line items */}
      {isRecurring && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <div>
            <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
              Recurring line items
            </h2>
            <p className="text-xs text-slate-500 mt-1 leading-relaxed">
              Harvest generates nothing for this billing type — these lines are sent literally,
              every month.
            </p>
          </div>
          <RecurringItemsEditor
            items={lineItems}
            onChange={setLineItems}
            projects={pickedProjects}
          />
        </div>
      )}

      {/* Payment schedule (draws) */}
      {isFixedFee && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <div className="flex items-start gap-2">
            <div className="flex-1">
              <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                Payment schedule
              </h2>
              <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                The contract's draws. Each one bills as its own invoice, when you confirm
                delivery — never automatically on its scheduled date.
              </p>
            </div>
          </div>
          <DrawScheduleEditor
            items={draws}
            onChange={setDraws}
            projects={pickedProjects}
          />
        </div>
      )}

      {/* Presentation */}
      {!isManual && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
          <h2 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">Presentation</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label
                hint={isFixedFee
                  ? 'Tokens: {client_name}, {draw_description}, {draw_number}, {draw_count}. A draw covers no period, so {period_label} has nothing to render.'
                  : 'Tokens: {client_name}, {period_label}'}
              >
                Subject template
              </Label>
              <input
                className={inputCls}
                value={form.subject_template ?? ''}
                onChange={(e) => set('subject_template', e.target.value)}
              />
            </div>
            <div>
              <Label hint="Optional. Same tokens.">Notes template</Label>
              <input
                className={inputCls}
                value={form.notes_template ?? ''}
                onChange={(e) => set('notes_template', e.target.value || null)}
              />
            </div>
            <div>
              <Label>Purchase order</Label>
              <input
                className={inputCls}
                value={form.purchase_order ?? ''}
                onChange={(e) => set('purchase_order', e.target.value || null)}
              />
              <label className="flex items-center gap-2 text-xs text-slate-600 mt-2">
                <input
                  type="checkbox"
                  checked={form.requires_purchase_order}
                  onChange={(e) => set('requires_purchase_order', e.target.checked)}
                  className="w-3.5 h-3.5 rounded accent-cyan-600"
                />
                Required — flag MISSING_PO when empty
              </label>
            </div>
            <div>
              <Label hint="Leave blank to inherit the Harvest client's currency.">Currency</Label>
              <input
                className={inputCls}
                value={form.currency ?? ''}
                onChange={(e) => set('currency', e.target.value || null)}
                placeholder="USD"
              />
            </div>
          </div>
        </div>
      )}

      <div className="sticky bottom-0 -mx-6 px-6 py-3 bg-white/95 backdrop-blur border-t border-slate-200 flex items-center gap-3">
        {!canSave && (
          <p className="text-xs text-slate-500">
            Needs a name, a client, at least one project
            {form.payment_term === 'custom' ? ', net days' : ''}
            {isRecurring && lineItems.some((it) => !it.description.trim())
              ? ', and a description on every line item' : ''}.
          </p>
        )}
        <button
          onClick={() => navigate(isEdit ? `/invoices/groups/${groupId}` : '/invoices/groups')}
          className="ml-auto px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors"
        >
          Cancel
        </button>
        <button
          disabled={!canSave || save.isPending}
          onClick={() => { setError(null); save.mutate(); }}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-cyan-500/15 border border-cyan-500/40 text-cyan-600 hover:bg-cyan-500/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {save.isPending
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Save className="w-3.5 h-3.5" />}
          {isEdit ? 'Save changes' : 'Create billing group'}
        </button>
      </div>
    </div>
  );
}
