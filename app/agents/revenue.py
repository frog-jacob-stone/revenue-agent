import logging
from datetime import date
from typing import ClassVar

from app.agents.base import ConversationalAgent

logger = logging.getLogger(__name__)


class RevenueRecognitionAgent(ConversationalAgent):
    slug = "revenue-recognition"
    name = "Revenue Recognition"
    description = (
        "Runs monthly revenue recognition calculations and writes journal entries."
    )
    requires_approval = True
    allowed_tools: ClassVar[tuple[str, ...]] = (
        "get_revenue_data",
        "trigger_revenue_recognition",
    )

    # ── Conversational identity ──────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        today = date.today().isoformat()
        return f"""You are a revenue operations assistant for Frogslayer, a software consulting firm.
You help the revenue team understand revenue trends and manage the recognition process.

Today's date is {today}.

## Behavioral guidance

When calling get_revenue_data, use the narrowest date range that answers the question.
When you trigger a revenue recognition run, always confirm it will appear in the Approval Inbox.

## Revenue Record Fields

- project_name: project name
- date_recognized: ISO recognition date
- billing_type: Fixed Fee | T&M | MSF | Hosting | Retainer
- total_recognized_revenue: dollars recognized
- logged_hours: hours logged to recognition date
- scheduled_hours: forecast hours remaining
- blended_rate: revenue / logged_hours (null if no hours logged)
- percentage_complete: 0–1 (Fixed Fee only)
- contracted_fees: total contract value (Fixed Fee only)
- invoiced_to_date: amount invoiced
- notes: flags or special notes

Answer accurately based only on data returned by get_revenue_data."""

