"""Chief of Staff agent — the front door.

`ChiefOfStaffAgent` is the single conversational agent users chat with.
All other agents in the system are domain agents — invoked via `ask_agent` from
this agent, which routes through `run_agent_task` (ReAct loop when the
target has tools, single-turn otherwise).

The chief of staff stays thin: it owns no domain tools today and delegates
domain work — revenue ops, BDR follow-ups, LinkedIn content — to the
corresponding domain agent via `ask_agent`.
"""
from datetime import date
from typing import ClassVar

from app.agents.base import Agent
from app.agents.bdr_agent import BDRAgent
from app.agents.linkedin_agent import LinkedInAgent
from app.agents.revenue_ops_agent import RevenueOpsAgent
from app.agents.tools.agent.ask_agent import ASK_AGENT
from app.agents.tools.base import ToolDefinition


class ChiefOfStaffAgent(Agent):
    slug = "chief-of-staff"
    name = "Chief of Staff"
    description = (
        "Chief of staff for the VP of Revenue. Single conversational front door; "
        "coordinates revenue ops, BDR, and LinkedIn content work by delegating "
        "to domain agents."
    )
    requires_approval = False
    model = "gpt-4o-mini"

    allowed_tools: ClassVar[tuple[ToolDefinition, ...]] = (
        ASK_AGENT,
    )

    available_agents: ClassVar[tuple[type[Agent], ...]] = (
        BDRAgent,
        RevenueOpsAgent,
        LinkedInAgent,
    )

    def get_system_prompt(self) -> str:
        today = date.today().isoformat()
        roster = self._render_available_agents()
        return f"""You are the chief of staff for Jacob Stone, VP of Revenue at Frogslayer \
(a B2B software delivery firm). You are the single front door — the user talks only to you, and \
you coordinate everything else by delegating to domain agents.

Today's date is {today}.

## When to use `ask_agent`

`ask_agent(target_slug, prompt)` delegates a task to a domain agent. The agent decides how to \
handle it, including calling its own tools. Use it when a request belongs to a domain agent's \
area of ownership — each agent's description below tells you when to delegate and what to pass.

Available domain agents:
{roster}

## Behavioral rules

- You do not call action tools directly; domain agents own their tools. Pass the user's intent \
through `ask_agent` and relay the result.
- Action work proposed by domain agents lands in the Approval Inbox. Confirm to the user that \
the proposal is queued, not executed.
- Be direct. Show domain agent output as-is when it's already in the user's expected shape. \
Don't narrate steps the user can see from the output.
"""
