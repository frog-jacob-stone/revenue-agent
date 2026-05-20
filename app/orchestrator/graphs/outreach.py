"""outreach_chain — personalised outbound email with two critique loops.

Eight host-defined nodes plus the critic loop attached by
`add_critique_loop` (which adds `voice_critique`, `accuracy_critique`, and
the shared `failed_terminal` node):

    [entry] → pull_hubspot → web_search → consolidate → retrieve_kb → compose_email
                                                                          │
                                       ┌──────────────────────────────────┘
                                       ▼
                          add_critique_loop(...)
                          (voice 3× → accuracy 2× → fail loops back to compose_email)
                                       │ pass
                                       ▼
                                propose_send
                                       │
                                [interrupt_before
                                  gmail_send]
                                       │
                                       ▼
                                  gmail_send → END

Cross-critic counter semantics (preserved by `add_critique_loop`):
`voice_attempts` and `accuracy_attempts` are independent counters that
accumulate monotonically across the workflow — when accuracy fails and
the loop returns to compose_email, voice runs again and its counter ticks
upward.

The shared slot `last_critique_feedback` is set by whichever critic last
failed; the `compose_email` node consumes it and clears it (sets to None
in its returned state). On budget exhaustion, the helper sets
`failure_reason = "{critic} budget exhausted"` and routes to
`failed_terminal`.
"""
from __future__ import annotations

import logging
from typing import Any, NotRequired
from uuid import UUID

from langgraph.graph import END, StateGraph

from app.agents.bdr_agent import BDRAgent
from app.config import settings
from app.db import get_pool
from app.integrations.llm import Attribution, dispatch
from app.lib.json_utils import parse_json
from app.orchestrator.critique_loop import Critic, add_critique_loop
from app.orchestrator.runner import GraphSpec
from app.orchestrator.state import BaseGraphState

logger = logging.getLogger(__name__)


OUTREACH_KIND = "outreach_chain"

# This graph is BDR's work. Every LLM call inside attributes to BDRAgent.slug.
# The sub-step (compose vs voice-critique vs accuracy-critique) is captured by
# the `purpose` field on each dispatch, not by separate per-node slugs.
OWNING_AGENT = BDRAgent

ACTION_TYPE_SEND = "send_email"

DEFAULT_VOICE_MAX_ATTEMPTS = 3
DEFAULT_ACCURACY_MAX_ATTEMPTS = 2


# ── State ────────────────────────────────────────────────────────────────────


class OutreachState(BaseGraphState, total=False):
    # From trigger
    hubspot_contact_id: NotRequired[str]
    notes: NotRequired[dict[str, Any]]

    # Built by upstream nodes
    contact: NotRequired[dict[str, Any]]
    company: NotRequired[dict[str, Any]]
    web_signals: NotRequired[dict[str, Any]]
    brief: NotRequired[str]
    gtm_blurb: NotRequired[str]

    # Current draft (overwritten each draft attempt)
    draft_email: NotRequired[dict[str, Any]]   # {to, to_name, subject, body}

    # Critique state — written by the critique_loop helper.
    voice_attempts: NotRequired[int]
    voice_max_attempts: NotRequired[int]
    last_voice_critique: NotRequired[dict[str, Any]]

    accuracy_attempts: NotRequired[int]
    accuracy_max_attempts: NotRequired[int]
    last_accuracy_critique: NotRequired[dict[str, Any]]

    # Shared slot — set by whichever critic last failed; cleared by compose_email.
    last_critique_feedback: NotRequired[dict[str, Any] | None]

    # Set by the critique_loop helper on the exhausting attempt.
    failure_reason: NotRequired[str]

    # Approval bridge
    executed_payload: NotRequired[dict[str, Any]]

    # Terminal result (written by failed_terminal or gmail_send)
    result: NotRequired[dict[str, Any]]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _wf_uuid(state: OutreachState) -> UUID | None:
    wf_id = state.get("workflow_id")
    return UUID(wf_id) if wf_id else None  # type: ignore[arg-type]


def _attribution(state: OutreachState, purpose: str) -> Attribution:
    """Build an Attribution for a dispatch from this graph.

    `agent_slug` comes from the workflow's owning agent (seeded by the runner
    from the GraphSpec default or an invoker override); `purpose` discriminates
    the sub-step.
    """
    return Attribution(
        agent_slug=state.get("_owning_agent_slug"),
        purpose=purpose,
        workflow_id=_wf_uuid(state),
    )


async def _load_voice_profile() -> str:
    """Read the most recent voice profile preference memory.

    The lookup keys on the legacy `"voice-critic"` slug — a memory-table key,
    not an LLM-attribution slug. There is no `voice-critic` agent class; if a
    memory row exists under that slug it was inserted by a separate
    voice-profile seeding flow. Lookup is best-effort; missing → empty string.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
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
        "voice-critic",
    )
    return (row["content"] if row else "") or ""


def _parse_email(text: str) -> tuple[str, str]:
    """Parse the email-draft JSON response. Falls back to splitting on the first line."""
    obj = parse_json(text)
    if obj:
        return str(obj.get("subject", "")).strip(), str(obj.get("body", "")).strip()
    lines = text.strip().splitlines()
    if not lines:
        return "", ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()


def _parse_critique(raw: str) -> dict[str, Any]:
    """Extract the critique contract fields from an LLM response."""
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


# ── Nodes ────────────────────────────────────────────────────────────────────


async def pull_hubspot(state: OutreachState) -> OutreachState:
    """Fetch HubSpot contact + company. Stub when no HUBSPOT_TOKEN is set."""
    contact_id = state.get("hubspot_contact_id")

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
        }

    raise NotImplementedError(
        "HubSpot fetch not yet implemented. Set HUBSPOT_TOKEN='' to use stub data."
    )


async def web_search(state: OutreachState) -> OutreachState:
    """Stubbed web search. Returns plausible signals."""
    company = state.get("company") or {}
    company_name = company.get("name", "the target company")
    return {
        "web_signals": {
            "company_name": company_name,
            "signals": [
                f"{company_name} closed a Series B funding round 30 days ago.",
                f"{company_name} CEO posted about scaling engineering org on LinkedIn last week.",
                f"{company_name} job board lists 12 open backend engineering roles.",
            ],
            "stub": True,
        },
    }


async def consolidate(state: OutreachState) -> OutreachState:
    """Turn raw HubSpot + web signals into a single context brief."""
    contact = state.get("contact") or {}
    company = state.get("company") or {}
    web = state.get("web_signals") or {}

    prompt = (
        "You are an SDR assistant for Frogslayer, a software delivery firm. "
        "Read the contact, company, and web signals below, and produce a 3-4 "
        "sentence brief in plain prose summarising the most relevant facts for a "
        "first outreach email. Avoid fluff.\n\n"
        f"CONTACT:\n{contact}\n\nCOMPANY:\n{company}\n\nWEB SIGNALS:\n{web}\n\nBRIEF:"
    )
    response = await dispatch(
        model=BDRAgent.model,
        messages=[{"role": "user", "content": prompt}],
        attribution=_attribution(state, "outreach.consolidate"),
        max_tokens=400,
    )
    return {"brief": response.text.strip()}


async def retrieve_kb(state: OutreachState) -> OutreachState:
    """Knowledge base retrieval. Stubbed — returns the Frogslayer GTM blurb."""
    return {
        "gtm_blurb": (
            "Frogslayer is a software delivery partner that builds and runs custom "
            "platforms for B2B clients in regulated industries. Differentiators: "
            "(1) a tight 'product factory' team model that ships in 2-week increments, "
            "(2) deep experience with operational data systems, (3) production "
            "ownership through a managed services tier, not just delivery."
        ),
    }


async def compose_email(state: OutreachState) -> OutreachState:
    """Draft a personalised outreach email. On retry, surface the most recent
    critique feedback so the model addresses it. Clears `last_critique_feedback`
    after consumption — the next critic invocation will reset it on fail."""
    brief = state.get("brief") or ""
    contact = state.get("contact") or {}
    company = state.get("company") or {}
    gtm = state.get("gtm_blurb") or ""

    revision_block = ""
    last_feedback = state.get("last_critique_feedback") or {}
    prior_draft = state.get("draft_email") or {}
    if last_feedback:
        feedback = last_feedback.get("feedback", "")
        issues = last_feedback.get("issues", [])
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

    response = await dispatch(
        model=BDRAgent.model,
        messages=[{"role": "user", "content": prompt}],
        attribution=_attribution(state, "outreach.compose_email"),
        max_tokens=600,
    )
    subject, body = _parse_email(response.text)

    return {
        "draft_email": {
            "to": contact.get("email") or "unknown@example",
            "to_name": " ".join(
                filter(None, [contact.get("first_name"), contact.get("last_name")])
            ),
            "subject": subject,
            "body": body,
        },
        "last_critique_feedback": None,  # consumed
    }


# ── Critic bodies (host-owned; helper wraps them with counter + slot logic) ──


async def run_voice_critic(state: OutreachState) -> dict[str, Any]:
    """LLM call: evaluate the latest draft against the voice profile."""
    draft_payload = state.get("draft_email") or {}
    voice_profile = await _load_voice_profile()

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
    response = await dispatch(
        model=BDRAgent.model,
        messages=[{"role": "user", "content": prompt}],
        attribution=_attribution(state, "outreach.voice_critique"),
        max_tokens=400,
    )
    return _parse_critique(response.text)


async def run_accuracy_critic(state: OutreachState) -> dict[str, Any]:
    """LLM call: evaluate the latest draft against the source facts."""
    draft_payload = state.get("draft_email") or {}
    contact = state.get("contact") or {}
    company = state.get("company") or {}
    web = state.get("web_signals") or {}
    brief = state.get("brief") or ""

    prompt = (
        "You are the Accuracy Critic. The draft must only assert facts that "
        "are supported by the contact record, web signals, or brief below. "
        "Inferences are allowed when clearly hedged ('saw', 'noticed'); "
        "hallucinated specifics (wrong company size, fake quotes, invented "
        "names) are not.\n\n"
        f"CONTACT:\n{contact}\n\nCOMPANY:\n{company}\n\n"
        f"WEB SIGNALS:\n{web}\n\nBRIEF:\n{brief}\n\n"
        f"DRAFT SUBJECT: {draft_payload.get('subject', '')}\n"
        f"DRAFT BODY: {draft_payload.get('body', '')}\n\n"
        'Respond with JSON only: {"passed": true|false, "score": 0.0-1.0, '
        '"feedback": "one or two sentences", '
        '"issues": ["specific unsupported claims if any"]}'
    )
    response = await dispatch(
        model=BDRAgent.model,
        messages=[{"role": "user", "content": prompt}],
        attribution=_attribution(state, "outreach.accuracy_critique"),
        max_tokens=400,
    )
    return _parse_critique(response.text)


async def propose_send(state: OutreachState) -> OutreachState:
    """Surface the draft for human review on the approval gate."""
    draft_payload = state.get("draft_email") or {}
    return {
        "_propose": {
            "action_type": ACTION_TYPE_SEND,
            "agent_slug": state.get("_owning_agent_slug") or OWNING_AGENT.slug,
            "risk_level": "medium",
            "summary": draft_payload.get("subject") or "Outreach email",
            "proposed_payload": draft_payload,
        }
    }


async def gmail_send(state: OutreachState) -> OutreachState:
    """Stub send. Reads the (possibly edited) `executed_payload`."""
    payload = state.get("executed_payload") or state.get("draft_email") or {}
    logger.info(
        "[gmail-stub] would send subject=%r to=%r",
        payload.get("subject"),
        payload.get("to"),
    )
    return {
        "result": {
            "stub": True,
            "would_send_to": payload.get("to"),
            "subject": payload.get("subject"),
        },
    }


# ── Graph factory ────────────────────────────────────────────────────────────


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(OutreachState)

    g.add_node("pull_hubspot", pull_hubspot)
    g.add_node("web_search", web_search)
    g.add_node("consolidate", consolidate)
    g.add_node("retrieve_kb", retrieve_kb)
    g.add_node("compose_email", compose_email)
    g.add_node("propose_send", propose_send)
    g.add_node("gmail_send", gmail_send)

    g.set_entry_point("pull_hubspot")
    g.add_edge("pull_hubspot", "web_search")
    g.add_edge("web_search", "consolidate")
    g.add_edge("consolidate", "retrieve_kb")
    g.add_edge("retrieve_kb", "compose_email")

    # Attach the critique loop: voice → accuracy, both loop back to compose_email
    # on fail with budget remaining; budget exhaustion routes to the helper's
    # shared `failed_terminal`.
    add_critique_loop(
        g,
        draft_node="compose_email",
        critics=[
            Critic("voice", run_voice_critic, DEFAULT_VOICE_MAX_ATTEMPTS),
            Critic("accuracy", run_accuracy_critic, DEFAULT_ACCURACY_MAX_ATTEMPTS),
        ],
        pass_target="propose_send",
    )

    g.add_edge("propose_send", "gmail_send")
    g.add_edge("gmail_send", END)

    return GraphSpec(
        graph=g,
        interrupt_before=("gmail_send",),
        owning_agent=OWNING_AGENT,
    )
