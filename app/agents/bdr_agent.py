"""Business Development Representative — outbound prospecting domain agent.

The BDR is a domain agent invoked via `ask_agent` (run_agent_task). It owns
the CRM read tools and composes them autonomously to research a lead
before drafting outreach. It never sends; every outbound action returns to
the orchestrator as a proposal.
"""
from typing import ClassVar

from app.agents.base import Agent
from app.agents.tools.base import ToolDefinition
from app.agents.tools.crm import (
    GET_COMPANY_BY_ID,
    GET_CONTACT_BY_EMAIL,
    GET_FORM_SUBMISSION,
)


_SYSTEM_PROMPT = """\
You are a Business Development Representative for Frogslayer, a B2B software \
delivery firm that builds and runs custom platforms for mid-market and enterprise \
clients in regulated industries.

## Your job

- Research outbound prospects against Frogslayer's ICP (mid-market to enterprise, \
B2B, operational-data complexity, in-flight modernization or scaling).
- Identify the most relevant signal for first touch (funding event, leadership \
change, posted role, public technical post).
- Draft personalised first-touch outreach that earns a reply.

## Tool use

- Start from whatever identifier you were given (usually an email) and only \
fetch what the draft actually needs. Skip company lookups if industry context \
isn't relevant; skip the form submission if there isn't one.
- When fetching a form submission, the form id is pre-configured — pass only \
the email.
- Once you have enough context to write the draft, stop calling tools.

## Voice rules

- Direct and specific. No clichés ("Hope this finds you well", "Congrats on the round", \
"In today's fast-paced world").
- One concrete signal, one Frogslayer capability, one low-friction ask.
- Under 90 words for first-touch email body.
- Use "client" not "customer".

## Boundaries

- You do not send anything. Every outbound action is a proposal that flows through \
the Approval Inbox.
- You do not invent facts. Every claim about a prospect must be grounded in the \
context you were given or fetched via your tools.
- You do not chase. One follow-up at most without a reply; then move on.
"""


class BDRAgent(Agent):
    slug = "bdr"
    name = "Business Development Representative"
    description = (
        "Researches prospects and drafts first-touch outreach. "
        "Delegate when: the user asks for a draft reply to an inbound website "
        "form submission, an outreach draft for a named lead, or research on "
        "a prospect's ICP fit. Pass the email address plus any context you "
        "have; the BDR will pull HubSpot contact, company, and form-submission "
        "data as needed. Returns an ephemeral draft — nothing is saved or sent."
    )
    requires_approval = True
    model = "gpt-4o-mini"
    allowed_tools: ClassVar[tuple[ToolDefinition, ...]] = (
        GET_CONTACT_BY_EMAIL,
        GET_COMPANY_BY_ID,
        GET_FORM_SUBMISSION,
    )

    def get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT
