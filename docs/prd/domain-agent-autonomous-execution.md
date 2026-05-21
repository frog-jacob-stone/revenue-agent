---
status: ready-for-agent
---

## Problem Statement

Every agent capability today either lives as a tool on the orchestrator agent (`revenue-ops`) or as a prescribed workflow graph. There is no middle tier. As capabilities grow, the orchestrator accumulates domain tools it shouldn't own, the tool list becomes unwieldy, and every new feature requires either adding a tool to the front door or writing a full LangGraph graph — even for simple, agentic tasks like "draft a reply to this inbound lead."

Jacob needs a way to say "handle this inbound form submission" to the orchestrator and have the right domain agent figure out what to do — fetching the right context, making decisions about what information is needed, and returning a result — without Jacob having to know which tools that involves.

## Solution

Introduce a ReAct loop primitive (`run_agent_task`) that allows domain agents to run autonomously when given a task. Domain agents declare their own tools via `allowed_tools`. When the orchestrator delegates to a domain agent via `ask_agent`, the call drives a tool-use loop to completion rather than a single LLM turn. The orchestrator stays thin; domain agents own their domain.

The first domain agent to get this capability is the BDR agent, whose first agentic task is: given an email address, find the most recent HubSpot form submission from that person, gather contact and company context, and draft a personalised reply in the BDR voice — all without any prescribed steps.

The draft surfaces in chat only. Nothing is persisted to the database, no approval row is created, and no email is sent.

## User Stories

1. As Jacob, I want to tell the orchestrator an email address and have it produce a draft reply in the BDR voice, so that I don't have to manually look up HubSpot context or write the email myself.
2. As Jacob, I want the orchestrator to delegate outreach tasks to the BDR agent automatically, so that I only have one agent to talk to.
3. As Jacob, I want the draft to appear directly in chat, so that I can review and use it immediately without navigating another UI.
4. As Jacob, I want the draft to be ephemeral (not saved anywhere), so that it is always a starting point I control — nothing is sent or committed without my action.
5. As Jacob, I want the BDR to autonomously decide it needs to fetch HubSpot data and then draft the email, so that I don't have to enumerate the steps when I make the request.
6. As Jacob, I want to be able to ask for a reply for a lead from the last 7 days or the last 14 days depending on context, so that I can handle both fresh and slightly older submissions.
7. As Jacob, I want the BDR to enrich its draft with the lead's company name, job title, and form submission message when available, so that the reply is relevant and personalised.
8. As Jacob, I want to receive a clear message when no matching form submission is found, so that I know the lookup did not silently fail.
9. As Jacob, I want the system to handle a missing or misconfigured HubSpot token gracefully and surface a clear error, so that I'm not left wondering why nothing came back.
10. As Jacob, I want the orchestrator's tool list to stay small and bounded, so that as capabilities grow the system remains navigable and maintainable.
11. As a developer, I want domain agents to declare their own tools as class attributes, so that adding a new BDR capability means changing only the BDR agent — not the orchestrator.
12. As a developer, I want the ReAct loop to have a bounded iteration limit, so that a misbehaving model cannot produce an infinite tool-call chain.
13. As a developer, I want `ask_agent` to automatically detect whether the target has tools and route accordingly, so that callers do not need to know the difference between a single-turn agent and an autonomous one.
14. As a developer, I want the agentic task loop to write the same audit events as single-turn agent invocations (AGENT_INVOKED, AGENT_COMPLETED, AGENT_FAILED), so that observability is consistent across both patterns.
15. As a developer, I want the HubSpot form submission lookup to be testable in isolation with a mock HTTP client, so that tests do not require a live HubSpot token.
16. As a developer, I want BDR's tool execution to use the existing `ToolDefinition.execute` interface, so that the tool layer is consistent whether called from a chat turn or from an agentic task.

## Implementation Decisions

### New primitive: `run_agent_task`

A new function in the orchestrator's agent invocation module alongside the existing `invoke_agent`. Accepts an agent slug, a task string, and an optional context. Resolves the agent class from the registry, builds an initial message list from the agent's system prompt and the task, then runs a loop:

1. Dispatch to the LLM with the agent's tool schemas.
2. If the response contains tool calls: execute each tool, append the results as tool-result messages, continue the loop.
3. If the response has no tool calls (finish_reason is `stop`): return the final text.
4. Hard cap at a configurable max iterations (default 10) to prevent runaway loops.

Attribution on every dispatch: `agent_slug` = target agent's slug, `purpose = "agent:<slug>"`. This is identical to the existing `invoke_agent` attribution pattern.

The same AGENT_INVOKED / AGENT_COMPLETED / AGENT_FAILED audit events bracket the entire task (not per iteration).

Tool execution inside the loop uses `ToolDefinition.execute` directly from the agent's `allowed_tools`. A `ToolContext` is constructed from the agent's slug and agent ID. `workflow_id` is None for agentic tasks that originate from chat (not from inside a graph node).

### `ask_agent` upgrade

The `ask_agent` tool delegates to `run_agent_task` when the target agent has a non-empty `allowed_tools`, and falls back to `invoke_agent` when it does not. Callers (including `revenue-ops`) do not change.

### BDR agent gains `allowed_tools`

The BDR agent declares `allowed_tools` containing three CRM read tools (`get_contact_by_email`, `get_company_by_id`, `get_form_submission`). The BDR remains a `BaseAgent` subclass; `run_agent_task` does not require `ConversationalAgent` — it resolves the system prompt from the `system_prompt` class attribute and the tools from `allowed_tools` directly.

### CRM tools (`app/tools/crm/`)

The original single `find_hubspot_form_submission` tool was split into three orthogonal primitives so the BDR composes them autonomously rather than receiving one pre-bundled payload:

- `get_contact_by_email(email_address)` — returns the contact's profile plus `primary_company_id` (resolved via HubSpot associations API).
- `get_company_by_id(company_id)` — returns firmographic profile for a company.
- `get_form_submission(email_address, form_id?)` — returns the most recent form submission matching the email; `form_id` defaults to the `HUBSPOT_FORM_ID` setting (Frogslayer has one inbound form today).

Each tool returns a structured dict with `status` ∈ {`success`, `not_found`, `error`}. All HubSpot errors (`HubSpotNotConfigured`, `HubSpotAuthError`, `HubSpotApiError`) collapse into `status: error` with a human-readable message.

`app/integrations/hubspot.py` exposes the underlying primitives: `search_contact_by_email`, `get_primary_company_id`, `get_company`, `find_form_submission_for_email`. `app/lib/nomalize_utils.py` is kept. The WIP `app/tools/outreach/` directory and its tests are deleted.

### Revenue-ops agent

Removes the `DRAFT_FORM_SUBMISSION_REPLY` import and tool. The existing `ask_agent` tool already handles BDR delegation — no new tools are added to the orchestrator.

### Draft output shape

When the BDR completes its agentic task, it returns plain text (its final LLM turn). Revenue-ops surfaces this to Jacob verbatim. There is no structured `draft_email` object, no persisted row, no approval. The BDR's system prompt instructs it to output the draft directly in its final response, along with a brief note that nothing has been saved or sent.

### Loop safety

`run_agent_task` enforces a maximum of 10 tool-call iterations. If the limit is reached, the function raises an exception that is caught, AGENT_FAILED is written, and the error surfaces to the caller.

## Testing Decisions

Good tests verify externally observable behaviour, not implementation steps. For the ReAct loop, that means: given a FakeProvider scripted with a sequence of tool-call responses followed by a final text response, does `run_agent_task` return the final text and execute the expected tools? It does not assert on how many times the loop ran internally.

**Modules to test:**

- **`run_agent_task` in isolation** — use `FakeProvider` with a scripted sequence: first response has a tool call, second response is a final answer. Assert the final text is returned and the tool was executed. Also test: max-iterations guard raises when the provider never stops calling tools; AGENT_INVOKED and AGENT_COMPLETED audit events are written (requires test DB). Prior art: `tests/test_agent_invoke.py` for the audit event pattern and `FakeProvider` usage.

- **CRM tools (one test file per tool)** — patch the HubSpot integration functions at each tool's import site (`AsyncMock`). For each tool assert: happy path returns `status: success` with populated fields; missing record returns `status: not_found`; `HubSpotNotConfigured` returns `status: error`; invalid input returns `status: error` without calling HubSpot. Prior art: `tests/test_hubspot_integration.py` for the HubSpot mock patterns.

- **`ask_agent` routing** — assert that when the target agent has `allowed_tools`, `run_agent_task` is called; when it has none, `invoke_agent` is called. No DB required — patch both callees.

- **BDR integration (agentic task end-to-end)** — FakeProvider scripted to chain `get_contact_by_email` → `get_company_by_id` → `get_form_submission` → final draft text. Mock the HubSpot integration functions on each tool's import site. Assert the final text from the BDR contains the expected draft content, that `BDRAgent.system_prompt` was in the first dispatch, and that `runner.start` was never called.

- **Revenue-ops surface check** — assert `draft_form_submission_reply` is no longer in `revenue-ops` allowed tools. Assert `ask_agent` is still present.

## Out of Scope

- Sending the drafted email (requires propose-approve-execute through the outreach graph).
- Persisting the draft to any database table.
- The BDR performing web research or LinkedIn enrichment (future tool addition to the BDR).
- Asynchronous agentic task execution (the task runs synchronously within the chat turn; async is a future concern).
- Multi-step outreach sequences (belong in a prescribed workflow graph, not an agentic task).
- SDR agent or any other domain agent beyond BDR (future).
- Extending `run_agent_task` to support streaming intermediate output back to chat.
- Modifying the existing outreach graph (`app/orchestrator/graphs/outreach.py`).

## Further Notes

The `_multi_agent_demo.py` POC in `app/orchestrator/graphs/` demonstrated the supervisor-worker pattern but implemented it as a prescribed LangGraph graph with fixed nodes. `run_agent_task` supersedes that approach for agentic tasks — no graph required. The demo can remain as a reference but should not be used as a template for new domain agent capabilities.

The BDR's `system_prompt` already instructs it to produce output under 90 words, avoid clichés, and ground every claim in provided context. The agentic task output will naturally inherit this voice because the same system prompt governs both the tool-calling turns and the final drafting turn.

ADR documenting the supervisor-worker architectural decision: `docs/adr/0001-supervisor-worker-multi-agent-architecture.md`.
