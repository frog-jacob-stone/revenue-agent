"""Test fake for the Harvest API.

Patches the module-level functions the billing services call, so tests exercise
the real service code paths without any network.

Reads are scenario data. The single write — `create_invoice` — is scriptable, so a
test can make it succeed, be refused, or never return::

    fake.fail_create_invoice(harvest.HarvestValidationError("422", body={...}))
    fake.fail_create_invoice(httpx.TimeoutException("timed out"))

Created invoices land on `.created_invoices` so a test can assert **how many
POSTs happened**, which is the number that becomes duplicate money.

Build a scenario, then install it::

    fake = FakeHarvest()
    fake.add_project(14307913, "Acme Platform", client_id=5735774)
    fake.add_time(14307913, spent_date="2026-07-06", hours=8, rate=185)
    fake.install(monkeypatch)

Every recorded request lands on `.calls` so a test can assert what was asked
for — particularly that `from`/`to` are always bounded.
"""
from __future__ import annotations

from typing import Any

from app.integrations import harvest as _real


class FakeHarvest:
    # `install()` replaces the whole `harvest` module reference inside each
    # service, so these names must keep meaning what they mean in production —
    # `draws.invoice_draw` catches `harvest.HarvestValidationError` by attribute
    # lookup, and a fake that shadowed it with anything else would silently route
    # a 4xx into the unknown-outcome branch.
    HarvestError = _real.HarvestError
    HarvestAuthError = _real.HarvestAuthError
    HarvestNotFoundError = _real.HarvestNotFoundError
    HarvestValidationError = _real.HarvestValidationError
    HarvestRateLimited = _real.HarvestRateLimited
    HarvestServerError = _real.HarvestServerError
    def __init__(self) -> None:
        self.clients: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        # Mirrors the categories on the real Frogslayer Harvest account, so
        # `kind` validation in tests matches what production will accept.
        self.categories: list[dict[str, Any]] = [
            {"id": 1, "name": "Service"},
            {"id": 2, "name": "Billable Expense"},
            {"id": 3, "name": "Discount"},
            {"id": 4, "name": "Advanced Deposit"},
        ]
        self.task_assignments: dict[int, list[dict[str, Any]]] = {}
        # project_id -> entries
        self.time_entries: dict[int, list[dict[str, Any]]] = {}
        self.expenses: dict[int, list[dict[str, Any]]] = {}
        # client_id -> invoices
        self.invoices: dict[int, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        # ── Write state ─────────────────────────────────────────────────────
        # Every payload that reached create_invoice, in order.
        self.created_invoices: list[dict[str, Any]] = []
        self._next_invoice_id = 9000
        self._create_failures: list[BaseException] = []
        # Set to make the created invoice's amount differ from what was asked
        # for, which is how a variance is produced.
        self.create_invoice_amount: float | None = None

    # ── Scenario building ───────────────────────────────────────────────────

    def add_client(self, client_id: int, name: str, currency: str = "USD") -> None:
        self.clients.append({
            "id": client_id, "name": name, "currency": currency, "is_active": True,
        })

    def add_project(
        self, project_id: int, name: str, *, client_id: int,
        client_name: str | None = None, is_billable: bool = True,
        is_active: bool = True, is_fixed_fee: bool = False,
        hourly_rate: float | None = None, currency: str = "USD",
    ) -> None:
        client = next((c for c in self.clients if c["id"] == client_id), None)
        self.projects.append({
            "id": project_id,
            "name": name,
            "code": None,
            "client": {
                "id": client_id,
                "name": client_name or (client["name"] if client else f"Client {client_id}"),
                "currency": currency,
            },
            "is_billable": is_billable,
            "is_active": is_active,
            "is_fixed_fee": is_fixed_fee,
            "bill_by": "People",
            "hourly_rate": hourly_rate,
            "fee": None,
            "budget": None,
            "budget_by": None,
            "budget_is_monthly": False,
        })

    def add_task_assignment(
        self, project_id: int, *, task_id: int, task_name: str, hourly_rate: float
    ) -> None:
        self.task_assignments.setdefault(project_id, []).append({
            "id": project_id * 1000 + task_id,
            "task": {"id": task_id, "name": task_name},
            "hourly_rate": hourly_rate,
            "is_active": True,
        })

    def add_time(
        self, project_id: int, *, spent_date: str, hours: float,
        rate: float | None = None, rounded_hours: float | None = None,
        billable: bool = True, is_billed: bool = False,
        task_id: int = 1, task_name: str = "Engineering",
        user_name: str = "M. Alvarez", approval_status: str = "approved",
        project_name: str | None = None,
    ) -> None:
        project = next((p for p in self.projects if p["id"] == project_id), None)
        self.time_entries.setdefault(project_id, []).append({
            "id": len(self.time_entries.get(project_id, [])) + 1,
            "spent_date": spent_date,
            "hours": hours,
            "rounded_hours": rounded_hours if rounded_hours is not None else hours,
            "billable": billable,
            "is_billed": is_billed,
            "billable_rate": rate,
            "approval_status": approval_status,
            "user": {"id": 1, "name": user_name},
            "task": {"id": task_id, "name": task_name},
            "project": {
                "id": project_id,
                "name": project_name or (project["name"] if project else "Project"),
            },
        })

    def add_expense(
        self, project_id: int, *, spent_date: str, total_cost: float,
        category: str = "Travel", billable: bool = True, is_billed: bool = False,
    ) -> None:
        project = next((p for p in self.projects if p["id"] == project_id), None)
        self.expenses.setdefault(project_id, []).append({
            "id": len(self.expenses.get(project_id, [])) + 1,
            "spent_date": spent_date,
            "total_cost": total_cost,
            "billable": billable,
            "is_billed": is_billed,
            "expense_category": {"id": 1, "name": category},
            "user": {"id": 1, "name": "M. Alvarez"},
            "project": {
                "id": project_id,
                "name": project["name"] if project else "Project",
            },
        })

    def add_invoice(
        self, client_id: int, *, invoice_id: int, number: str,
        issue_date: str, amount: float,
    ) -> None:
        self.invoices.setdefault(client_id, []).append({
            "id": invoice_id, "number": number,
            "issue_date": issue_date, "amount": amount, "state": "draft",
        })

    # ── Fake API surface ────────────────────────────────────────────────────

    async def get_clients(self, cfg):
        self.calls.append(("get_clients", {}))
        return list(self.clients)

    async def list_projects_detailed(self, cfg, *, is_active=True):
        self.calls.append(("list_projects_detailed", {"is_active": is_active}))
        if is_active is None:
            return list(self.projects)
        return [p for p in self.projects if p["is_active"] is is_active]

    async def get_invoice_item_categories(self, cfg):
        self.calls.append(("get_invoice_item_categories", {}))
        return list(self.categories)

    async def get_task_assignments(self, cfg, project_id):
        self.calls.append(("get_task_assignments", {"project_id": project_id}))
        return list(self.task_assignments.get(project_id, []))

    async def list_time_entries(self, cfg, *, project_id, from_, to):
        self.calls.append((
            "list_time_entries",
            {"project_id": project_id, "from": from_, "to": to},
        ))
        return [
            e for e in self.time_entries.get(project_id, [])
            if from_ <= e["spent_date"] <= to
        ]

    async def list_time_entries_all(self, cfg, *, from_, to):
        self.calls.append(("list_time_entries_all", {"from": from_, "to": to}))
        return [
            e
            for entries in self.time_entries.values()
            for e in entries
            if from_ <= e["spent_date"] <= to
        ]

    async def list_expenses(self, cfg, *, project_id, from_, to):
        self.calls.append((
            "list_expenses", {"project_id": project_id, "from": from_, "to": to},
        ))
        return [
            e for e in self.expenses.get(project_id, [])
            if from_ <= e["spent_date"] <= to
        ]

    async def list_invoices(self, cfg, *, client_id, from_, to):
        self.calls.append((
            "list_invoices", {"client_id": client_id, "from": from_, "to": to},
        ))
        return [
            i for i in self.invoices.get(client_id, [])
            if from_ <= i["issue_date"] <= to
        ]

    # ── The one write ───────────────────────────────────────────────────────

    def fail_create_invoice(self, exc: BaseException) -> None:
        """Queue one failure for the next `create_invoice` call.

        Queued rather than sticky so a test can script "fails, then succeeds"
        without a mutable flag — and so an unexpected extra POST hits the happy
        path and shows up in `created_invoices`, where an assertion catches it.
        """
        self._create_failures.append(exc)

    async def create_invoice(self, cfg, payload):
        self.calls.append(("create_invoice", dict(payload)))
        if self._create_failures:
            raise self._create_failures.pop(0)

        self._next_invoice_id += 1
        requested = sum(
            float(li.get("unit_price") or 0) * float(li.get("quantity") or 1)
            for li in payload.get("line_items", [])
        )
        invoice = {
            "id": self._next_invoice_id,
            "number": f"INV-{self._next_invoice_id}",
            "amount": (
                self.create_invoice_amount
                if self.create_invoice_amount is not None
                else round(requested, 2)
            ),
            "state": "draft",
            "client": {"id": payload.get("client_id")},
        }
        self.created_invoices.append({"payload": dict(payload), "invoice": invoice})
        return invoice

    # ── Installation ────────────────────────────────────────────────────────

    _TARGETS = (
        "app.services.billing.harvest_snapshot.harvest",
        "app.services.billing.reconcile.harvest",
        "app.services.billing.estimator.harvest",
        "app.services.billing.duplicate_guard.harvest",
        "app.services.billing.draws.harvest",
    )

    def install(self, monkeypatch) -> "FakeHarvest":
        """Patch the `harvest` module reference inside every billing service."""
        for target in self._TARGETS:
            monkeypatch.setattr(target, self, raising=True)
        return self

    def calls_to(self, name: str) -> list[dict[str, Any]]:
        return [params for called, params in self.calls if called == name]
