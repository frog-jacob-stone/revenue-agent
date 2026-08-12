"""T&M estimation: rate resolution, rounding, summary types, stragglers."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from app.config import Settings, settings
from app.db import get_pool
from app.services.billing import estimator, rates
from app.services.billing.dates import resolve_period
from tests.fakes.harvest import FakeHarvest

ACME = 5735774
PLATFORM = 14307913
MOBILE = 14307914

JULY = resolve_period(date(2026, 8, 1), "arrears")


async def _sync_project(pool, fake: FakeHarvest, project_id: int):
    """Mirror one fake project (and its task rates) into the snapshot cache."""
    p = next(x for x in fake.projects if x["id"] == project_id)
    await pool.execute(
        """
        INSERT INTO harvest_projects
            (harvest_id, name, client_id, client_name, client_currency,
             is_billable, is_active, is_fixed_fee, hourly_rate)
        VALUES ($1,$2,$3,$4,'USD',$5,$6,$7,$8)
        ON CONFLICT (harvest_id) DO UPDATE SET hourly_rate = EXCLUDED.hourly_rate
        """,
        p["id"], p["name"], p["client"]["id"], p["client"]["name"],
        p["is_billable"], p["is_active"], p["is_fixed_fee"], p["hourly_rate"],
    )
    for a in fake.task_assignments.get(project_id, []):
        await pool.execute(
            """
            INSERT INTO harvest_task_assignments
                (harvest_id, harvest_project_id, task_id, task_name, hourly_rate)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (harvest_id) DO NOTHING
            """,
            a["id"], project_id, a["task"]["id"], a["task"]["name"], a["hourly_rate"],
        )


def _cfg(**over) -> Settings:
    return settings.model_copy(update=over)


async def _estimate(pool, cfg, project_ids, **over):
    return await estimator.estimate_group(
        pool, cfg,
        project_ids=project_ids,
        period=over.pop("period", JULY),
        time_summary_type=over.pop("time_summary_type", "task"),
        include_expenses=over.pop("include_expenses", False),
        expense_summary_type=over.pop("expense_summary_type", None),
    )


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(ACME, "Acme Corp")
    f.add_project(PLATFORM, "Acme Platform", client_id=ACME)
    f.install(monkeypatch)
    return f


# ── Totals and summary types ────────────────────────────────────────────────


async def test_sums_billable_uninvoiced_time_at_entry_rate(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185)
    fake.add_time(PLATFORM, spent_date="2026-07-07", hours=6, rate=185)
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])

    assert est.total == pytest.approx(14 * 185)
    assert est.hours == 14


async def test_already_billed_and_non_billable_time_is_excluded(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185)
    fake.add_time(PLATFORM, spent_date="2026-07-07", hours=8, rate=185, is_billed=True)
    fake.add_time(PLATFORM, spent_date="2026-07-08", hours=8, rate=185, billable=False)
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])

    assert est.hours == 8
    assert est.total == pytest.approx(8 * 185)


async def test_summary_by_task_groups_line_items(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185,
                  task_id=1, task_name="Engineering")
    fake.add_time(PLATFORM, spent_date="2026-07-07", hours=5, rate=185,
                  task_id=1, task_name="Engineering")
    fake.add_time(PLATFORM, spent_date="2026-07-08", hours=4, rate=145,
                  task_id=2, task_name="QA")
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM], time_summary_type="task")

    labels = {li["label"]: li for li in est.line_items}
    assert set(labels) == {"Engineering", "QA"}
    assert labels["Engineering"]["quantity"] == 15
    assert labels["Engineering"]["amount"] == pytest.approx(15 * 185)
    assert labels["QA"]["amount"] == pytest.approx(4 * 145)


async def test_summary_by_people_groups_by_user(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185,
                  user_name="M. Alvarez")
    fake.add_time(PLATFORM, spent_date="2026-07-07", hours=6, rate=175,
                  user_name="T. Okafor")
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM], time_summary_type="people")

    assert {li["label"] for li in est.line_items} == {"M. Alvarez", "T. Okafor"}


async def test_multiple_projects_roll_into_one_estimate(fake):
    pool = await get_pool()
    fake.add_project(MOBILE, "Acme Mobile", client_id=ACME)
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    fake.add_time(MOBILE, spent_date="2026-07-06", hours=5, rate=185)
    await _sync_project(pool, fake, PLATFORM)
    await _sync_project(pool, fake, MOBILE)

    est = await _estimate(pool, _cfg(), [PLATFORM, MOBILE])
    assert est.hours == 15


# ── Rate ladder ─────────────────────────────────────────────────────────────


async def test_falls_back_to_project_hourly_rate(fake):
    pool = await get_pool()
    fake.projects[0]["hourly_rate"] = 165.0
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=None)
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])

    assert est.total == pytest.approx(10 * 165)
    assert est.unresolved_rate_entries == []


async def test_falls_back_to_task_assignment_rate(fake):
    """Last rung: no entry rate, no project rate, but the task has one."""
    pool = await get_pool()
    fake.add_task_assignment(PLATFORM, task_id=7, task_name="Design", hourly_rate=175.0)
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=4, rate=None, task_id=7,
                  task_name="Design")
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])

    assert est.total == pytest.approx(4 * 175)


async def test_entry_rate_wins_over_project_rate(fake):
    pool = await get_pool()
    fake.projects[0]["hourly_rate"] = 100.0
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=250.0)
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])
    assert est.total == pytest.approx(10 * 250)


async def test_unresolvable_rate_is_reported_not_silently_zero(fake):
    """A rate that resolves to nothing must surface, never quietly price the
    work at zero — that is how revenue disappears."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185)
    fake.add_time(PLATFORM, spent_date="2026-07-07", hours=3.5, rate=None,
                  task_id=9, task_name="Untitled", user_name="J. Whitcomb")
    await _sync_project(pool, fake, PLATFORM)  # project hourly_rate is None

    est = await _estimate(pool, _cfg(), [PLATFORM])

    assert len(est.unresolved_rate_entries) == 1
    assert est.total == pytest.approx(8 * 185)  # excludes the unpriced entry


# ── Rounding ────────────────────────────────────────────────────────────────


async def test_rounding_flag_changes_the_total(fake):
    """Both branches of `rates.USE_ROUNDED_HOURS`, patched rather than configured.

    The flag is a module constant, not a setting — it mirrors Harvest's own time
    preference, so it cannot differ per environment. Production is the False
    branch; the True branch is covered so that flipping the constant is a
    one-line change with a test already behind it.
    """
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=7.4, rounded_hours=7.5,
                  rate=200)
    await _sync_project(pool, fake, PLATFORM)

    with patch.object(rates, "USE_ROUNDED_HOURS", False):
        unrounded = await _estimate(pool, _cfg(), [PLATFORM])
    with patch.object(rates, "USE_ROUNDED_HOURS", True):
        rounded = await _estimate(pool, _cfg(), [PLATFORM])

    assert unrounded.total == pytest.approx(7.4 * 200)
    assert rounded.total == pytest.approx(7.5 * 200)


def test_rounding_is_off_in_production():
    """Guards the shipped value: it must match Harvest → Settings → Time.

    If this fails, either someone flipped the constant without changing Harvest,
    or Harvest changed and this test is the reminder to confirm which.
    """
    assert rates.USE_ROUNDED_HOURS is False


# ── Expenses ────────────────────────────────────────────────────────────────


async def test_expenses_are_included_only_when_configured(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=10, rate=185)
    fake.add_expense(PLATFORM, spent_date="2026-07-10", total_cost=2145.80)
    await _sync_project(pool, fake, PLATFORM)

    without = await _estimate(pool, _cfg(), [PLATFORM], include_expenses=False)
    with_exp = await _estimate(
        pool, _cfg(), [PLATFORM],
        include_expenses=True, expense_summary_type="category",
    )

    assert without.total == pytest.approx(1850)
    assert with_exp.total == pytest.approx(1850 + 2145.80)
    assert any(li["label"] == "Travel" for li in with_exp.line_items)


async def test_billed_expenses_are_excluded(fake):
    pool = await get_pool()
    fake.add_expense(PLATFORM, spent_date="2026-07-10", total_cost=500, is_billed=True)
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(
        pool, _cfg(), [PLATFORM],
        include_expenses=True, expense_summary_type="category",
    )
    assert est.total == 0


# ── Unapproved, straggler, and late time ────────────────────────────────────


async def test_unapproved_time_is_collected(fake):
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185,
                  approval_status="pending_approval")
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])
    assert len(est.unapproved_entries) == 1


async def test_straggler_time_before_the_period_is_detected(fake):
    """Time entered late for a prior month will not be swept up by the bounded
    import and would otherwise roll forward invisibly."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185)
    fake.add_time(PLATFORM, spent_date="2026-06-18", hours=18.5, rate=185)
    await _sync_project(pool, fake, PLATFORM)

    est = await _estimate(pool, _cfg(), [PLATFORM])

    assert est.straggler_hours == 18.5
    assert est.straggler_earliest == date(2026, 6, 18)
    # The straggler must not inflate the invoice estimate itself.
    assert est.total == pytest.approx(8 * 185)


async def test_time_queries_are_always_bounded(fake):
    """Omitting from/to makes Harvest pull *all* unbilled time regardless of
    date — the single most expensive mistake available here."""
    pool = await get_pool()
    fake.add_time(PLATFORM, spent_date="2026-07-06", hours=8, rate=185)
    await _sync_project(pool, fake, PLATFORM)

    await _estimate(pool, _cfg(), [PLATFORM])

    queries = fake.calls_to("list_time_entries")
    assert queries
    for q in queries:
        assert q["from"] and q["to"]


async def test_no_projects_yields_an_empty_estimate(fake):
    pool = await get_pool()
    est = await _estimate(pool, _cfg(), [])
    assert est.total == 0 and est.line_items == []
