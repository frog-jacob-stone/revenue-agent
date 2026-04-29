import logging
from datetime import date
from typing import Any, ClassVar
from uuid import UUID

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

    # ── Workflow execution ───────────────────────────────────────────────────

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        # Revenue recognition runs as an orchestrated chain (see
        # app/orchestrator/chains/rev_rec.py). The legacy `run()` -> propose-an-
        # action flow is no longer used; callers should use trigger() instead.
        raise NotImplementedError(
            "RevenueRecognitionAgent.run() is no longer used. "
            "Use RevenueRecognitionAgent.trigger() — it routes through the "
            "rev_rec_monthly chain in app/orchestrator/chains/rev_rec.py."
        )

    @classmethod
    async def trigger(
        cls,
        context: dict[str, Any],
        initiated_by: str = "system",
    ) -> dict[str, Any]:
        """Kick off the rev_rec_monthly chain through the orchestrator.

        Returns a dict containing `workflow_id` (str) so callers (chat tools,
        cron, the `/agents/{slug}/trigger` endpoint) can correlate the run.
        """
        from app.orchestrator import orchestrator
        from app.orchestrator.chains.rev_rec import REV_REC_KIND

        workflow_id = await orchestrator.create_workflow(
            REV_REC_KIND,
            context=context,
            initiated_by=initiated_by,
            trigger_source="manual",
        )
        await orchestrator.resume(workflow_id)
        return {"workflow_id": str(workflow_id)}
