# Operator-initiated writes need no approval row

The approval chain is no longer the only write path. A write may skip the
`approvals` table when a human initiated it from the UI, provided three
conditions hold:

1. the **exact payload** is shown before the click,
2. the endpoint is **human-only** — never in any agent's `allowed_tools`, never
   an executor,
3. the state transition writes `audit_log`.

Agent-initiated writes are unchanged: `AwaitingApproval` → pending approval →
human approves → executor runs. This supersedes nothing in
[ADR-0002](0002-tools-not-graphs.md) or [ADR-0003](0003-single-agent-class-structural-delegation.md)
about how *agents* act. It narrows the scope of what those ADRs assumed was
universal.

## Why

The system was designed agent-first. That is why the approval inbox was
load-bearing: an LLM proposed an action, no human was present at the moment of
proposal, and the inbox was where a human entered the loop to authorize a
specific payload before it took effect.

The system is becoming automation-first, with chat kept for synthesis and
question-answering. Agentic execution is deferred — possibly permanently. In the
automation shape the human is not absent; they are the one clicking. Invoicing is
the first module built this way: the operator opens a draw, reads the computed
invoice, and clicks *Create draft in Harvest*.

Routing that through an approval row would mean the operator authorizes the same
payload twice — once by clicking the button, once by approving the row their own
click created. That is ceremony, not safety. Worse, it is *misleading* ceremony:
an inbox whose rows are all self-approved teaches the operator to clear rows
without reading them, which degrades the inbox for the case it exists to protect.

So the invariant worth stating is not "there is an approval row." It is:

> **No write happens without a human authorizing that specific payload.**

An approval row is one way to get that. A confirmed click on a screen showing the
payload is another, and it is the better fit when the human is already present.

## What this is not

It is not "the UI can write freely." Condition 2 is the load-bearing one and it
is the same structural standard [ADR-0002](0002-tools-not-graphs.md) sets for
executors: the LLM must not be able to reach the write through *any* tool
surface. A human-only endpoint that later gets wrapped in a tool has silently
become an agent-initiated write with no approval row — the exact failure this ADR
must not license.

That is enforced by test, not by comment. `tests/test_no_agent_approval_tools.py`
walks every tool in every agent's `allowed_tools` and fails the build if its
handler source contains `AwaitingApproval`; the invoicing service functions are
in no tool module at all. The trust boundary stays structural.

Nor is it a deprecation of the approvals module. Nothing in
`app/services/approvals.py`, `app/executors/`, or `/approvals` is removed. Both
existing executors (`post_to_linkedin`, `write_rev_rec_entries`) stay registered
and intact. If the system evolves back toward agentic execution, the machinery is
there — unused, not deleted.

## Considered options

**Option discarded: route operator writes through approvals anyway, auto-granting
them.** Keeps one code path, and was tempting for exactly that reason. Rejected
because the audit trail it produces is a lie — `approved_by` would record a human
decision that never happened as a separate act, and the inbox would fill with
rows that were approved before they were visible. One code path is not worth a
dishonest log.

**Option discarded: keep the rule and give invoicing a real inbox flow.** This is
the status quo extended. Rejected on the double-authorization argument above, and
because it makes the operator's monthly work slower in a way that buys nothing:
the payload they would approve in the inbox is the payload they were just looking
at when they clicked.

**Option discarded: drop the rule entirely; rely on auth plus `audit_log`.**
Rejected because it erases the distinction that matters. The reason an agent write
needs an approval row is not that writes are dangerous in general — it is that
*nobody was watching* when the LLM decided. Collapsing agent and operator writes
into one permissive rule would leave nothing to point at when agentic execution
returns.

## Consequences

- CLAUDE.md Unbreakable Rule 1 restated in two-initiator form. Rules 2 (auth) and
  3 (approvals are human-only) unchanged.
- The draw invoice path is operator-initiated: `POST /billing/draws/{id}/invoice`
  and `POST /billing/runs/{run_id}/items/{item_id}/resolve` are human-only,
  documented as such the way `release_draw` already is, and audit-logged.
- `PUBLISH_POST` removed from `LinkedInAgent.allowed_tools`;
  `TRIGGER_REVENUE_RECOGNITION` removed from `RevenueOpsAgent.allowed_tools`.
  These were the only two tools in the codebase returning `AwaitingApproval`, so
  the inbox is now empty by construction. `create_post`, `rewrite_post`, and
  `reject_post` stay agent-callable — they return `Done()` and write only to the
  internal `posts` table.
- Rev rec consequently has **no runner** until it gets a UI button of the same
  shape. Tracked as follow-on work; the executor is untouched and waiting.
- `tests/test_no_agent_approval_tools.py` makes condition 2 a build failure
  rather than a convention.
