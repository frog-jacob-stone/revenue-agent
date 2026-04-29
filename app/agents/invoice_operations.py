import logging
from datetime import date, timedelta
from typing import Any, ClassVar
from uuid import UUID

from app.agents.base import ConversationalAgent
from app.config import settings
from app.integrations import harvest

logger = logging.getLogger(__name__)

_TERM_DAYS: dict[str, int] = {
    "net0": 0,
    "net15": 15,
    "net30": 30,
    "net45": 45,
    "net60": 60,
}


def _due_date(issue_date: str, payment_term: str) -> str:
    days = _TERM_DAYS.get(payment_term.replace(" ", "").lower(), 30)
    return (date.fromisoformat(issue_date) + timedelta(days=days)).isoformat()


class InvoiceOperationsAgent(ConversationalAgent):
    slug = "invoice-operations"
    name = "Invoice Operations"
    description = (
        "Generates draft invoices from Harvest time tracking data. "
        "Triggered via chat. Proposes actions for human approval before anything is created."
    )
    requires_approval = True
    allowed_tools: ClassVar[tuple[str, ...]] = (
        "list_harvest_clients",
        "list_client_projects",
        "trigger_invoice_generation",
    )

    # ── Conversational identity ──────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        today = date.today().isoformat()
        return f"""You are an invoice operations assistant for Frogslayer, a software consulting firm.
You help generate draft invoices from Harvest time tracking data.

Today's date is {today}.

## Capabilities
- List Harvest clients and their projects
- Generate a draft invoice for a client and billing period (creates a proposed action for human approval)

## Behavioral guidance
- Always confirm the client name and billing period before triggering invoice generation.
- If a client has multiple projects, ask which project(s) to include unless the user specified.
- Invoice generation creates a proposed action in the Approval Inbox — nothing is sent to the client until approved and then separately sent.
- You cannot send or delete invoices in this version. Direct the user to Harvest for those actions.
- If asked to do anything other than listing clients/projects or generating a draft, say so clearly.

## Billing models
- T&M projects: line items are derived from Harvest time entries grouped by task.
- Fixed fee projects: a single line item for the project budget amount.
- The billing model is determined by the Harvest project's bill_by field (none = fixed fee, anything else = T&M).
"""

    # ── Workflow execution ───────────────────────────────────────────────────

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        # Monthly sweep scaffold — disabled in v1.
        # To enable: remove this guard, set up the n8n schedule to call
        # POST /agents/invoice-operations/trigger with {"sweep_mode": true}.
        if context.get("sweep_mode"):
            raise NotImplementedError(
                "invoice-operations: monthly sweep is scaffolded but not active in v1. "
                "Remove this guard to enable."
            )

        client_id = context.get("client_id")
        period_start = context.get("period_start")
        period_end = context.get("period_end")

        if not client_id or not period_start or not period_end:
            raise ValueError(
                "invoice-operations requires client_id, period_start, and period_end in context"
            )

        client_id = int(client_id)
        project_id = context.get("project_id")

        # Fetch client record for name and payment terms
        client = await harvest.get_client(settings, client_id)
        client_name = client.get("name", str(client_id))

        # Resolve projects to include
        if project_id:
            projects = [await harvest.get_project(settings, int(project_id))]
        else:
            all_projects = await harvest.get_active_projects(settings)
            projects = [
                p for p in all_projects
                if (p.get("client") or {}).get("id") == client_id
            ]

        if not projects:
            raise ValueError(
                f"No active Harvest projects found for client '{client_name}' (id={client_id})"
            )

        line_items: list[dict[str, Any]] = []
        reasoning_parts: list[str] = []

        for project in projects:
            bill_by = project.get("bill_by", "none")
            project_name = project.get("name", "Unknown")
            project_harvest_id = project["id"]

            if bill_by == "none":
                # Fixed fee — use project budget as the single line item
                budget = float(project.get("budget") or 0)
                if budget <= 0:
                    reasoning_parts.append(
                        f"{project_name}: skipped (bill_by=none but budget is unset)"
                    )
                    continue
                line_items.append({
                    "project_id": project_harvest_id,
                    "kind": "Service",
                    "description": project_name,
                    "unit_price": budget,
                    "quantity": 1,
                    "amount": budget,
                    "_billing_model": "fixed_fee",
                    "_project_name": project_name,
                })
                reasoning_parts.append(f"{project_name}: fixed fee ${budget:,.2f}")
            else:
                # T&M — group billable time entries by task
                entries = await harvest.get_time_entries_for_period(
                    settings, project_harvest_id, period_start, period_end
                )
                billable = [e for e in entries if e.get("billable")]
                if not billable:
                    reasoning_parts.append(
                        f"{project_name}: no billable entries between {period_start} and {period_end}"
                    )
                    continue

                task_groups: dict[str, dict[str, Any]] = {}
                for entry in billable:
                    task_name = (entry.get("task") or {}).get("name", "General")
                    rate = float(entry.get("billable_rate") or 0)
                    hours = float(entry.get("hours") or 0)
                    if task_name not in task_groups:
                        task_groups[task_name] = {"hours": 0.0, "rate": rate}
                    task_groups[task_name]["hours"] = round(
                        task_groups[task_name]["hours"] + hours, 4
                    )

                for task_name, data in task_groups.items():
                    hours = round(data["hours"], 2)
                    rate = data["rate"]
                    if hours <= 0:
                        continue
                    amount = round(hours * rate, 2)
                    line_items.append({
                        "project_id": project_harvest_id,
                        "kind": "Service",
                        "description": f"{project_name} — {task_name}",
                        "unit_price": rate,
                        "quantity": hours,
                        "amount": amount,
                        "_billing_model": "time_and_materials",
                        "_project_name": project_name,
                        "_task_name": task_name,
                    })

                total_hours = round(sum(t["hours"] for t in task_groups.values()), 2)
                reasoning_parts.append(
                    f"{project_name}: T&M, {total_hours}h across {len(task_groups)} task(s)"
                )

        if not line_items:
            raise ValueError(
                f"No billable line items found for '{client_name}' "
                f"between {period_start} and {period_end}"
            )

        subtotal = round(sum(li["amount"] for li in line_items), 2)
        payment_term = client.get("payment_term") or "net30"
        issue_date = period_end
        due = _due_date(issue_date, payment_term)

        harvest_line_items = [
            {k: v for k, v in li.items() if not k.startswith("_")}
            for li in line_items
        ]
        harvest_payload = {
            "client_id": client_id,
            "issue_date": issue_date,
            "due_date": due,
            "payment_term": payment_term,
            "line_items": harvest_line_items,
            "notes": context.get("notes", ""),
        }

        reasoning = (
            f"Invoice for {client_name} covering {period_start} to {period_end}.\n"
            + "\n".join(f"  \u2022 {r}" for r in reasoning_parts)
        )

        logger.info(
            "invoice-operations: proposing generate_invoice for %s, %s\u2013%s, $%.2f",
            client_name,
            period_start,
            period_end,
            subtotal,
        )

        return [
            {
                "action_type": "generate_invoice",
                "summary": (
                    f"Generate invoice for {client_name} — "
                    f"{period_start} to {period_end} — ${subtotal:,.2f}"
                ),
                "proposed_payload": {
                    "harvest_payload": harvest_payload,
                    "client_name": client_name,
                    "client_id": client_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "line_items": line_items,
                    "subtotal": subtotal,
                    "payment_term": payment_term,
                    "due_date": due,
                },
                "reasoning": reasoning,
                "risk_level": "medium",
            }
        ]
