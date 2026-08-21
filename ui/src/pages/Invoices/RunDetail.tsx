import { Fragment, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft, ChevronDown, ChevronRight, RefreshCw, Unlock,
  AlertOctagon, SkipForward, ExternalLink, Link2, Loader2, Check, Undo2, Ban,
  Pencil, Info,
} from 'lucide-react';
import StubBadge from '../../components/shared/StubBadge';
import { FlagChip, FlagRow, SeverityCount } from './components/FlagChip';
import {
  BillingTypeChip, TimingChip, ItemStatusChip, StatTile, Field, Delta,
  RunStatusChip,
} from './components/Bits';
import InFlightModal from './components/InFlightModal';
import PlaceholderPanel from './components/PlaceholderPanel';
import {
  getBillingRun, planBillingRun, abandonBillingRun, setItemApproval, setRunApproval,
} from '../../api';
import {
  blockingFlag, hasError, money, shortDate, dateTime, unresolvedPlaceholders,
} from '../../invoicing';
import type { BillingRunDetail, RunItem } from '../../invoicing';

/** The approval decision for one group.
 *
 *  Two lines, always in the same place: the top line states where the group
 *  stands right now, the button states the transition a click performs. A bare
 *  checkbox left both implicit — you could not tell whether ticking it meant
 *  "approved" or was merely a selection for some later action.
 */
function ApprovalControl({
  approved,
  blocked,
  blockedReason,
  busy,
  approvedBy,
  onToggle,
}: {
  approved: boolean;
  blocked: boolean;
  blockedReason: string;
  busy: boolean;
  approvedBy: string | null;
  onToggle: () => void;
}) {
  const state = blocked ? 'Blocked' : approved ? 'Approved' : 'Pending';
  const stateTone = blocked
    ? 'text-red-700'
    : approved
      ? 'text-emerald-700'
      : 'text-slate-500';

  return (
    <div className="w-[104px] flex-shrink-0">
      <p className={`text-[10px] font-semibold uppercase tracking-wide mb-1 ${stateTone}`}>
        {state}
      </p>
      <button
        type="button"
        onClick={onToggle}
        disabled={blocked || busy}
        title={
          blocked
            ? blockedReason
            : approved
              ? `Move back to pending — this group will not be invoiced${approvedBy ? ` (approved by ${approvedBy})` : ''}`
              : 'Approve this group — it will be included when drafts are created'
        }
        className={`w-full flex items-center justify-center gap-1.5 px-2 py-1 rounded-lg border text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
          blocked
            ? 'border-slate-300 text-slate-500'
            : approved
              ? 'border-slate-300 text-slate-600 hover:bg-slate-100'
              : 'bg-emerald-500/10 border-emerald-500/50 text-emerald-700 hover:bg-emerald-500/20'
        }`}
      >
        {blocked ? (
          <><Ban className="w-3 h-3" /> Approve</>
        ) : approved ? (
          <><Undo2 className="w-3 h-3" /> Unapprove</>
        ) : (
          <><Check className="w-3 h-3" /> Approve</>
        )}
      </button>
    </div>
  );
}

function PlannedItemCard({
  run,
  item,
  busy,
  onToggleSelect,
  onOverride,
  onResolveInFlight,
}: {
  run: BillingRunDetail;
  item: RunItem;
  busy: boolean;
  onToggleSelect: () => void;
  onOverride: () => void;
  onResolveInFlight: (item: RunItem) => void;
}) {
  const undecided = unresolvedPlaceholders(item);
  const [open, setOpen] = useState(false);
  const inFlightFlag = blockingFlag(item);
  // Approval and the error override both live on the ledger row, so a reload
  // shows exactly what the operator last decided.
  const selected = item.status === 'approved';
  const overridden = item.error_override;
  const flagBlocked = hasError(item) && (!!inFlightFlag || !overridden);
  const blocked = flagBlocked || undecided.length > 0;
  const payload = item.planned_payload as { subject?: string };

  // PLACEHOLDER_LINE_ITEMS is still recorded — it is a frozen, honest note in
  // `plan_snapshot` of what the plan contained. It is just not rendered here,
  // because the banner above already says it, always, and says it live rather
  // than as of plan time. Three copies of "this invoice has placeholders" made
  // the real flags harder to see, which is the opposite of what flags are for.
  const shownFlags = item.flags.filter((f) => f.code !== 'PLACEHOLDER_LINE_ITEMS');

  return (
    <div className={`bg-white border-y border-r rounded-xl overflow-hidden transition-colors border-l-4 ${
      blocked
        ? 'border-red-500/40 border-l-red-500'
        : selected
          ? 'border-emerald-500/40 border-l-emerald-500'
          : 'border-slate-200 border-l-slate-300'
    }`}>
      <div className="flex items-center gap-3 px-4 py-3">
        <ApprovalControl
          approved={selected}
          blocked={blocked}
          blockedReason={
            undecided.length > 0
              ? `Decide ${undecided.length === 1 ? 'the placeholder' : `all ${undecided.length} placeholders`} below — an amount, or omit for this month`
              : 'Resolve the error flag below before this group can be approved'
          }
          busy={busy}
          approvedBy={item.approved_by ?? null}
          onToggle={onToggleSelect}
        />
        <button className="flex-1 flex items-center gap-3 text-left min-w-0" onClick={() => setOpen(!open)}>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-slate-900 truncate">
                {item.billing_group_name}
              </span>
              {item.billing_type && <BillingTypeChip type={item.billing_type} />}
              {item.billing_timing && <TimingChip timing={item.billing_timing} />}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              {item.harvest_client_name} · {shortDate(item.period_start)} – {shortDate(item.period_end)} ·
              issue {shortDate(item.issue_date)} · due {shortDate(item.due_date)}
            </p>
          </div>

          <div className="flex items-center gap-1.5 flex-wrap justify-end max-w-[280px]">
            {shownFlags.map((f) => <FlagChip key={f.code} flag={f} />)}
          </div>

          <div className="text-right w-36 flex-shrink-0">
            <p className="text-sm font-semibold text-slate-900 tabular-nums">
              {money(item.planned_amount)}
            </p>
            <Delta current={item.planned_amount} prior={item.prior_amount} />
          </div>

          {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
        </button>
      </div>

      {/* Undecided placeholders come first: it is the cheapest of the three to
          clear, and unlike the others it is cleared right here.

          Sky rather than red or amber. Entering a monthly amount is a routine
          step, not a fault — the alarm palette belongs to the flags that mean
          something went wrong. `sky` is already what `FlagChip` uses for info,
          so this reads as the same register. This banner is also the *only*
          placeholder notice on the card: it shows collapsed as well as
          expanded, which is why the PLACEHOLDER_LINE_ITEMS chip and flag row
          are filtered out below rather than saying it a third time. */}
      {undecided.length > 0 && (
        <div className="flex items-center gap-3 px-4 py-2 bg-sky-500/10 border-t border-sky-500/40">
          <Info className="w-3.5 h-3.5 text-sky-600 flex-shrink-0" />
          <p className="text-xs text-sky-700 flex-1">
            {undecided.length === 1
              ? '1 placeholder line item still needs an amount, or an explicit omit for this month.'
              : `${undecided.length} placeholder line items still need an amount, or an explicit omit for this month.`}
            {' '}Not overridable — that is what a placeholder is for.
          </p>
          {/* No Override button, deliberately. The decision is two fields
              below; an override here would be the forgetting this prevents. */}
          {!open && (
            <button
              onClick={() => setOpen(true)}
              className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium bg-sky-500/20 border border-sky-500/50 text-sky-700 hover:bg-sky-500/30 transition-colors"
            >
              <Pencil className="w-3 h-3" />
              Decide {undecided.length === 1 ? 'it' : 'them'}
            </button>
          )}
        </div>
      )}

      {flagBlocked && (
        <div className="flex items-center gap-3 px-4 py-2 bg-red-500/10 border-t border-red-500/40">
          <AlertOctagon className="w-3.5 h-3.5 text-red-600 flex-shrink-0" />
          <p className="text-xs text-red-700 flex-1">
            {inFlightFlag
              ? 'Blocked by an unresolved in-flight row. Not overridable — approving could create a duplicate invoice.'
              : 'Error-severity flag. Defaults to unapproved — approving requires an explicit override.'}
          </p>
          {inFlightFlag ? (
            <button
              onClick={() => onResolveInFlight(item)}
              className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium bg-red-500/20 border border-red-500/50 text-red-700 hover:bg-red-500/30 transition-colors"
            >
              <Link2 className="w-3 h-3" />
              Resolve in-flight row
            </button>
          ) : (
            <button
              onClick={onOverride}
              className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium bg-red-500/20 border border-red-500/50 text-red-700 hover:bg-red-500/30 transition-colors"
            >
              <Unlock className="w-3 h-3" />
              Override
            </button>
          )}
        </div>
      )}

      {overridden && hasError(item) && !inFlightFlag && (
        <div className="flex items-center gap-3 px-4 py-1.5 bg-amber-400/10 border-t border-amber-400/40">
          <p className="text-[11px] text-amber-700 flex-1">
            Override recorded — this group is approvable despite an error flag.
          </p>
          <button
            onClick={onOverride}
            disabled={busy}
            className="text-[11px] text-amber-700 hover:text-amber-800 underline disabled:opacity-50"
          >
            Withdraw override
          </button>
        </div>
      )}

      {open && (
        <div className="border-t border-slate-200 px-4 py-4 space-y-5 bg-slate-50">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Field label="Harvest client">{item.harvest_client_name ?? '—'}</Field>
            <Field label="Service period">
              {shortDate(item.period_start)} – {shortDate(item.period_end)}
            </Field>
            <Field label="Issue / due">
              {shortDate(item.issue_date)} → {shortDate(item.due_date)}
            </Field>
            <Field label="Subject">{payload.subject ?? '—'}</Field>
            <Field label="Config">
              <Link
                to={`/invoices/groups/${item.billing_group_id}`}
                className="text-cyan-600 hover:text-cyan-700 text-xs inline-flex items-center gap-1"
              >
                Edit billing group <ExternalLink className="w-3 h-3" />
              </Link>
            </Field>
          </div>

          {/* Above the line-item table: it is the thing to act on, and every
              number in that table depends on it. */}
          <PlaceholderPanel run={run} item={item} />

          <div>
            <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium mb-2">
              Estimated line items
            </p>
            <div className="border border-slate-200 rounded-lg overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-white text-slate-500 uppercase tracking-wide">
                    <th className="text-left px-3 py-2 font-medium">Description</th>
                    <th className="text-right px-3 py-2 font-medium">Qty</th>
                    <th className="text-right px-3 py-2 font-medium">Rate</th>
                    <th className="text-right px-3 py-2 font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {item.estimated_line_items.map((li, i) => {
                    const omitted = li.placeholder_state === 'omitted';
                    return (
                      <tr key={i} className="border-t border-slate-200">
                        <td className="px-3 py-2">
                          <span className={omitted ? 'text-slate-400 line-through' : 'text-slate-800'}>
                            {li.label}
                          </span>
                          {li.placeholder_state === 'unresolved' && (
                            <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded bg-amber-400/20 text-amber-700 font-medium align-middle">
                              needs amount
                            </span>
                          )}
                          {li.placeholder_state === 'resolved' && (
                            <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-700 font-medium align-middle">
                              entered
                            </span>
                          )}
                          {omitted && (
                            <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded bg-slate-200 text-slate-500 font-medium align-middle">
                              omitted
                            </span>
                          )}
                          {li.detail && <span className="text-slate-400 block text-[11px]">{li.detail}</span>}
                        </td>
                        <td className={`px-3 py-2 text-right tabular-nums ${omitted ? 'text-slate-300 line-through' : 'text-slate-600'}`}>
                          {li.quantity} {li.unit}
                        </td>
                        <td className={`px-3 py-2 text-right tabular-nums ${omitted ? 'text-slate-300' : 'text-slate-600'}`}>
                          {omitted ? '—' : money(li.unit_price)}
                        </td>
                        <td className={`px-3 py-2 text-right tabular-nums ${omitted ? 'text-slate-300' : 'text-slate-800'}`}>
                          {omitted ? '—' : money(li.amount)}
                        </td>
                      </tr>
                    );
                  })}
                  <tr className="border-t border-slate-300 bg-white">
                    <td className="px-3 py-2 text-slate-600 font-medium" colSpan={3}>
                      {undecided.length > 0 ? 'Estimated total so far' : 'Estimated total'}
                    </td>
                    <td className="px-3 py-2 text-right text-slate-900 font-semibold tabular-nums">
                      {money(item.planned_amount)}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            {/* The T&M caveat is about the estimator, and does not apply to a
                recurring group — there we hand Harvest the literal lines, so
                once every placeholder is decided the total is exact. */}
            <p className="text-[11px] text-slate-400 mt-1.5">
              {item.billing_type === 'recurring_monthly'
                ? undecided.length > 0
                  ? 'A floor, not a forecast — it excludes the placeholders still awaiting an amount.'
                  : 'These are the literal line items sent to Harvest, so this total is exact.'
                : "Computed independently of Harvest's own invoice generation. It is a sanity check, not a contract — variance is recorded after creation."}
            </p>
          </div>

          {shownFlags.length > 0 && (
            <div className="space-y-2">
              <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">Flags</p>
              {shownFlags.map((f) => <FlagRow key={f.code} flag={f} />)}
            </div>
          )}

          <details>
            <summary className="text-[11px] text-slate-500 uppercase tracking-wide font-medium cursor-pointer hover:text-slate-600">
              Planned payload — exact POST body
            </summary>
            <pre className="mt-2 text-xs text-emerald-700 bg-slate-50 border border-slate-200 rounded-lg p-3 overflow-x-auto font-mono leading-relaxed">
              {JSON.stringify(item.planned_payload, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}

function PreflightView({ run }: { run: BillingRunDetail }) {
  const queryClient = useQueryClient();
  const planned = run.items.filter((i) => i.status !== 'skipped');
  const skipped = run.items.filter((i) => i.status === 'skipped');

  const [resolving, setResolving] = useState<RunItem | null>(null);

  // Approval is persisted, so every gesture is a round trip. The response is
  // the whole run, which becomes the new cache entry.
  const onSuccess = (fresh: BillingRunDetail) => {
    queryClient.setQueryData(['billing-run', run.id], fresh);
    queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
  };

  const itemApproval = useMutation({
    mutationFn: ({ id, ...body }: { id: string; approved?: boolean; override?: boolean }) =>
      setItemApproval(run.id, id, body),
    onSuccess,
  });
  const bulkApproval = useMutation({
    mutationFn: (approved: boolean) => setRunApproval(run.id, approved),
    onSuccess,
  });

  const busy = itemApproval.isPending || bulkApproval.isPending;
  const failure = (itemApproval.error ?? bulkApproval.error) as Error | null;

  const dormantFlags = run.run_flags.filter((f) => f.code === 'UNMAPPED_PROJECT_NO_TIME');
  const liveFlags = run.run_flags.filter((f) => f.code !== 'UNMAPPED_PROJECT_NO_TIME');

  const approved = planned.filter((i) => i.status === 'approved');
  const approvedTotal = approved.reduce((s, i) => s + i.planned_amount, 0);
  // Must match the server's bulk filter, or "Approve all 5" approves 4 and the
  // count reads as a failure.
  const approvable = planned.filter(
    (i) => i.status === 'planned'
      && (!hasError(i) || (i.error_override && !blockingFlag(i)))
      && unresolvedPlaceholders(i).length === 0,
  );
  const undecidedCount = planned.reduce(
    (n, i) => n + unresolvedPlaceholders(i).length, 0,
  );

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatTile label="Invoices to create" value={planned.length} sub={`${skipped.length} groups skipped`} />
        <StatTile label="Estimated value" value={money(run.planned_total)} />
        <StatTile
          label="Flags"
          value={
            <span className="flex items-center gap-3 text-sm">
              <SeverityCount severity="error" count={run.flag_counts?.error ?? 0} />
              <SeverityCount severity="warning" count={run.flag_counts?.warning ?? 0} />
            </span>
          }
          sub={`${run.flag_counts?.info ?? 0} info`}
        />
        <StatTile
          label="Approved so far"
          value={`${approved.length} / ${planned.length}`}
          sub={
            // The one thing standing between the operator and a full run that
            // isn't a config problem — worth stating before any card is opened.
            undecidedCount > 0
              ? `${undecidedCount} placeholder${undecidedCount === 1 ? '' : 's'} to decide`
              : money(approvedTotal)
          }
          tone={
            undecidedCount > 0
              ? 'warn'
              : planned.length > 0 && approved.length === planned.length ? 'good' : 'warn'
          }
        />
      </div>

      {failure && (
        <div className="bg-red-500/10 border border-red-500/40 rounded-lg px-4 py-2.5">
          <p className="text-xs text-red-700">{failure.message}</p>
        </div>
      )}

      {run.run_flags.length > 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-2">
          <p className="text-[11px] text-slate-500 uppercase tracking-wide font-medium">
            Run-level flags — projects outside every billing group
          </p>
          {liveFlags.map((f, i) => <FlagRow key={`${f.code}-${i}`} flag={f} />)}

          {/* Ungrouped projects with no uninvoiced time cost this run nothing,
              so they fold away — visible, but never louder than the flags that
              represent money on the floor. */}
          {dormantFlags.length > 0 && (
            <details className="rounded-lg border border-amber-400/40 bg-amber-400/10">
              <summary className="cursor-pointer px-3 py-2 text-xs text-amber-700 list-none flex items-center gap-2">
                <ChevronRight className="w-3 h-3 flex-shrink-0" />
                <span>
                  <span className="font-medium">{dormantFlags.length}</span>{' '}
                  billable project{dormantFlags.length === 1 ? '' : 's'} in no billing
                  group, with no uninvoiced time — nothing is missing from this run, but
                  time logged {dormantFlags.length === 1 ? 'to it' : 'to them'} would go
                  uninvoiced.
                </span>
              </summary>
              <div className="px-3 pb-3 pt-1 space-y-2">
                {dormantFlags.map((f, i) => <FlagRow key={`${f.code}-${i}`} flag={f} />)}
              </div>
            </details>
          )}

          <Link
            to="/invoices/groups"
            className="inline-flex items-center gap-1 text-xs text-cyan-600 hover:text-cyan-700 font-medium pt-1"
          >
            Fix in Billing Groups <ChevronRight className="w-3 h-3" />
          </Link>
        </div>
      )}

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
            {planned.length === 1
              ? 'Planned invoice — not approved until you say so'
              : `Planned invoices (${planned.length}) — nothing is approved until you say so`}
          </p>
          {/* Bulk controls on a single item are just a second way to click the
              same checkbox. */}
          {planned.length > 1 && (
            <div className="flex items-center gap-2 text-xs">
              {busy && <Loader2 className="w-3 h-3 animate-spin text-slate-400" />}
              <button
                onClick={() => bulkApproval.mutate(true)}
                disabled={busy || approvable.length === 0}
                title="Approve every group that is not blocked by an error flag"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-emerald-500/50 bg-emerald-500/10 text-emerald-700 font-medium hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Check className="w-3 h-3" />
                Approve all {approvable.length}
              </button>
              <button
                onClick={() => bulkApproval.mutate(false)}
                disabled={busy || approved.length === 0}
                title="Move every approved group back to pending"
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-slate-300 text-slate-600 font-medium hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Undo2 className="w-3 h-3" />
                Unapprove all {approved.length}
              </button>
            </div>
          )}
        </div>
        {planned.length === 0 ? (
          <div className="bg-white border border-slate-200 rounded-xl px-4 py-10 text-center">
            <p className="text-sm text-slate-600">No invoices to create for this run.</p>
            <p className="text-xs text-slate-400 mt-1">
              Every active group was skipped — see the reasons below.
            </p>
          </div>
        ) : planned.map((item) => (
          <PlannedItemCard
            key={item.id}
            run={run}
            item={item}
            busy={busy}
            onToggleSelect={() => itemApproval.mutate({
              id: item.id, approved: item.status !== 'approved',
            })}
            onOverride={() => itemApproval.mutate({
              id: item.id, override: !item.error_override,
            })}
            onResolveInFlight={setResolving}
          />
        ))}
      </div>

      {skipped.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
            Skipped ({skipped.length})
          </p>
          {skipped.map((item) => (
            <div key={item.id} className="flex items-start gap-3 bg-slate-50 border border-slate-200 rounded-lg px-4 py-2.5">
              <SkipForward className="w-3.5 h-3.5 text-slate-400 flex-shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm text-slate-700">{item.billing_group_name}</span>
                  {item.billing_type && <BillingTypeChip type={item.billing_type} />}
                  {item.flags.map((f) => <FlagChip key={f.code} flag={f} />)}
                </div>
                <p className="text-xs text-slate-500 mt-0.5">{item.skip_reason}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {resolving && (
        <InFlightModal run={run} item={resolving} onClose={() => setResolving(null)} />
      )}

      {/* Monthly-run execution is still unbuilt, and stays behind the reconcile
          gate (plan a month, invoice it by hand, compare). The single-draw write
          ships — that path is on the Draws tab, which is where a draw is drafted
          anyway since it never rides a run. */}
      <div className="sticky bottom-0 -mx-6 px-6 py-3 bg-white/95 backdrop-blur border-t border-slate-200 flex items-center gap-4">
        <div className="text-sm">
          {run.kind === 'draw' ? (
            <>
              <span className={approved.length ? 'text-emerald-700 font-semibold' : 'text-slate-500'}>
                {approved.length ? 'Approved' : 'Not approved'}
              </span>
              <span className="text-slate-500"> · </span>
            </>
          ) : (
            <>
              <span className="text-slate-900 font-semibold">{approved.length}</span>
              <span className="text-slate-500"> of {planned.length} groups approved · </span>
            </>
          )}
          <span className="text-slate-900 font-semibold tabular-nums">
            {money(run.kind === 'draw' ? run.planned_total : approvedTotal)}
          </span>
          {run.kind !== 'draw' && planned.length - approved.length > 0 && (
            <span className="text-slate-500">
              {' · '}{planned.length - approved.length} still pending
            </span>
          )}
        </div>
        {run.kind === 'draw' ? (
          <Link
            to="/invoices/draws"
            className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-emerald-500/10 border border-emerald-500/50 text-emerald-700 hover:bg-emerald-500/20 transition-colors"
          >
            Bill this from the Draws tab
          </Link>
        ) : (
          <button
            disabled
            title="Monthly-run execution is not built yet, and is gated on reconciling a full month by hand. Draws are drafted from the Draws tab."
            className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 border border-slate-300 text-slate-500 cursor-not-allowed"
          >
            Create {approved.length} draft{approved.length === 1 ? '' : 's'} in Harvest
            <StubBadge />
          </button>
        )}
      </div>
    </>
  );
}

function ResultView({ run }: { run: BillingRunDetail }) {
  const [resolving, setResolving] = useState<RunItem | null>(null);
  const created = run.items.filter((i) => i.status === 'created');
  const failed = run.items.filter((i) => i.status === 'failed');
  const inFlight = run.items.filter((i) => i.status === 'in_flight');
  const plannedTotal = created.reduce((s, i) => s + i.planned_amount, 0);
  const actualTotal = created.reduce((s, i) => s + (i.actual_amount ?? 0), 0);
  const variance = actualTotal - plannedTotal;

  if (run.items.length === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl px-4 py-10 text-center">
        <p className="text-slate-600 text-sm">This run has no ledger rows.</p>
      </div>
    );
  }

  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <StatTile label="Created" value={created.length} tone="good" />
        <StatTile label="Failed" value={failed.length} tone={failed.length ? 'bad' : 'default'} />
        <StatTile
          label="In flight"
          value={inFlight.length}
          tone={inFlight.length ? 'bad' : 'default'}
          sub={inFlight.length ? 'needs a human' : undefined}
        />
        <StatTile label="Created amount" value={money(actualTotal)} sub="pre-edit, not what was sent" />
        <StatTile
          label="Variance vs plan"
          value={`${variance >= 0 ? '+' : ''}${money(variance)}`}
          tone={Math.abs(variance) > 50 ? 'warn' : 'default'}
          sub={plannedTotal ? `${((variance / plannedTotal) * 100).toFixed(2)}%` : undefined}
        />
      </div>

      {inFlight.length > 0 && (
        <div className="bg-red-500/10 border border-red-500/40 rounded-xl p-4 space-y-2">
          <p className="text-sm text-red-700 font-medium flex items-center gap-2">
            <AlertOctagon className="w-4 h-4" /> Unresolved in-flight rows
          </p>
          {inFlight.map((i) => (
            <div key={i.id} className="text-xs text-slate-600 leading-relaxed">
              <span className="text-slate-800">{i.billing_group_name}</span> — {i.error_message}
              <button
                onClick={() => setResolving(i)}
                className="ml-2 inline-flex items-center gap-1 text-red-700 hover:text-red-800 font-medium"
              >
                Resolve <ChevronRight className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      {resolving && (
        <InFlightModal run={run} item={resolving} onClose={() => setResolving(null)} />
      )}

      <div className="bg-white border border-slate-200 rounded-xl overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500 uppercase tracking-wide">
              <th className="text-left px-4 py-3 font-medium">Billing group</th>
              <th className="text-left px-4 py-3 font-medium">Status</th>
              <th className="text-left px-4 py-3 font-medium">Invoice</th>
              <th className="text-right px-4 py-3 font-medium">Planned</th>
              <th className="text-right px-4 py-3 font-medium">Created</th>
              <th className="text-right px-4 py-3 font-medium">Variance</th>
            </tr>
          </thead>
          <tbody>
            {run.items.map((item, i) => {
              const v = item.variance;
              return (
                <Fragment key={item.id}>
                  <tr className={i < run.items.length - 1 ? 'border-b border-slate-200' : ''}>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-slate-800">{item.billing_group_name}</span>
                        {item.billing_type && <BillingTypeChip type={item.billing_type} />}
                      </div>
                    </td>
                    <td className="px-4 py-3"><ItemStatusChip status={item.status} /></td>
                    <td className="px-4 py-3 text-xs text-slate-600">
                      {item.harvest_invoice_number ? `#${item.harvest_invoice_number}` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-slate-600 tabular-nums">
                      {money(item.planned_amount)}
                    </td>
                    <td className="px-4 py-3 text-right text-slate-800 tabular-nums">
                      {item.actual_amount === null ? '—' : money(item.actual_amount)}
                    </td>
                    <td className={`px-4 py-3 text-right tabular-nums ${
                      v === null ? 'text-slate-400' : Math.abs(v) > 50 ? 'text-amber-600' : 'text-slate-500'
                    }`}>
                      {v === null ? '—' : `${v >= 0 ? '+' : ''}${money(v)}`}
                    </td>
                  </tr>
                  {(item.error_message || item.flags.length > 0) && (
                    <tr className={i < run.items.length - 1 ? 'border-b border-slate-200' : ''}>
                      <td colSpan={6} className="px-4 pb-3 -mt-1">
                        {item.error_message && (
                          <p className="text-xs text-red-700/90 font-mono leading-relaxed">
                            {item.error_message}
                          </p>
                        )}
                        {item.flags.map((f) => (
                          <p key={f.code} className="text-xs text-amber-600/80 leading-relaxed">
                            {f.code} — {f.message}
                          </p>
                        ))}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function RunDetail() {
  const { runId = '' } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: run, isLoading, error } = useQuery({
    queryKey: ['billing-run', runId],
    queryFn: () => getBillingRun(runId),
    enabled: !!runId,
  });

  const replan = useMutation({
    mutationFn: () => planBillingRun(run!.run_month),
    onSuccess: (fresh) => {
      queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
      navigate(`/invoices/runs/${fresh.id}`, { replace: true });
    },
  });

  const abandon = useMutation({
    mutationFn: () => abandonBillingRun(runId),
    onSuccess: (fresh) => {
      queryClient.invalidateQueries({ queryKey: ['billing-run', runId] });
      queryClient.invalidateQueries({ queryKey: ['billing-runs'] });
      // Discarding a draw run releases its draw back to Ready to bill, and an
      // abandoned single-invoice run is not worth staying on.
      if (fresh.kind === 'draw') {
        queryClient.invalidateQueries({ queryKey: ['billing-draws'] });
        navigate('/invoices/draws');
      }
    },
  });

  if (isLoading) {
    return (
      <div className="p-6 max-w-7xl mx-auto text-sm text-slate-500 animate-pulse">Loading run…</div>
    );
  }
  if (error || !run) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <p className="text-slate-600 text-sm">
          {(error as Error)?.message ?? 'Run not found.'}
        </p>
      </div>
    );
  }

  const isPreflight = run.status === 'awaiting_approval' || run.status === 'planning';
  // A draw run bills one milestone off-cycle. It has no period to re-plan, so
  // the month framing and the Re-plan action would both be nonsense.
  const isDraw = run.kind === 'draw';
  const drawItem = isDraw ? run.items[0] : undefined;

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      <button
        className="flex items-center gap-2 text-sm text-slate-600 hover:text-slate-800 transition-colors"
        onClick={() => navigate(isDraw ? '/invoices/draws' : '/invoices/runs')}
      >
        <ArrowLeft className="w-4 h-4" />
        {isDraw ? 'Back to Draws' : 'Back to Billing Runs'}
      </button>

      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-slate-900">
              {isDraw
                ? (drawItem?.estimated_line_items[0]?.label ?? 'Draw invoice')
                : `${run.label} billing run`}
            </h1>
            <RunStatusChip status={run.status} />
          </div>
          <p className="text-sm text-slate-600 mt-0.5">
            {isDraw && drawItem && (
              <>
                {drawItem.harvest_client_name} · {drawItem.billing_group_name} ·{' '}
              </>
            )}
            Prepared {dateTime(run.created_at)}
            {run.completed_at && ` · completed ${dateTime(run.completed_at)}`}
          </p>
        </div>
        {isPreflight && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => abandon.mutate()}
              disabled={abandon.isPending}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 transition-colors"
            >
              {isDraw ? 'Discard' : 'Abandon run'}
            </button>
            {!isDraw && (
              <button
                onClick={() => replan.mutate()}
                disabled={replan.isPending}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 disabled:opacity-50 transition-colors"
              >
                {replan.isPending
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <RefreshCw className="w-3.5 h-3.5" />}
                Re-plan
              </button>
            )}
          </div>
        )}
      </div>

      {isPreflight ? <PreflightView run={run} /> : <ResultView run={run} />}
    </div>
  );
}
