import json
import logging
import re
from typing import Any
from uuid import UUID

from app.agents.base import BaseAgent
from app.integrations.anthropic_client import get_client

logger = logging.getLogger(__name__)

FAKE_DEALS = [
    {
        "id": "D-2198",
        "company": "CloudNine Logistics",
        "contract_value": 85500,
        "contract_start": "2026-03-01",
        "contract_months": 12,
        "status": "active",
    },
    {
        "id": "D-2241",
        "company": "Vertex IO",
        "contract_value": 180000,
        "contract_start": "2026-04-01",
        "contract_months": 6,
        "status": "active",
    },
    {
        "id": "D-2190",
        "company": "Meridian Group",
        "contract_value": 72000,
        "contract_start": "2026-02-01",
        "contract_months": 12,
        "status": "active",
    },
]


class RevenueRecognitionAgent(BaseAgent):
    slug = "revenue-recognition"
    name = "Revenue Recognition"

    async def run(self, workflow_id: UUID, context: dict[str, Any]) -> list[dict[str, Any]]:
        period = context.get("period", "2026-04")
        client = get_client()

        prompt = f"""You are a revenue recognition accountant for Frogslayer, a software consultancy.

Review these active contracts and calculate revenue to recognize for period {period}:

{json.dumps(FAKE_DEALS, indent=2)}

For each contract:
- monthly_amount = contract_value / contract_months (round to 2 decimal places)
- deferred_balance = (remaining months after {period}) * monthly_amount

Return ONLY valid JSON with this exact structure, no prose:
{{
  "period": "{period}",
  "entries": [
    {{
      "deal_id": "string",
      "company": "string",
      "monthly_amount": 0.00,
      "deferred_balance": 0.00,
      "note": "string"
    }}
  ],
  "total_recognized": 0.00
}}"""

        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            result = (
                json.loads(match.group())
                if match
                else {"period": period, "entries": [], "total_recognized": 0.0}
            )

        total = result.get("total_recognized", 0)
        entry_count = len(result.get("entries", []))
        logger.info("revenue-recognition produced %d entries, total $%.2f", entry_count, total)

        return [
            {
                "action_type": "write_rev_rec",
                "summary": (
                    f"Write revenue recognition journal entries for {period} — "
                    f"${total:,.2f} recognized across {entry_count} contracts"
                ),
                "proposed_payload": result,
                "reasoning": (
                    "Monthly revenue recognition run. Each active contract is amortized "
                    "over its term. Entries are proposed for human review before posting."
                ),
                "risk_level": "low",
            }
        ]
