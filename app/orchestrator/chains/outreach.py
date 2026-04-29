"""Outreach chain — pattern #2 (prompt_chain_action).

The first real prompt chain. Pulls HubSpot context, drafts an email, gates on a
human checkpoint, and sends via Gmail. Phase D ships the happy path only —
no critique loops yet (Phase E adds those).

Step plan:

  1. tool_call — HubSpot contact + company
  2. tool_call — Web search (stub) for company signals
  3. llm_step  — Consolidate context into a brief
  4. tool_call — Knowledge base retrieval (Frogslayer GTM blurb)
  5. llm_step  — Draft email
  6–9 (Phase E) — Voice + accuracy critique loops
  6. execution — Human approval + Gmail send (stub)

The original directive had a separate `checkpoint` step before `execution`,
but ExecutionStep already pauses for approval before the side effect — having
both creates a redundant inbox item. The single ExecutionStep is the approval
gate AND the send.

External integrations are stubbed when their credentials are not configured;
real calls are wired only where the stub would be misleading. LLM calls go
through `app.integrations.anthropic_client.get_client()` — tests patch it.
"""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.integrations import anthropic_client
from app.models.workflows import WorkflowPattern
from app.orchestrator.chain import Chain, register_chain
from app.orchestrator.state import StepContext
from app.orchestrator.steps import (
    ExecutionStep,
    LLMStep,
    ToolCallStep,
)

logger = logging.getLogger(__name__)

OUTREACH_KIND = "outreach_chain"
OUTREACH_AGENT_SLUG = "outreach-agent"

# Default model for LLM steps. Read from agent config first, then fall back
# to a sensible default. Hardcoded here (rather than per-step) so a single
# place controls model choice for the chain.
DEFAULT_MODEL = "claude-sonnet-4-6"


# -----------------------------------------------------------------------------
# Step handlers
# -----------------------------------------------------------------------------

async def _pull_hubspot_contact(ctx: StepContext) -> dict[str, Any]:
    """Fetch HubSpot contact + company. Returns realistic placeholder data
    when HubSpot is not configured so the chain still runs end-to-end."""
    workflow = ctx.state
    contact_id = (
        workflow.actions[0].proposed_payload.get("hubspot_contact_id")
        if workflow.actions
        else None
    )
    # The contact id is also passed as workflow trigger context; pick it up
    # from the workflow row's trigger_payload via state.
    if not contact_id:
        contact_id = await _trigger_payload_get(ctx, "hubspot_contact_id")

    if not settings.hubspot_token or not contact_id:
        # Stub path — used in tests and when creds are not wired locally.
        return {
            "hubspot_contact_id": contact_id or "stub-contact-001",
            "contact": {
                "first_name": "Sarah",
                "last_name": "Chen",
                "email": "schen@acmecorp.example",
                "title": "VP Engineering",
            },
            "company": {
                "name": "Acme Corp",
                "domain": "acmecorp.example",
                "industry": "B2B SaaS",
                "size": "200-500",
            },
            "stub": True,
        }

    # Real path — would call hubspot.get_contact_with_company(contact_id).
    # Implemented when HubSpot integration is wired.
    raise NotImplementedError(
        "HubSpot fetch not yet implemented. Set HUBSPOT_TOKEN='' to use stub data."
    )


async def _web_search_company(ctx: StepContext) -> dict[str, Any]:
    """Stubbed web search. Returns plausible signals for the consolidated brief."""
    contact = ctx.state.actions[0].result if ctx.state.actions else {}
    company = (contact or {}).get("company", {}) if isinstance(contact, dict) else {}
    company_name = company.get("name", "the target company")
    return {
        "company_name": company_name,
        "signals": [
            f"{company_name} closed a Series B funding round 30 days ago.",
            f"{company_name} CEO posted about scaling engineering org on LinkedIn last week.",
            f"{company_name} job board lists 12 open backend engineering roles.",
        ],
        "stub": True,
    }


async def _consolidate_context(ctx: StepContext) -> dict[str, Any]:
    """LLM step: turn raw HubSpot + web signals into a single context brief."""
    contact = ctx.state.latest_for_step(0)
    web = ctx.state.latest_for_step(1)
    contact_data = contact.result if contact and contact.result else {}
    web_data = web.result if web and web.result else {}

    prompt = (
        "You are an SDR assistant for Frogslayer, a software delivery firm. "
        "Read the contact, company, and web signals below, and produce a 3-4 "
        "sentence brief in plain prose summarising the most relevant facts for a "
        "first outreach email. Avoid fluff.\n\n"
        f"CONTACT/COMPANY:\n{contact_data}\n\nWEB SIGNALS:\n{web_data}\n\nBRIEF:"
    )

    text = await _complete(ctx, prompt, max_tokens=400)
    return {"brief": text.strip()}


async def _retrieve_knowledge_base(ctx: StepContext) -> dict[str, Any]:
    """Knowledge base retrieval. Stubbed for Phase D — returns a Frogslayer GTM
    blurb. Real pgvector retrieval lands when the ingestion pipeline ships
    (see docs/BACKLOG.md)."""
    return {
        "gtm_blurb": (
            "Frogslayer is a software delivery partner that builds and runs custom "
            "platforms for B2B clients in regulated industries. Differentiators: "
            "(1) a tight 'product factory' team model that ships in 2-week increments, "
            "(2) deep experience with operational data systems, (3) production "
            "ownership through a managed services tier, not just delivery."
        ),
        "stub": True,
    }


async def _draft_email(ctx: StepContext) -> dict[str, Any]:
    """LLM step: draft a personalised outreach email."""
    brief = (ctx.state.latest_for_step(2).result or {}).get("brief", "")
    contact_action = ctx.state.latest_for_step(0)
    contact = (contact_action.result or {}).get("contact", {}) if contact_action else {}
    company = (contact_action.result or {}).get("company", {}) if contact_action else {}
    gtm = (ctx.state.latest_for_step(3).result or {}).get("gtm_blurb", "")

    prompt = (
        "You write outbound emails on behalf of Frogslayer. Voice: direct, "
        "specific, no clichés, no 'Hi <name>', no 'I hope this finds you well', "
        "no 'Congrats on the round'. Reference one concrete signal from the brief, "
        "tie it to a Frogslayer capability from the GTM blurb, and end with a "
        "single, low-friction ask (15-min call this Thursday or Friday).\n\n"
        f"RECIPIENT:\n{contact}\n\nCOMPANY:\n{company}\n\n"
        f"BRIEF:\n{brief}\n\nFROGSLAYER GTM BLURB:\n{gtm}\n\n"
        "Output JSON: {\"subject\": \"...\", \"body\": \"...\"}"
    )

    text = await _complete(ctx, prompt, max_tokens=600)
    subject, body = _parse_email(text)
    return {
        "to": contact.get("email") or "unknown@example",
        "to_name": " ".join(filter(None, [contact.get("first_name"), contact.get("last_name")])),
        "subject": subject,
        "body": body,
    }


async def _propose_send(ctx: StepContext) -> dict[str, Any]:
    """Surface the draft for human review on the execution step. The reviewer
    can edit fields (subject, body, to) before approving — the orchestrator
    passes the executed_payload through to the executor."""
    draft = ctx.state.latest_for_step(4)
    return draft.result if draft and draft.result else {}


async def _gmail_send_stub(ctx: StepContext) -> dict[str, Any]:
    """Phase D: log + return stub success. Phase F switches to a real Gmail call
    once end-to-end works.
    """
    payload = ctx.executed_payload or {}
    logger.info(
        "[gmail-stub] would send subject=%r to=%r",
        payload.get("subject"),
        payload.get("to"),
    )
    return {
        "stub": True,
        "would_send_to": payload.get("to"),
        "subject": payload.get("subject"),
    }


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

async def _complete(ctx: StepContext, prompt: str, *, max_tokens: int) -> str:
    """Single Anthropic completion. Patched in tests.

    When no API key is configured (dev environments without creds), returns a
    deterministic stub keyed on the prompt's section markers so the chain can
    still run end-to-end. Production / staging hits the real API.
    """
    if not settings.anthropic_api_key:
        logger.warning(
            "[outreach-chain] ANTHROPIC_API_KEY is empty — returning stub LLM output."
        )
        return _stub_llm_response(prompt)

    client = anthropic_client.get_client()
    msg = await client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    # The SDK returns a Message with content blocks; pull text out.
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "".join(parts)


def _stub_llm_response(prompt: str) -> str:
    """Dev-mode fallback when ANTHROPIC_API_KEY is unset. Returns a plausible
    stub keyed on the unique marker each chain prompt contains."""
    import json as _json

    if "Output JSON:" in prompt:
        return _json.dumps({
            "subject": "Quick thought after your Series B",
            "body": (
                "Saw the Series B announcement and the engineering hiring spike. "
                "Frogslayer's product factory model has helped a few B2B SaaS teams "
                "ship customer-facing platforms without growing the in-house team. "
                "Open to a 15-min call Thursday or Friday?"
            ),
        })
    if "3-4 sentence brief" in prompt:
        return (
            "Acme Corp closed a Series B 30 days ago and is scaling its backend "
            "engineering org — 12 open roles per the public job board. The CEO "
            "is publicly framing this as a platform-investment moment. Sarah Chen "
            "(VP Eng) is the right person to talk to about delivery capacity."
        )
    return "<stub-llm-output>"


def _parse_email(text: str) -> tuple[str, str]:
    """Best-effort parse of the model's JSON response. Falls back to splitting
    on the first blank line if the model returns plain text."""
    import json
    import re

    # Strip code fences if present.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        obj = json.loads(cleaned)
        return str(obj.get("subject", "")).strip(), str(obj.get("body", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        # Fallback: first line as subject, rest as body.
        lines = cleaned.splitlines()
        if not lines:
            return "", ""
        return lines[0].strip(), "\n".join(lines[1:]).strip()


async def _trigger_payload_get(ctx: StepContext, key: str) -> Any:
    """Read a key from the workflow's trigger_payload."""
    row = await ctx.conn.fetchrow(
        "SELECT trigger_payload FROM workflows WHERE id = $1",
        ctx.workflow_id,
    )
    payload = (row["trigger_payload"] if row else None) or {}
    return payload.get(key)


# -----------------------------------------------------------------------------
# Chain registration
# -----------------------------------------------------------------------------

OUTREACH_CHAIN = Chain(
    kind=OUTREACH_KIND,
    pattern=WorkflowPattern.prompt_chain_action,
    agent_slug=OUTREACH_AGENT_SLUG,
    steps=(
        ToolCallStep("Pull HubSpot contact + company", _pull_hubspot_contact),
        ToolCallStep("Web search company signals", _web_search_company),
        LLMStep("Consolidate context brief", _consolidate_context),
        ToolCallStep("Retrieve Frogslayer GTM context", _retrieve_knowledge_base),
        LLMStep("Draft outreach email", _draft_email),
        ExecutionStep(
            "Approve and send outreach email",
            _gmail_send_stub,
            propose_handler=_propose_send,
            action_type="send_email",
            risk_level="medium",
        ),
    ),
)


def register() -> None:
    """Idempotent: only registers if not already present.

    Tests reset the registry, so calling this from app startup must not crash
    if the chain is already there from a prior import.
    """
    from app.orchestrator.chain import has_chain

    if has_chain(OUTREACH_KIND):
        return
    register_chain(OUTREACH_CHAIN)
