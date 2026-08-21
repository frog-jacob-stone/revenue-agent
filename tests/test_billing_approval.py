"""Per-group approval of a planned run.

Approval is a decision about whether an invoice is allowed to exist. It has to
survive a reload, and it has to start at "no".
"""
from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest

from app.config import settings
from app.db import get_pool
from app.services.billing import groups as groups_service
from app.services.billing import harvest_snapshot, planner, review
from tests.fakes.harvest import FakeHarvest

CLIENT = 5735833
HOSTING = 14308510
APP = 14308511

AUGUST = date(2026, 8, 1)


@pytest.fixture
async def fake(monkeypatch):
    f = FakeHarvest()
    f.add_client(CLIENT, "Brightline Logistics")
    f.add_project(HOSTING, "Brightline Hosting", client_id=CLIENT, is_fixed_fee=True)
    f.add_project(APP, "Brightline App Support", client_id=CLIENT, is_fixed_fee=True)
    f.install(monkeypatch)
    await harvest_snapshot.refresh_snapshot(await get_pool(), settings)
    return f


async def _group(pool, name: str, project_id: int, amount: float):
    return await groups_service.create_group(pool, {
        "name": name,
        "harvest_client_id": CLIENT,
        "billing_type": "recurring_monthly",
        "billing_timing": "advance",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": project_id}],
        "recurring_items": [{
            "harvest_project_id": project_id,
            "description": "Monthly fee",
            "quantity": 1,
            "unit_price": amount,
            "kind": "Service",
            "is_placeholder": False,
        }],
    })


@pytest.fixture
async def run(fake):
    """A planned run with two groups, one per project."""
    pool = await get_pool()
    await _group(pool, "Brightline — Hosting", HOSTING, 1500)
    await _group(pool, "Brightline — App", APP, 4200)
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    return await planner.get_run(pool, run_id)


def _items(detail) -> list[dict]:
    return [i for i in detail["items"] if i["status"] != "skipped"]


async def _flag(item_id: UUID, run_id: UUID, code: str, severity: str = "error"):
    """Attach a flag directly — the gate under test is severity, not the
    particular condition that produced it."""
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO billing_run_flags
            (billing_run_id, billing_run_item_id, code, severity, message, context)
        VALUES ($1,$2,$3,$4::billing_flag_severity,'seeded by test','{}'::jsonb)
        """,
        run_id, item_id, code, severity,
    )


# ── The default ─────────────────────────────────────────────────────────────


async def test_nothing_is_approved_when_a_run_is_planned(run):
    """The pre-flight is a review, not a rubber stamp."""
    items = _items(run)
    assert len(items) == 2
    for item in items:
        assert item["status"] == "planned"
        assert item["approved_at"] is None
        assert item["approved_by"] is None
        assert item["error_override"] is False


# ── Persistence ─────────────────────────────────────────────────────────────


async def test_approval_survives_a_reload(run):
    pool = await get_pool()
    target = _items(run)[0]

    assert await review.set_item_approval(
        pool, run["id"], target["id"], approved=True, actor="jacob@frogslayer.com"
    )

    fresh = await planner.get_run(pool, run["id"])
    approved = next(i for i in fresh["items"] if i["id"] == target["id"])
    other = next(i for i in fresh["items"] if i["id"] != target["id"])

    assert approved["status"] == "approved"
    assert approved["approved_at"] is not None
    assert approved["approved_by"] == "jacob@frogslayer.com"
    # Approving one group must not approve its neighbour.
    assert other["status"] == "planned"


async def test_unapproving_clears_the_stamp(run):
    pool = await get_pool()
    target = _items(run)[0]
    await review.set_item_approval(pool, run["id"], target["id"], approved=True)
    await review.set_item_approval(pool, run["id"], target["id"], approved=False)

    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == target["id"])
    assert item["status"] == "planned"
    assert item["approved_at"] is None
    assert item["approved_by"] is None


async def test_re_approving_is_idempotent(run):
    pool = await get_pool()
    target = _items(run)[0]
    await review.set_item_approval(pool, run["id"], target["id"], approved=True)
    await review.set_item_approval(pool, run["id"], target["id"], approved=True)

    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == target["id"])
    assert item["status"] == "approved"


async def test_unknown_item_is_not_found(run):
    pool = await get_pool()
    missing = UUID("00000000-0000-0000-0000-000000000001")
    assert await review.set_item_approval(
        pool, run["id"], missing, approved=True
    ) is False


# ── Error flags ─────────────────────────────────────────────────────────────


async def test_error_flag_blocks_approval_until_overridden(run):
    pool = await get_pool()
    target = _items(run)[0]
    await _flag(target["id"], run["id"], "AMOUNT_VARIANCE")

    with pytest.raises(review.ApprovalError, match="override"):
        await review.set_item_approval(pool, run["id"], target["id"], approved=True)

    await review.set_item_approval(pool, run["id"], target["id"], override=True)
    await review.set_item_approval(pool, run["id"], target["id"], approved=True)

    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == target["id"])
    assert item["status"] == "approved"
    assert item["error_override"] is True


async def test_override_is_sticky_across_unapproving(run):
    """Toggling the checkbox off and on must not make the operator re-accept a
    flag they already accepted."""
    pool = await get_pool()
    target = _items(run)[0]
    await _flag(target["id"], run["id"], "AMOUNT_VARIANCE")

    await review.set_item_approval(pool, run["id"], target["id"], override=True, approved=True)
    await review.set_item_approval(pool, run["id"], target["id"], approved=False)
    await review.set_item_approval(pool, run["id"], target["id"], approved=True)

    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == target["id"])
    assert item["status"] == "approved"
    assert item["error_override"] is True


async def test_warning_flags_do_not_block_approval(run):
    pool = await get_pool()
    target = _items(run)[0]
    await _flag(target["id"], run["id"], "UNAPPROVED_TIME", severity="warning")

    await review.set_item_approval(pool, run["id"], target["id"], approved=True)
    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == target["id"])
    assert item["status"] == "approved"


async def test_in_flight_can_never_be_approved_or_overridden(run):
    """The one flag whose override risks billing a client twice."""
    pool = await get_pool()
    target = _items(run)[0]
    await _flag(target["id"], run["id"], "UNRESOLVED_IN_FLIGHT")

    with pytest.raises(review.ApprovalError, match="cannot be overridden"):
        await review.set_item_approval(pool, run["id"], target["id"], override=True)
    with pytest.raises(review.ApprovalError, match="not.*overridable"):
        await review.set_item_approval(pool, run["id"], target["id"], approved=True)

    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == target["id"])
    assert item["status"] == "planned"
    assert item["error_override"] is False


# ── Bulk ────────────────────────────────────────────────────────────────────


async def test_bulk_approve_touches_only_approvable_groups(run):
    pool = await get_pool()
    blocked, clean = _items(run)
    await _flag(blocked["id"], run["id"], "AMOUNT_VARIANCE")

    changed = await review.set_all_approvals(pool, run["id"], approved=True)
    assert changed == 1

    fresh = await planner.get_run(pool, run["id"])
    by_id = {i["id"]: i for i in fresh["items"]}
    assert by_id[clean["id"]]["status"] == "approved"
    assert by_id[blocked["id"]]["status"] == "planned"


async def test_bulk_approve_includes_already_overridden_groups(run):
    pool = await get_pool()
    overridden, _clean = _items(run)
    await _flag(overridden["id"], run["id"], "AMOUNT_VARIANCE")
    await review.set_item_approval(pool, run["id"], overridden["id"], override=True)

    assert await review.set_all_approvals(pool, run["id"], approved=True) == 2


async def test_bulk_approve_never_sweeps_in_a_non_overridable_flag(run):
    pool = await get_pool()
    in_flight, _clean = _items(run)
    await _flag(in_flight["id"], run["id"], "UNRESOLVED_IN_FLIGHT")
    # Even with the override column somehow set, the flag itself is disqualifying.
    await pool.execute(
        "UPDATE billing_run_items SET error_override = true WHERE id = $1",
        in_flight["id"],
    )

    assert await review.set_all_approvals(pool, run["id"], approved=True) == 1
    fresh = await planner.get_run(pool, run["id"])
    item = next(i for i in fresh["items"] if i["id"] == in_flight["id"])
    assert item["status"] == "planned"


async def test_bulk_clear_unapproves_everything(run):
    pool = await get_pool()
    await review.set_all_approvals(pool, run["id"], approved=True)
    assert await review.set_all_approvals(pool, run["id"], approved=False) == 2

    fresh = await planner.get_run(pool, run["id"])
    assert all(i["status"] == "planned" for i in _items(fresh))


# ── Run state ───────────────────────────────────────────────────────────────


async def test_approval_is_refused_once_the_run_is_abandoned(run):
    pool = await get_pool()
    target = _items(run)[0]
    await planner.abandon_run(pool, run["id"])

    with pytest.raises(review.ApprovalError, match="under review"):
        await review.set_item_approval(pool, run["id"], target["id"], approved=True)
    with pytest.raises(review.ApprovalError, match="under review"):
        await review.set_all_approvals(pool, run["id"], approved=True)


async def test_skipped_groups_cannot_be_approved(fake):
    pool = await get_pool()
    await groups_service.create_group(pool, {
        "name": "Brightline — Draw schedule",
        "harvest_client_id": CLIENT,
        "billing_type": "fixed_fee_schedule",
        "projects": [{"harvest_project_id": HOSTING}],
    })
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    detail = await planner.get_run(pool, run_id)
    skipped = next(i for i in detail["items"] if i["status"] == "skipped")

    with pytest.raises(review.ApprovalError, match="skipped"):
        await review.set_item_approval(pool, run_id, skipped["id"], approved=True)


async def test_re_planning_starts_the_review_over(run):
    """A fresh plan is a fresh decision — approvals from the abandoned run must
    not carry across."""
    pool = await get_pool()
    await review.set_all_approvals(pool, run["id"], approved=True)

    new_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    fresh = await planner.get_run(pool, new_id)
    assert all(i["status"] == "planned" for i in _items(fresh))


# ── Router ──────────────────────────────────────────────────────────────────


async def test_approval_endpoints_require_auth(run, unauthed_client):
    item = _items(run)[0]
    for path in (
        f"/billing/runs/{run['id']}/items/{item['id']}/approval",
        f"/billing/runs/{run['id']}/approval",
    ):
        res = await unauthed_client.post(path, json={"approved": True})
        assert res.status_code in (401, 403), f"POST {path} was not protected"


async def test_approving_through_the_api_returns_the_updated_run(run, client):
    item = _items(run)[0]
    res = await client.post(
        f"/billing/runs/{run['id']}/items/{item['id']}/approval",
        json={"approved": True},
    )
    assert res.status_code == 200
    body = res.json()
    updated = next(i for i in body["items"] if i["id"] == str(item["id"]))
    assert updated["status"] == "approved"
    assert updated["approved_by"]


async def test_api_refuses_a_non_overridable_approval_with_409(run, client):
    item = _items(run)[0]
    await _flag(item["id"], run["id"], "UNRESOLVED_IN_FLIGHT")

    res = await client.post(
        f"/billing/runs/{run['id']}/items/{item['id']}/approval",
        json={"approved": True},
    )
    assert res.status_code == 409
    assert "UNRESOLVED_IN_FLIGHT" in res.json()["detail"]


# ── Undecided placeholders block approval, with no way through ──────────────


async def _placeholder_group(pool, name: str, project_id: int):
    """A group with one fixed line and one placeholder."""
    return await groups_service.create_group(pool, {
        "name": name,
        "harvest_client_id": CLIENT,
        "billing_type": "recurring_monthly",
        "billing_timing": "advance",
        "payment_term": "net 30",
        "projects": [{"harvest_project_id": project_id}],
        "recurring_items": [
            {
                "harvest_project_id": project_id,
                "description": "Monthly fee",
                "quantity": 1, "unit_price": 1500,
                "kind": "Service", "is_placeholder": False,
            },
            {
                "harvest_project_id": project_id,
                "description": "Hosting pass-through",
                "quantity": 1, "unit_price": 0,
                "kind": "Billable Expense", "is_placeholder": True,
            },
        ],
    })


@pytest.fixture
async def placeholder_run(fake):
    """One group carrying an undecided placeholder, and one clean group."""
    pool = await get_pool()
    await _placeholder_group(pool, "Brightline — Hosting", HOSTING)
    await _group(pool, "Brightline — App", APP, 4200)
    run_id = await planner.plan_run(pool, settings, run_month=AUGUST)
    return await planner.get_run(pool, run_id)


def _by_name(detail, name: str) -> dict:
    return next(i for i in detail["items"] if i["billing_group_name"] == name)


def _line_id(item, label: str) -> str:
    return next(
        li["recurring_line_item_id"] for li in item["estimated_line_items"]
        if li["label"] == label
    )


async def test_an_undecided_placeholder_refuses_approval(placeholder_run):
    pool = await get_pool()
    item = _by_name(placeholder_run, "Brightline — Hosting")

    with pytest.raises(review.ApprovalError, match="Hosting pass-through"):
        await review.set_item_approval(
            pool, placeholder_run["id"], item["id"], approved=True,
        )


async def test_an_override_does_not_unlock_a_placeholder(placeholder_run):
    """The one gate with no override path. An override is a way to forget with a
    click, and not forgetting is the whole purpose of a placeholder."""
    pool = await get_pool()
    item = _by_name(placeholder_run, "Brightline — Hosting")

    await review.set_item_approval(
        pool, placeholder_run["id"], item["id"], override=True,
    )
    with pytest.raises(review.ApprovalError, match="Not overridable"):
        await review.set_item_approval(
            pool, placeholder_run["id"], item["id"], approved=True,
        )


async def test_pricing_the_placeholder_unlocks_approval(placeholder_run):
    pool = await get_pool()
    from app.services.billing import placeholders

    item = _by_name(placeholder_run, "Brightline — Hosting")
    await placeholders.set_resolution(
        pool, placeholder_run["id"], item["id"],
        _line_id(item, "Hosting pass-through"),
        resolution="amount", unit_price=1240,
    )

    assert await review.set_item_approval(
        pool, placeholder_run["id"], item["id"], approved=True, actor="jacob",
    )
    fresh = _by_name(
        await planner.get_run(pool, placeholder_run["id"]), "Brightline — Hosting"
    )
    assert fresh["status"] == "approved"


async def test_omitting_the_placeholder_also_unlocks_approval(placeholder_run):
    """Omitting is a decision, so it satisfies the gate just as a price does."""
    pool = await get_pool()
    from app.services.billing import placeholders

    item = _by_name(placeholder_run, "Brightline — Hosting")
    await placeholders.set_resolution(
        pool, placeholder_run["id"], item["id"],
        _line_id(item, "Hosting pass-through"), resolution="omitted",
    )

    assert await review.set_item_approval(
        pool, placeholder_run["id"], item["id"], approved=True, actor="jacob",
    )


async def test_clearing_a_decision_re_blocks_approval(placeholder_run):
    pool = await get_pool()
    from app.services.billing import placeholders

    item = _by_name(placeholder_run, "Brightline — Hosting")
    line_id = _line_id(item, "Hosting pass-through")
    await placeholders.set_resolution(
        pool, placeholder_run["id"], item["id"], line_id,
        resolution="amount", unit_price=1240,
    )
    await placeholders.clear_resolution(
        pool, placeholder_run["id"], item["id"], line_id,
    )

    with pytest.raises(review.ApprovalError, match="placeholder line item"):
        await review.set_item_approval(
            pool, placeholder_run["id"], item["id"], approved=True,
        )


async def test_bulk_approve_skips_the_blocked_group_and_approves_the_rest(
    placeholder_run,
):
    """Consistent with how bulk already treats an un-overridden error flag: it
    approves what is approvable rather than failing the whole batch."""
    pool = await get_pool()

    changed = await review.set_all_approvals(
        pool, placeholder_run["id"], approved=True, actor="jacob",
    )
    assert changed == 1

    detail = await planner.get_run(pool, placeholder_run["id"])
    assert _by_name(detail, "Brightline — Hosting")["status"] == "planned"
    assert _by_name(detail, "Brightline — App")["status"] == "approved"


async def test_un_approving_is_never_blocked(placeholder_run):
    """You can always retreat. Only moving *to* approved is gated."""
    pool = await get_pool()
    from app.services.billing import placeholders

    item = _by_name(placeholder_run, "Brightline — Hosting")
    line_id = _line_id(item, "Hosting pass-through")
    await placeholders.set_resolution(
        pool, placeholder_run["id"], item["id"], line_id,
        resolution="amount", unit_price=1240,
    )
    await review.set_item_approval(
        pool, placeholder_run["id"], item["id"], approved=True, actor="jacob",
    )
    await placeholders.clear_resolution(
        pool, placeholder_run["id"], item["id"], line_id,
    )

    # Resolving already un-approved it; un-approving again is a no-op, not an error.
    assert await review.set_item_approval(
        pool, placeholder_run["id"], item["id"], approved=False,
    )


async def test_api_refuses_an_undecided_placeholder_with_409(placeholder_run, client):
    item = _by_name(placeholder_run, "Brightline — Hosting")

    res = await client.post(
        f"/billing/runs/{placeholder_run['id']}/items/{item['id']}/approval",
        json={"approved": True},
    )
    assert res.status_code == 409
    assert "Hosting pass-through" in res.json()["detail"]
