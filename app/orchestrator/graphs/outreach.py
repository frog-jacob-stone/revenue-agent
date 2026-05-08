"""outreach_chain — personalised outbound email with two critique loops.

Ten nodes, one interrupt gate, two critique loops sharing one `compose_email` node:

    [entry] → pull_hubspot → web_search → consolidate → retrieve_kb → compose_email → voice_critique
                                                                        ▲          │
                                                                        │          ▼
                                                                        │   ┌──────┼──────────┐
                                                                        │ pass  fail+      fail+
                                                                        │  │   budget    exhausted
                                                                        │  ▼     │            │
                                                                        │ accuracy_critique   │
                                                                        │  │                  │
                                                                        │  ▼                  │
                                                                        │ ┌────┼──────────┐   │
                                                                        │ pass fail+    fail+ │
                                                                        │  │  budget  exhaust │
                                                                        │  │   │          │   │
                                                                        └──┘ (loop)       ▼   ▼
                                                                        │             failed_terminal
                                                                        ▼                    │
                                                                  propose_send               │
                                                                        │                    │
                                                                  [interrupt_before          │
                                                                   gmail_send]               │
                                                                        │                    │
                                                                        ▼                    │
                                                                  gmail_send → END           │
                                                                                             ▼
                                                                                            END

Key design points:

- `voice_attempts` and `accuracy_attempts` are independent counters with
  independent `max_attempts` ceilings (defaults: 3 voice, 2 accuracy). When
  accuracy fails and we loop back to draft, voice runs again on the new
  draft — voice_attempts continues to accumulate.
- The most recent failed critique is surfaced into the next draft prompt via
  `state.last_critique_feedback`. The `draft` node clears it after consumption
  so each redraft sees only one critic's feedback at a time.
- LLM calls go through `invoke_agent` because all three outreach agents
  (outreach-agent, voice-critic, accuracy-critic) are in `AGENTS` and
  Anthropic-backed. `invoke_agent` emits AGENT_INVOKED/AGENT_COMPLETED audit
  events automatically.
- No infinite-loop guard at framework level; the two budgets bound the loop
  in practice (max ~5 drafts before terminal failure).
"""
from __future__ import annotations

import logging
from typing import Any, NotRequired
from uuid import UUID

from langgraph.graph import END, StateGraph

from app.config import settings
from app.db import get_pool
from app.lib.json_utils import parse_json
from app.orchestrator.agent_invoke import NodeContext, invoke_agent
from app.orchestrator.runner import GraphSpec
from app.orchestrator.state import BaseGraphState

logger = logging.getLogger(__name__)


OUTREACH_KIND = "outreach_chain"
OUTREACH_AGENT_SLUG = "outreach-agent"
VOICE_CRITIC_SLUG = "voice-critic"
ACCURACY_CRITIC_SLUG = "accuracy-critic"

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

    # Critique state (independent budgets)
    voice_attempts: NotRequired[int]
    voice_max_attempts: NotRequired[int]
    last_voice_critique: NotRequired[dict[str, Any]]

    accuracy_attempts: NotRequired[int]
    accuracy_max_attempts: NotRequired[int]
    last_accuracy_critique: NotRequired[dict[str, Any]]

    # Most recent failed critique — surfaced in the next draft prompt; cleared after consumption
    last_critique_feedback: NotRequired[dict[str, Any] | None]

    # Approval bridge
    executed_payload: NotRequired[dict[str, Any]]

    # Terminal states
    result: NotRequired[dict[str, Any]]
    failure_reason: NotRequired[str]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ctx_from_state(state: OutreachState) -> NodeContext:
    """Build a NodeContext for invoke_agent from the workflow_id in state."""
    wf_id = state.get("workflow_id")
    parent_id = state.get("parent_workflow_id")
    return NodeContext(
        workflow_id=UUID(wf_id) if wf_id else None,  # type: ignore[arg-type]
        parent_workflow_id=UUID(parent_id) if parent_id else None,
    )


async def _load_voice_profile() -> str:
    """Read the most recent voice profile preference memory written for the
    voice-critic agent."""
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
        VOICE_CRITIC_SLUG,
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
    response = await invoke_agent(
        OUTREACH_AGENT_SLUG,
        {"prompt": prompt, "max_tokens": 400},
        _ctx_from_state(state),
    )
    return {"brief": response["text"].strip()}


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


async def draft(state: OutreachState) -> OutreachState:
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

    response = await invoke_agent(
        OUTREACH_AGENT_SLUG,
        {"prompt": prompt, "max_tokens": 600},
        _ctx_from_state(state),
    )
    subject, body = _parse_email(response["text"])

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


async def voice_critique(state: OutreachState) -> OutreachState:
    """Run the Voice Critic against the latest draft. Increments voice_attempts.
    Sets `last_critique_feedback` on fail so the next draft can address it."""
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
    response = await invoke_agent(
        VOICE_CRITIC_SLUG,
        {"prompt": prompt, "max_tokens": 400},
        _ctx_from_state(state),
    )
    critique = _parse_critique(response["text"])

    update: dict[str, Any] = {
        "voice_attempts": state.get("voice_attempts", 0) + 1,
        "last_voice_critique": critique,
    }
    if not critique.get("passed"):
        update["last_critique_feedback"] = critique
    return update


async def accuracy_critique(state: OutreachState) -> OutreachState:
    """Run the Accuracy Critic against the latest draft. Increments accuracy_attempts.
    Sets `last_critique_feedback` on fail so the next draft can address it."""
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
    response = await invoke_agent(
        ACCURACY_CRITIC_SLUG,
        {"prompt": prompt, "max_tokens": 400},
        _ctx_from_state(state),
    )
    critique = _parse_critique(response["text"])

    update: dict[str, Any] = {
        "accuracy_attempts": state.get("accuracy_attempts", 0) + 1,
        "last_accuracy_critique": critique,
    }
    if not critique.get("passed"):
        update["last_critique_feedback"] = critique
    return update


async def propose_send(state: OutreachState) -> OutreachState:
    """Surface the draft for human review on the approval gate."""
    draft_payload = state.get("draft_email") or {}
    return {
        "_propose": {
            "action_type": ACTION_TYPE_SEND,
            "agent_slug": OUTREACH_AGENT_SLUG,
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


async def failed_terminal(state: OutreachState) -> OutreachState:
    """Terminal failure node: critique budget exhausted on either voice or accuracy."""
    last = state.get("last_critique_feedback") or {}
    return {
        "result": {
            "outcome": "failed",
            "reason": state.get("failure_reason") or "critique budget exhausted",
            "last_feedback": last.get("feedback"),
        },
    }


# ── Routing ──────────────────────────────────────────────────────────────────


def route_after_voice(state: OutreachState) -> str:
    last = state.get("last_voice_critique") or {}
    if last.get("passed"):
        return "accuracy_critique"
    attempts = state.get("voice_attempts", 0)
    max_attempts = state.get("voice_max_attempts", DEFAULT_VOICE_MAX_ATTEMPTS)
    if attempts >= max_attempts:
        return "failed_terminal"
    return "compose_email"  # loop


def route_after_accuracy(state: OutreachState) -> str:
    last = state.get("last_accuracy_critique") or {}
    if last.get("passed"):
        return "propose_send"
    attempts = state.get("accuracy_attempts", 0)
    max_attempts = state.get("accuracy_max_attempts", DEFAULT_ACCURACY_MAX_ATTEMPTS)
    if attempts >= max_attempts:
        return "failed_terminal"
    return "compose_email"  # loop (will re-run voice on the new draft too)


# ── Graph factory ────────────────────────────────────────────────────────────


def build_graph() -> GraphSpec:
    g: StateGraph = StateGraph(OutreachState)

    g.add_node("pull_hubspot", pull_hubspot)
    g.add_node("web_search", web_search)
    g.add_node("consolidate", consolidate)
    g.add_node("retrieve_kb", retrieve_kb)
    g.add_node("compose_email", draft)
    g.add_node("voice_critique", voice_critique)
    g.add_node("accuracy_critique", accuracy_critique)
    g.add_node("propose_send", propose_send)
    g.add_node("gmail_send", gmail_send)
    g.add_node("failed_terminal", failed_terminal)

    g.set_entry_point("pull_hubspot")
    g.add_edge("pull_hubspot", "web_search")
    g.add_edge("web_search", "consolidate")
    g.add_edge("consolidate", "retrieve_kb")
    g.add_edge("retrieve_kb", "compose_email")
    g.add_edge("compose_email", "voice_critique")
    g.add_conditional_edges(
        "voice_critique",
        route_after_voice,
        {
            "accuracy_critique": "accuracy_critique",
            "compose_email": "compose_email",
            "failed_terminal": "failed_terminal",
        },
    )
    g.add_conditional_edges(
        "accuracy_critique",
        route_after_accuracy,
        {
            "propose_send": "propose_send",
            "compose_email": "compose_email",
            "failed_terminal": "failed_terminal",
        },
    )
    g.add_edge("propose_send", "gmail_send")
    g.add_edge("gmail_send", END)
    g.add_edge("failed_terminal", END)

    return GraphSpec(graph=g, interrupt_before=("gmail_send",))
