"""One-time import of the Airtable revenue recognition history into Postgres.

Run once per database, at cutover. Deleted in Phase 4 along with the rest of the
Airtable integration — this file has no future beyond the migration it performs,
and is deliberately a script rather than an endpoint for that reason.

    uv run python -m scripts.backfill_rev_rec --dry-run   # reconcile only
    uv run python -m scripts.backfill_rev_rec             # import
    uv run python -m scripts.backfill_rev_rec --reset     # re-import from scratch


PRODUCTION

Writes to whatever `DATABASE_URL` points at, so production is the same command
with production's DSN. In order:

    1. ./scripts/deploy-db.sh          # migration 0040 must exist there first
    2. DATABASE_URL=<prod> uv run python -m scripts.backfill_rev_rec --dry-run
    3. DATABASE_URL=<prod> uv run python -m scripts.backfill_rev_rec

Step 2 touches nothing — it reads Airtable and reconciles in memory, without
opening a database connection at all — so it is safe to run against a
production DSN and worth doing, because Airtable may have changed since the
last local run.

Step 3 prints the target database and, when it is not localhost, requires the
host name to be typed. The one irreversible mistake available here is importing
years of revenue history into the wrong database.

Deliberately *not* emitted as a SQL file applied through `supabase/migrations/`.
That would work, and would be reviewable, but it would also commit every
client's revenue history to git permanently. Running the script keeps the data
out of version control.


THE RECONCILIATION GATE

The new ledger stores the *period* amount (`recognized_amount`) and derives
cumulative-to-date by summing it. Airtable did the opposite: it stored the
cumulative figure (`Total Recognized Revenue`) and derived the period amount
(`Revenue Delta`) as a formula.

So the import inverts the relationship, and the gate is the proof that the
inversion is lossless: replaying the deltas in date order must reproduce every
historical cumulative total, to the cent, for every project. If it does not,
either Airtable's formula meant something other than what we believe, or the
history has a gap — and in both cases importing the numbers anyway would give
us a ledger that quietly disagrees with the one it replaced.

Nothing is written unless the gate passes. It is not a warning and there is no
flag to skip it.


WHAT IS NOT IMPORTED

The Airtable Projects and Clients tables are a Harvest mirror; this system
already has Harvest. Only the three hand-typed Projects columns survive, into
`revenue_project_config`, and one of those (Client Id) has no successor —
`harvest_projects.client_id` already answers it.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import settings
from app.db import get_pool
from app.integrations import airtable
from app.orchestrator import events
from app.services import audit

#: Who the imported rows say they came from. Also the handle `--reset` deletes
#: by, so a re-import cannot touch a run a human actually planned.
BACKFILL_ACTOR = "airtable-backfill"

#: Airtable's `Billing Type` single-select -> the `revenue_type` enum.
#:
#: Explicit and total. An unrecognised label aborts the import rather than
#: defaulting, because every wrong guess here is a project whose revenue is
#: computed by the wrong formula from now on — and the label is a
#: single-select, so a new one means someone deliberately added it.
REVENUE_TYPE_BY_AIRTABLE_LABEL: dict[str, str] = {
    "Fixed Fee": "fixed_fee",
    "T&M": "time_and_materials",
    "MSF": "msf",
    "Hosting": "hosting",
    "Retainer": "retainer",
}

#: Reconciliation tolerance. Money, so a cent — amounts are quantized to 2dp
#: before summing and the comparison should be exact; the cent absorbs a
#: historical row that was itself rounded.
TOLERANCE = Decimal("0.01")


class BackfillError(RuntimeError):
    """The import cannot proceed. Raised before anything is written."""


# ── Pure mapping and reconciliation ─────────────────────────────────────────


def _money(value: Any) -> Decimal | None:
    """Airtable numbers arrive as JSON floats. Via str() so the Decimal is the
    number as written rather than the float's binary approximation of it."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise BackfillError(f"Cannot read {value!r} as an amount.") from exc


def _ratio(value: Any) -> Decimal | None:
    """Percentage complete, stored 0-1 at 4dp to match `calc_revenue`."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError) as exc:
        raise BackfillError(f"Cannot read {value!r} as a ratio.") from exc


def _period_month(value: Any) -> date:
    """`Date Recognized` -> the first of its month.

    Airtable used the last day of the month. That was a convention its date
    formulas wanted, and it does not carry over — `revenue_runs.period_month`
    matches `billing_runs.run_month` and is first-of-month by CHECK.
    """
    if not value:
        raise BackfillError("A revenue record has no Date Recognized.")
    return date.fromisoformat(str(value)[:10]).replace(day=1)


def map_revenue_type(label: Any) -> str:
    known = ", ".join(sorted(REVENUE_TYPE_BY_AIRTABLE_LABEL))
    if not label:
        raise BackfillError(f"A record has no Billing Type. Known: {known}.")
    try:
        return REVENUE_TYPE_BY_AIRTABLE_LABEL[str(label).strip()]
    except KeyError:
        raise BackfillError(
            f"Unmapped Billing Type {label!r}. Known: {known}. Add it to "
            "REVENUE_TYPE_BY_AIRTABLE_LABEL — do not let it default."
        ) from None


@dataclass
class Entry:
    """One `revenue_entries` row, plus the cumulative figure it came from."""

    period_month: date
    harvest_project_id: int
    harvest_project_name: str
    revenue_type: str
    recognized_amount: Decimal
    logged_hours: Decimal | None
    scheduled_hours: Decimal | None
    percent_complete: Decimal | None
    contracted_fees: Decimal | None
    invoiced_to_date: Decimal | None
    notes: str | None
    #: Airtable's `Total Recognized Revenue` — cumulative-to-date. Kept only to
    #: reconcile against; it is not stored.
    source_cumulative: Decimal | None
    #: Airtable's `Logged Hours`, which is also cumulative-to-date. Differenced
    #: into `logged_hours` by `difference_hours()` after sorting.
    source_cumulative_hours: Decimal | None


@dataclass
class Mismatch:
    harvest_project_id: int
    harvest_project_name: str
    period_month: date
    expected: Decimal
    actual: Decimal

    @property
    def difference(self) -> Decimal:
        return self.actual - self.expected


@dataclass
class Plan:
    entries: list[Entry] = field(default_factory=list)
    configs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def period_months(self) -> list[date]:
        return sorted({e.period_month for e in self.entries})

    @property
    def total(self) -> Decimal:
        return sum((e.recognized_amount for e in self.entries), Decimal("0.00"))


def build_entries(records: list[dict[str, Any]]) -> list[Entry]:
    """Airtable revenue records -> Entry rows, sorted by project then period.

    `recognized_amount` comes from `Revenue Delta`. A project's first-ever
    record has no prior month to difference against, so Airtable leaves the
    formula empty there and the cumulative total *is* the period amount.
    """
    entries: list[Entry] = []
    # Every bad record, not just the first. This is a one-time import of years
    # of history: dying on record 1 of 960 means fixing one cell in Airtable,
    # re-running for four minutes, and discovering the next one. The whole list
    # in a single pass is the difference between one afternoon and several.
    problems: list[str] = []

    for record in records:
        harvest_id = record.get("Harvest Id")
        name = str(record.get("Project Name") or "Unnamed")
        where = (
            f"{name} @ {record.get('Date Recognized') or 'no date'} "
            f"(airtable id {record.get('airtableId')})"
        )
        if not harvest_id:
            problems.append(f"{where}: no Harvest Id, cannot attribute to a project")
            continue

        try:
            revenue_type = map_revenue_type(record.get("Billing Type"))
            period_month = _period_month(record.get("Date Recognized"))
            delta = _money(record.get("Revenue Delta"))
            cumulative = _money(record.get("Total Recognized Revenue"))
        except BackfillError as exc:
            problems.append(f"{where}: {exc}")
            continue

        entries.append(
            Entry(
                period_month=period_month,
                harvest_project_id=int(harvest_id),
                harvest_project_name=name,
                revenue_type=revenue_type,
                # The fallback is only correct for a project's first record.
                # reconcile() is what proves it was only used there: anywhere
                # else, substituting a cumulative figure for a period one
                # throws the running sum off immediately.
                recognized_amount=(
                    delta if delta is not None else (cumulative or Decimal("0.00"))
                ),
                # Filled in by difference_hours() below, once the rows are
                # sorted and each one's predecessor is known.
                logged_hours=None,
                scheduled_hours=_money(record.get("Scheduled Hours")),
                percent_complete=_ratio(record.get("Percentage Complete")),
                contracted_fees=_money(record.get("Contracted Fees")),
                invoiced_to_date=_money(record.get("Invoiced to Date")),
                notes=record.get("Notes") or None,
                source_cumulative=cumulative,
                source_cumulative_hours=_money(record.get("Logged Hours")),
            )
        )

    if problems:
        raise BackfillError(
            f"{len(problems)} of {len(records)} revenue record(s) cannot be "
            "imported:\n  " + "\n  ".join(sorted(problems))
            + "\n\nFix them in Airtable, or delete them if they are scratch rows."
        )

    entries.sort(key=lambda e: (e.harvest_project_id, e.period_month))
    difference_hours(entries)
    return entries


def difference_hours(entries: list[Entry]) -> None:
    """Turn Airtable's cumulative `Logged Hours` into hours-in-the-period.

    Mutates in place; `entries` must already be sorted by project then period.

    The column stores a period quantity because it is divided by
    `recognized_amount`, which is also one. Airtable stored the running total
    instead — verified against the live base, where all 76 projects with three
    or more months show hours that never decrease.

    The first row with hours has no predecessor to difference against, so its
    cumulative value is a lump covering an unknown span. What to do with it
    depends on whether the revenue history starts at the same place:

    - **Hours start on the project's first revenue record.** Both figures are
      catch-up lumps over the same span — Airtable leaves `Revenue Delta` empty
      there too, so `recognized_amount` falls back to the cumulative total. They
      agree, and the revenue-per-hour figure is meaningful. Kept.
    - **Hours start later** (the live base does this: one project's revenue
      history begins 2024-12 but its hours begin 2025-04). The lump spans four
      months of hours against one month of revenue, which reads as $12/hour on a
      project actually running at $155. Nulled — a dash says "not known", which
      is true, where a number would be believed.

    A decrease is not clamped to zero. It would mean hours were removed in
    Harvest after a period closed, and a negative is the honest record of that:
    silently flooring it would leave the summed hours disagreeing with Harvest
    forever, with nothing to show why.
    """
    seen_project: set[int] = set()
    previous_cumulative: dict[int, Decimal] = {}

    for entry in entries:
        project = entry.harvest_project_id
        is_first_record = project not in seen_project
        seen_project.add(project)

        cumulative = entry.source_cumulative_hours
        if cumulative is None:
            continue

        prior = previous_cumulative.get(project)
        if prior is not None:
            entry.logged_hours = cumulative - prior
        elif is_first_record:
            # Aligned with the revenue lump on the same row. Both cover the
            # same unknown span, so the ratio between them holds.
            entry.logged_hours = cumulative
        else:
            # Hours appear mid-history. The span is unknown and does not match
            # this row's revenue; a rate computed from it would be wrong by an
            # order of magnitude.
            entry.logged_hours = None

        previous_cumulative[project] = cumulative


def reconcile(entries: list[Entry]) -> list[Mismatch]:
    """Replay the deltas and check they reproduce every cumulative total.

    See the module docstring: this is the proof that storing the period amount
    loses nothing, and it is the whole basis for trusting the imported history.
    """
    mismatches: list[Mismatch] = []
    running: dict[int, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for entry in sorted(entries, key=lambda e: (e.harvest_project_id, e.period_month)):
        running[entry.harvest_project_id] += entry.recognized_amount
        if entry.source_cumulative is None:
            # Nothing to check against. Not an error — an early row may simply
            # predate the formula — but the running sum carries on regardless.
            continue
        actual = running[entry.harvest_project_id]
        if abs(actual - entry.source_cumulative) > TOLERANCE:
            mismatches.append(
                Mismatch(
                    harvest_project_id=entry.harvest_project_id,
                    harvest_project_name=entry.harvest_project_name,
                    period_month=entry.period_month,
                    expected=entry.source_cumulative,
                    actual=actual,
                )
            )

    return mismatches


def build_configs(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Airtable Projects -> `revenue_project_config` rows.

    Only the hand-typed columns. Everything else on that table was a Harvest
    mirror, and a project whose Billing Type was never filled in is skipped
    rather than guessed at — the run's config gate will ask for it by name.
    """
    configs: list[dict[str, Any]] = []
    problems: list[str] = []
    for project in projects:
        harvest_id = project.get("Harvest Id")
        if not harvest_id or not project.get("Billing Type"):
            continue
        try:
            revenue_type = map_revenue_type(project["Billing Type"])
            contracted_fees = _money(project.get("Contracted Fees"))
        except BackfillError as exc:
            problems.append(f"{project.get('Project Name') or 'Unnamed'}: {exc}")
            continue
        if revenue_type == "fixed_fee" and contracted_fees is None:
            # The CHECK would reject it, and rightly. Skipping leaves the
            # project unconfigured, which the run surfaces as "needs
            # configuration" — the right place for a human to notice.
            print(
                f"  ! skipping config for {project.get('Project Name')!r}: "
                "fixed fee with no Contracted Fees",
                file=sys.stderr,
            )
            continue
        configs.append({
            "harvest_project_id": int(harvest_id),
            "revenue_type": revenue_type,
            "contracted_fees": contracted_fees,
        })

    if problems:
        raise BackfillError(
            f"{len(problems)} project(s) have a Billing Type this import does "
            "not recognize:\n  " + "\n  ".join(sorted(problems))
        )
    return configs


# ── I/O ─────────────────────────────────────────────────────────────────────


async def fetch_plan() -> Plan:
    records, projects = await asyncio.gather(
        airtable.get_revenue_records(settings),
        airtable.get_projects(settings),
    )
    print(f"Read {len(records)} revenue records and {len(projects)} projects.")
    return Plan(entries=build_entries(records), configs=build_configs(projects))


async def write_plan(pool, plan: Plan) -> dict[str, int]:
    """Import everything in one transaction.

    One synthetic run per period, `recognized` from the outset — these months
    were recognized years ago in Airtable and there is nothing left to review.
    """
    runs_by_month: dict[date, str] = {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            for period_month in plan.period_months:
                runs_by_month[period_month] = await conn.fetchval(
                    "INSERT INTO revenue_runs "
                    "(period_month, status, created_by, finalized_at, finalized_by) "
                    "VALUES ($1, 'recognized', $2, now(), $2) RETURNING id",
                    period_month,
                    BACKFILL_ACTOR,
                )

            for entry in plan.entries:
                await conn.execute(
                    """
                    INSERT INTO revenue_entries (
                        revenue_run_id, period_month, harvest_project_id,
                        harvest_project_name, revenue_type, recognized_amount,
                        computed_amount, logged_hours, scheduled_hours,
                        percent_complete, contracted_fees, invoiced_to_date, notes
                    ) VALUES ($1, $2, $3, $4, $5::revenue_type, $6, $6,
                              $7, $8, $9, $10, $11, $12)
                    """,
                    runs_by_month[entry.period_month],
                    entry.period_month,
                    entry.harvest_project_id,
                    entry.harvest_project_name,
                    entry.revenue_type,
                    # computed_amount = recognized_amount: no override history
                    # exists, and claiming one would invent a decision nobody
                    # made.
                    entry.recognized_amount,
                    entry.logged_hours,
                    entry.scheduled_hours,
                    entry.percent_complete,
                    entry.contracted_fees,
                    entry.invoiced_to_date,
                    entry.notes,
                )

            for config in plan.configs:
                await conn.execute(
                    """
                    INSERT INTO revenue_project_config (
                        harvest_project_id, revenue_type, contracted_fees,
                        created_by, updated_by
                    ) VALUES ($1, $2::revenue_type, $3, $4, $4)
                    ON CONFLICT (harvest_project_id) DO NOTHING
                    """,
                    config["harvest_project_id"],
                    config["revenue_type"],
                    config["contracted_fees"],
                    BACKFILL_ACTOR,
                )

            counts = {
                "runs": len(runs_by_month),
                "entries": len(plan.entries),
                "configs": len(plan.configs),
            }
            await audit.write_audit_event(
                conn,
                events.REVENUE_BACKFILL_IMPORTED,
                actor=BACKFILL_ACTOR,
                payload={
                    **counts,
                    "total_recognized": str(plan.total),
                    "earliest_period": str(plan.period_months[0]),
                    "latest_period": str(plan.period_months[-1]),
                },
            )

    return counts


async def reset(pool) -> None:
    """Delete what a previous run of this script imported, and only that.

    Scoped to `created_by = BACKFILL_ACTOR` so a re-import cannot take a run a
    human planned with it. Entries cascade from their run.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            runs = await conn.fetch(
                "DELETE FROM revenue_runs WHERE created_by = $1 RETURNING id",
                BACKFILL_ACTOR,
            )
            configs = await conn.fetch(
                "DELETE FROM revenue_project_config WHERE created_by = $1 "
                "RETURNING harvest_project_id",
                BACKFILL_ACTOR,
            )
    print(
        f"Reset: removed {len(runs)} run(s) (entries cascaded) and "
        f"{len(configs)} config row(s)."
    )


def _describe_target() -> tuple[str, bool]:
    """(host/database, is_local) for the DSN this run would write to.

    Credentials are never returned, only where the writes would land. Printed
    before anything commits, because the one irreversible mistake available
    here is importing years of revenue history into the wrong database — and
    the DSN comes from an environment variable, which is exactly the kind of
    thing that is set once and forgotten.
    """
    from urllib.parse import urlparse

    from app.config import settings

    parsed = urlparse(settings.database_url.get_secret_value())
    host = parsed.hostname or "?"
    target = f"{host}:{parsed.port or 5432}{parsed.path}"
    return target, host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def confirm_target(assume_yes: bool) -> None:
    """Show the target database and, if it is not local, require it be typed.

    A y/n prompt is not enough for this one: the answer to "are you sure" is
    always yes, whereas typing the host name means having actually read it.
    """
    target, is_local = _describe_target()
    print(f"\nWriting to: {target}")

    if is_local or assume_yes:
        return

    print(
        "\nThat is not a local database. This import writes years of revenue "
        "history and is not something to undo by hand."
    )
    typed = input(f"Type the host name to continue ({target.split(':')[0]}): ")
    if typed.strip() != target.split(":")[0]:
        raise BackfillError("Host name did not match. Nothing was written.")


async def assert_empty(pool) -> None:
    existing = await pool.fetchval("SELECT count(*) FROM revenue_runs")
    if existing:
        raise BackfillError(
            f"{existing} revenue run(s) already exist. This script is a "
            "one-time import and will not merge into a populated ledger. "
            "Re-run with --reset to replace what a previous backfill wrote."
        )


def report(plan: Plan, mismatches: list[Mismatch]) -> None:
    print(
        f"\n{len(plan.entries)} entries across {len(plan.period_months)} months "
        f"({plan.period_months[0]:%b %Y} – {plan.period_months[-1]:%b %Y}), "
        f"{len({e.harvest_project_id for e in plan.entries})} projects, "
        f"${plan.total:,.2f} total recognized."
    )
    print(f"{len(plan.configs)} project config row(s).")

    if not mismatches:
        print("\nReconciliation passed: replayed deltas reproduce every "
              "historical cumulative total.")
        return

    print(f"\nRECONCILIATION FAILED — {len(mismatches)} mismatch(es):\n", file=sys.stderr)
    for m in sorted(mismatches, key=lambda m: (m.harvest_project_name, m.period_month)):
        print(
            f"  {m.harvest_project_name} ({m.harvest_project_id}) "
            f"{m.period_month:%b %Y}: Airtable says ${m.expected:,.2f}, "
            f"replayed deltas give ${m.actual:,.2f} "
            f"(off by ${m.difference:,.2f})",
            file=sys.stderr,
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read Airtable and run the reconciliation gate. Writes nothing.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete what a previous backfill imported, then import again.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation for a non-local target. For CI only.",
    )
    args = parser.parse_args()

    try:
        # A dry run reads Airtable and reconciles in memory. It never touches
        # the database, so it does not open a connection to one — the point is
        # to be able to run it against production data from anywhere, before
        # deciding whether the import is safe.
        pool = None
        if not args.dry_run:
            # Before the pool, so a wrong DSN is caught by a human reading it
            # rather than by a failed connection — or worse, a successful one.
            confirm_target(args.yes)
            pool = await get_pool()
            if args.reset:
                await reset(pool)
            await assert_empty(pool)

        plan = await fetch_plan()
        if not plan.entries:
            raise BackfillError("Airtable returned no revenue records.")

        mismatches = reconcile(plan.entries)
        report(plan, mismatches)

        if mismatches:
            print(
                "\nNothing was written. Either Airtable's Revenue Delta formula "
                "means something other than month-over-month change, or the "
                "history has a gap. Resolve before importing.",
                file=sys.stderr,
            )
            return 1

        if args.dry_run:
            print("\n--dry-run: nothing written.")
            return 0

        counts = await write_plan(pool, plan)
        print(
            f"\nImported {counts['entries']} entries across {counts['runs']} "
            f"months, plus {counts['configs']} project config rows."
        )
        return 0

    except BackfillError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
