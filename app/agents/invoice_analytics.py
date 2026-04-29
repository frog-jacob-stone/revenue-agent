import logging
from datetime import date
from typing import Any, ClassVar
from uuid import UUID

from app.agents.base import ConversationalAgent

logger = logging.getLogger(__name__)


class InvoiceAnalyticsAgent(ConversationalAgent):
    slug = "invoice-analytics"
    name = "Invoice Analytics"
    description = (
        "Read-only chat agent for querying invoice and billing data from Harvest. "
        "Answers questions about outstanding AR, payment history, unbilled time, and pending approvals. "
        "Never proposes actions."
    )
    requires_approval = False
    allowed_tools: ClassVar[tuple[str, ...]] = (
        "list_harvest_clients",
        "list_invoices",
        "get_invoice_details",
        "get_unbilled_time_entries",
        "get_pending_invoice_approvals",
    )

    # ── Conversational identity ──────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        today = date.today().isoformat()
        return f"""You are a read-only invoice analytics assistant for Frogslayer, a software consulting firm.
You answer questions about invoices and billing using Harvest data.

Today's date is {today}.

## Capabilities
- List and summarize invoices (by client, status, date range)
- Retrieve details of a specific invoice including line items
- Find unbilled time entries
- Report on invoices currently awaiting approval in the system

## Behavioral guidance
- You are READ-ONLY. You cannot create, send, or delete invoices. If asked to do so, decline and direct the user to Invoice Operations.
- If data is missing or the question requires a system you don't have access to, say so explicitly — never return zero when the correct answer is "I don't have this data."
- If a question is ambiguous, ask one clarifying question rather than guessing.
- When a client is on net-45 terms, a 35-day-old invoice is NOT past due — always apply stored terms before flagging overdue status.

## Invoice statuses in Harvest
- draft: created but not sent
- open: sent, awaiting payment
- paid: payment received
- closed: written off

## What you log
Every answer you give is logged to the audit trail as agent.queried. This is expected behavior.
"""

    # ── Workflow execution ───────────────────────────────────────────────────

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError(
            "invoice-analytics is a read-only chat agent and cannot be triggered as a workflow."
        )
