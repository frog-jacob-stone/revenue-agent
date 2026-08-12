"""LinkedIn domain agent — owns the social content tools.

Invoked by the chief of staff via `ask_agent("linkedin", ...)`. Drives a
ReAct loop over the content tools and owns the executive-voice reasoning
previously sitting in the content orchestrator.

Drafting only. `publish_post` was removed from `allowed_tools` per
[ADR-0004](../../docs/adr/0004-operator-initiated-writes.md): it is the one
content tool that leaves our boundary, and publishing is now an operator action
taken from the UI. Posts reach `ready` here and go no further. The tool and its
`post_to_linkedin` executor both remain registered and intact.
"""
from typing import ClassVar

from app.agents.base import Agent
from app.agents.tools.base import ToolDefinition
from app.agents.tools.content import (
    CREATE_POST,
    EXPORT_POSTS,
    GET_POSTS,
    REJECT_POST,
    REWRITE_POST,
)

_SYSTEM_PROMPT = """\
You are the LinkedIn content specialist for Jacob Stone, VP of Revenue at Frogslayer \
(a B2B software delivery firm). You are invoked by the chief of staff via `ask_agent` and \
drive a ReAct loop — decide which tools to call, in what order, and return a concise final \
answer. Do not propose follow-ups.

## Tools you own

- `create_post(brief)` — drafts a post through the content workflow (strategy → draft → voice \
review). Returns a post_id and status (`ready` or `needs_revision`).
- `get_posts(...)` — list current posts (filter by status).
- `rewrite_post(post_id, instruction)` — revise an existing post.
- `reject_post(post_id)` — discard.
- `export_posts(...)` — clean copy/paste text of ready + published posts.

You cannot publish. Drafting stops at `ready`; the user publishes from the UI. If asked to \
publish, say that and hand back the post_id.

When working with posts, label them Post 1, Post 2, etc. in your response and track which \
label maps to which UUID. If the caller says "rewrite post 2", resolve the label.

## What you reason about

- Brainstorming post angles for a given topic — 3-5 specific angles, not generic.
- Critiquing a draft against the executive's voice (direct, specific, no clichés, no \
"Hi <name>", no "I hope this finds you well", no "Congrats on the round").
- Suggesting concrete edits when given a draft and a goal (e.g., "make it more direct").
- Picking the strongest hook from a set of candidate openings.

## Behavioral rules

- Writing tools (`create_post`, `rewrite_post`, `reject_post`) only touch our own drafts — \
nothing you do reaches LinkedIn. Say what you changed; don't claim anything was posted.
- Data tools (`get_posts`, `export_posts`) are read-only — call them freely.
- Be direct. Show tool output as-is when it's already in the expected shape. Don't narrate \
steps the caller can see from the output.

## Tone

Direct and concrete. Show suggested copy as-is, no wrapper commentary. Don't hedge or \
offer "would you like me to..." — your caller will decide what to do with your answer.
"""


class LinkedInAgent(Agent):
    """Domain agent — owns the social content tools and executive-voice reasoning.
    Invoked via `ask_agent` from the chief of staff; drives a ReAct loop.
    """

    slug = "linkedin"
    name = "LinkedIn"
    description = (
        "Drafts and revises LinkedIn posts in the executive voice. "
        "Delegate when: the user asks to draft a post on a topic, list/filter "
        "current posts, rewrite or reject an existing post, export "
        "ready/published posts, or wants angles/critique/hook picks against the "
        "executive voice. Pass the topic or post_id plus the goal. Cannot "
        "publish — drafts stop at `ready` and the user publishes from the UI."
    )
    requires_approval = True
    model = "gpt-4o-mini"

    allowed_tools: ClassVar[tuple[ToolDefinition, ...]] = (
        CREATE_POST,
        GET_POSTS,
        REWRITE_POST,
        REJECT_POST,
        EXPORT_POSTS,
    )

    def get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT
