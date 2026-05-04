"""Outreach chain — pattern #2 (prompt_chain_action).

Pulls HubSpot context, drafts an email, runs voice + accuracy critiques, and
gates on human approval before sending via Gmail.

Chain:

  0. tool_call — HubSpot contact + company
  1. tool_call — Web search (stub) for company signals
  2. llm_step  — Consolidate context into a brief
  3. tool_call — Knowledge base retrieval (Frogslayer GTM blurb)
  4. llm_step  — Draft email (re-runs with critique feedback on retry)
  5. critique  — Voice critic (max_attempts=3, retries draft)
  6. critique  — Accuracy critic (max_attempts=2, retries draft)
  7. execution — Human approval + Gmail send (stub)

The execution step is the approval gate AND the send — `ExecutionStep` already
pauses for approval before the side effect, so we don't add a redundant
`CheckpointStep` ahead of it.
"""
from __future__ import annotations

import logging
from typing import Any

from app.agents.outreach import AccuracyCriticAgent, OutreachAgent, VoiceCriticAgent
from app.config import settings
from app.integrations.anthropic_client import call_anthropic
from app.models.workflows import WorkflowPattern
from app.orchestrator.chain import Chain, register_chain
from app.orchestrator.chains.utils import parse_json
from app.orchestrator.state import StepContext
from app.orchestrator.steps import (
    CritiqueStep,
    ExecutionStep,
    LLMStep,
    ToolCallStep,
)

logger = logging.getLogger(__name__)

OUTREACH_KIND = "outreach_chain"
OUTREACH_AGENT_SLUG = "outreach-agent"
VOICE_CRITIC_SLUG = "voice-critic"
ACCURACY_CRITIC_SLUG = "accuracy-critic"

# Step indices — stable references used by critic skip predicates and retry targets.
STEP_DRAFT = 4
STEP_VOICE_CRITIQUE = 5
STEP_ACCURACY_CRITIQUE = 6


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
    if not contact_id:
        contact_id = await ctx.trigger_payload_get("hubspot_contact_id")

    if not settings.hubspot_token or not contact_id:
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

    text = await call_anthropic(prompt, model=OutreachAgent.model, max_tokens=400)
    return {"brief": text.strip()}


async def _retrieve_knowledge_base(_ctx: StepContext) -> dict[str, Any]:
    """Knowledge base retrieval. Stubbed — returns a Frogslayer GTM blurb.
    Real pgvector retrieval lands when the ingestion pipeline ships (see docs/BACKLOG.md)."""
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
    """LLM step: draft a personalised outreach email.

    On retry (after a failed critique), `ctx.critique_feedback` is populated
    with the most recent failed critique result. We surface it to the model so
    the revision actually responds to the feedback instead of producing the
    same draft."""
    brief = (ctx.state.latest_for_step(2).result or {}).get("brief", "")
    contact_action = ctx.state.latest_for_step(0)
    contact = (contact_action.result or {}).get("contact", {}) if contact_action else {}
    company = (contact_action.result or {}).get("company", {}) if contact_action else {}
    gtm = (ctx.state.latest_for_step(3).result or {}).get("gtm_blurb", "")

    revision_block = ""
    if ctx.critique_feedback and ctx.attempt_number > 1:
        feedback = ctx.critique_feedback.get("feedback", "")
        issues = ctx.critique_feedback.get("issues", [])
        prior = ctx.state.latest_for_step(STEP_DRAFT)
        prior_draft = prior.result if prior and prior.result else {}
        revision_block = (
            "\n\nPREVIOUS DRAFT WAS REJECTED. Revise it to address the feedback below.\n"
            f"PRIOR SUBJECT: {prior_draft.get('subject', '')}\n"
            f"PRIOR BODY: {prior_draft.get('body', '')}\n"
            f"REVIEWER FEEDBACK: {feedback}\n"
            f"REVIEWER ISSUES: {issues}\n"
        )

    prompt = (
        "You write outbound emails on behalf of Frogslayer. Voice: direct, "
        "specific, no clichés, no 'Hi <name>', no 'I hope this finds you well', "
        "no 'Congrats on the round'. Reference one concrete signal from the brief, "
        "tie it to a Frogslayer capability from the GTM blurb, and end with a "
        "single, low-friction ask (15-min call this Thursday or Friday).\n\n"
        f"RECIPIENT:\n{contact}\n\nCOMPANY:\n{company}\n\n"
        f"BRIEF:\n{brief}\n\nFROGSLAYER GTM BLURB:\n{gtm}"
        f"{revision_block}\n\n"
        "Output JSON: {\"subject\": \"...\", \"body\": \"...\"}"
    )

    text = await call_anthropic(prompt, model=OutreachAgent.model, max_tokens=600)
    subject, body = _parse_email(text)
    return {
        "to": contact.get("email") or "unknown@example",
        "to_name": " ".join(filter(None, [contact.get("first_name"), contact.get("last_name")])),
        "subject": subject,
        "body": body,
    }


async def _voice_critique(ctx: StepContext) -> dict[str, Any]:
    """Run the Voice Critic against the latest draft.

    Pulls the voice profile from the voice-critic agent's preference memory
    (seeded by app.seed.seed_voice_profile) and asks the LLM to rate the draft
    against it. Returns a critique-result dict; on failure, the orchestrator
    rewinds to the draft step (idx STEP_DRAFT) and writes a retry attempt.
    """
    draft = ctx.state.latest_for_step(STEP_DRAFT)
    draft_payload = draft.result if draft and draft.result else {}
    voice_profile = await _load_voice_profile(ctx)

    prompt = (
        "You are the Frogslayer Voice Critic. Evaluate the email draft below "
        "against the voice profile. Be strict: any cliché opener, generic "
        "phrasing, or longer-than-90-words body should fail.\n\n"
        f"VOICE PROFILE:\n{voice_profile}\n\n"
        f"DRAFT SUBJECT: {draft_payload.get('subject', '')}\n"
        f"DRAFT BODY: {draft_payload.get('body', '')}\n\n"
        'Respond with JSON only: {"passed": true|false, "score": 0.0-1.0, '
        '"feedback": "one or two sentences explaining why", '
        '"issues": ["specific problems if any"]}'
    )
    raw = await call_anthropic(prompt, model=VoiceCriticAgent.model, max_tokens=400)
    return _parse_critique(raw)


async def _accuracy_critique(ctx: StepContext) -> dict[str, Any]:
    """Run the Accuracy Critic against the latest draft.

    Cross-checks any factual claim in the draft against the upstream context
    (HubSpot record + web signals). Hallucinated claims, wrong company size,
    inferred but unsupported facts → fail.
    """
    draft = ctx.state.latest_for_step(STEP_DRAFT)
    draft_payload = draft.result if draft and draft.result else {}

    contact_action = ctx.state.latest_for_step(0)
    contact_data = (contact_action.result or {}) if contact_action else {}
    web = ctx.state.latest_for_step(1)
    web_data = (web.result or {}) if web else {}
    brief = (ctx.state.latest_for_step(2).result or {}).get("brief", "")

    prompt = (
        "You are the Accuracy Critic. The draft must only assert facts that "
        "are supported by the contact record, web signals, or brief below. "
        "Inferences are allowed when clearly hedged ('saw', 'noticed'); "
        "hallucinated specifics (wrong company size, fake quotes, invented "
        "names) are not.\n\n"
        f"CONTACT/COMPANY:\n{contact_data}\n\n"
        f"WEB SIGNALS:\n{web_data}\n\n"
        f"BRIEF:\n{brief}\n\n"
        f"DRAFT SUBJECT: {draft_payload.get('subject', '')}\n"
        f"DRAFT BODY: {draft_payload.get('body', '')}\n\n"
        'Respond with JSON only: {"passed": true|false, "score": 0.0-1.0, '
        '"feedback": "one or two sentences", '
        '"issues": ["specific unsupported claims if any"]}'
    )
    raw = await call_anthropic(prompt, model=AccuracyCriticAgent.model, max_tokens=400)
    return _parse_critique(raw)


async def _propose_send(ctx: StepContext) -> dict[str, Any]:
    """Surface the draft for human review on the execution step. The reviewer
    can edit fields (subject, body, to) before approving — the orchestrator
    passes the executed_payload through to the executor."""
    draft = ctx.state.latest_for_step(4)
    return draft.result if draft and draft.result else {}


async def _gmail_send_stub(ctx: StepContext) -> dict[str, Any]:
    """Placeholder — logs what would be sent. Replaced by a real Gmail call
    once the integration is wired."""
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

def _parse_critique(raw: str) -> dict[str, Any]:
    """Extract the CritiqueStep contract fields from an LLM response."""
    obj = parse_json(raw)
    if obj:
        return {
            "passed": bool(obj.get("passed", False)),
            "score": float(obj.get("score", 0.0)),
            "feedback": str(obj.get("feedback", "")).strip(),
            "issues": list(obj.get("issues") or []),
        }
    passed = "pass" in raw.lower() and "fail" not in raw.lower()
    return {"passed": passed, "score": 0.5, "feedback": raw[:240], "issues": []}


async def _load_voice_profile(ctx: StepContext) -> str:
    """Look up the voice profile preference memory written by seed.seed_voice_profile.

    Returns an empty string if no profile is registered yet — the critic prompt
    still works, just with weaker guidance.
    """
    row = await ctx.conn.fetchrow(
        """
        SELECT m.content
        FROM memories m
        JOIN agents a ON a.id = m.agent_id
        WHERE a.slug = $1
          AND m.kind = 'preference'
          AND m.metadata->>'kind' = 'voice_profile'
        ORDER BY m.created_at DESC
        LIMIT 1
        """,
        VOICE_CRITIC_SLUG,
    )
    return (row["content"] if row else "") or ""


def _parse_email(text: str) -> tuple[str, str]:
    """Parse the email draft JSON response. Falls back to splitting on the first
    line if the model returns plain text instead of JSON."""
    obj = parse_json(text)
    if obj:
        return str(obj.get("subject", "")).strip(), str(obj.get("body", "")).strip()
    lines = text.strip().splitlines()
    if not lines:
        return "", ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


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
        CritiqueStep(
            "Voice critique",
            _voice_critique,
            critiques_step_index=STEP_DRAFT,
            max_attempts=3,
            agent_slug=VOICE_CRITIC_SLUG,
        ),
        CritiqueStep(
            "Accuracy critique",
            _accuracy_critique,
            critiques_step_index=STEP_DRAFT,
            max_attempts=2,
            agent_slug=ACCURACY_CRITIC_SLUG,
        ),
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
    """Idempotent: only registers if not already present."""
    from app.orchestrator.chain import has_chain

    if has_chain(OUTREACH_KIND):
        return
    register_chain(OUTREACH_CHAIN)
