import json
import logging
from datetime import date
from typing import Any

from app.config import settings
from app.integrations.airtable import get_revenue_records
from app.integrations.openai_client import get_client
from app.services import agent_runner

logger = logging.getLogger(__name__)

# OpenAI tool definitions (swap "parameters" → "input_schema" when reverting to Claude)
_REVENUE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_revenue_data",
            "description": (
                "Fetch revenue recognition records for a date range. "
                "Choose the narrowest range that answers the question — "
                "last quarter for snapshots, last 12 months for trends, omit dates for all-time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {
                        "type": "string",
                        "description": "Start date ISO YYYY-MM-DD (inclusive), optional.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "End date ISO YYYY-MM-DD (inclusive), optional.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_revenue_recognition",
            "description": (
                "Trigger the monthly revenue recognition process. "
                "Use when the user asks to run, kick off, or start revenue recognition. "
                "This creates a proposed action in the Approval Inbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_recognized": {
                        "type": "string",
                        "description": "Recognition date ISO YYYY-MM-DD. Defaults to today.",
                    },
                },
            },
        },
    },
]


def _revenue_system_prompt() -> str:
    today = date.today().isoformat()
    return f"""You are a revenue operations assistant for Frogslayer, a software consulting firm.
You help the revenue team understand revenue trends and manage the recognition process.

Today's date is {today}.

## Tools

**get_revenue_data** — Fetch revenue recognition records for a date range.
Use the narrowest range that answers the question:
- Quick snapshot → last quarter
- Trend analysis → last 12 months
- All-time view → omit dates

**trigger_revenue_recognition** — Kick off the monthly revenue recognition process.
Use when the user asks to run, kick off, or trigger revenue recognition.
This creates a proposed action in the Approval Inbox — always tell the user to check there.

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

Answer accurately based only on data returned by get_revenue_data.
When you trigger a revenue recognition run, always confirm it will appear in the Approval Inbox."""


_REVENUE_FIELDS = {
    "Project Name": "project_name",
    "Date Recognized": "date_recognized",
    "Billing Type": "billing_type",
    "Total Recognized Revenue": "total_recognized_revenue",
    "Logged Hours": "logged_hours",
    "Scheduled Hours": "scheduled_hours",
    "Percentage Complete": "percentage_complete",
    "Contracted Fees": "contracted_fees",
    "Invoiced to Date": "invoiced_to_date",
    "Notes": "notes",
}


async def _execute_revenue_tool(name: str, tool_input: dict[str, Any]) -> Any:
    if name == "get_revenue_data":
        # Default to last 12 months when no range is specified to keep context size manageable
        date_from = tool_input.get("date_from")
        date_to = tool_input.get("date_to")
        if not date_from and not date_to:
            from datetime import timedelta
            date_from = (date.today().replace(day=1) - timedelta(days=365)).strftime("%Y-%m-%d")

        records = await get_revenue_records(settings, date_from=date_from, date_to=date_to)

        # Project to only the fields the LLM needs — drops Airtable metadata and linked records
        slim = []
        for r in records:
            row: dict[str, Any] = {}
            for airtable_key, slim_key in _REVENUE_FIELDS.items():
                row[slim_key] = r.get(airtable_key)
            logged = row.get("logged_hours") or 0
            revenue = row.get("total_recognized_revenue") or 0
            row["blended_rate"] = round(revenue / logged, 2) if logged > 0 else None
            slim.append(row)
        return slim

    if name == "trigger_revenue_recognition":
        date_recognized = tool_input.get("date_recognized") or date.today().isoformat()
        return await agent_runner.run_agent(
            "revenue-recognition",
            initiated_by="chat",
            context={"date_recognized": date_recognized},
        )

    raise ValueError(f"Unknown tool: {name}")


_HANDLERS: dict[str, dict] = {
    "revenue-recognition": {
        "system_prompt": _revenue_system_prompt,
        "tools": _REVENUE_TOOLS,
        "tool_executor": _execute_revenue_tool,
    },
}


async def agent_chat(agent_slug: str, messages: list[dict]) -> dict[str, Any]:
    handler = _HANDLERS.get(agent_slug)
    if not handler:
        raise ValueError(f"Chat not supported for agent '{agent_slug}'")

    client = get_client()
    system_prompt = handler["system_prompt"]()
    tools = handler["tools"]
    tool_executor = handler["tool_executor"]

    # OpenAI takes system as first message in the list
    msg_list: list[dict] = [{"role": "system", "content": system_prompt}] + list(messages)
    last_tool_used: str | None = None

    while True:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=msg_list,
            tools=tools,
        )

        choice = response.choices[0]

        if choice.finish_reason == "tool_calls":
            tool_calls = choice.message.tool_calls
            # Append assistant turn (with tool_calls)
            msg_list.append(choice.message)
            for tc in tool_calls:
                last_tool_used = tc.function.name
                try:
                    result = await tool_executor(tc.function.name, json.loads(tc.function.arguments))
                except Exception as exc:
                    logger.exception("Tool %s failed", tc.function.name)
                    result = {"error": str(exc)}
                msg_list.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
        else:
            return {"answer": choice.message.content or "", "tool_used": last_tool_used}
