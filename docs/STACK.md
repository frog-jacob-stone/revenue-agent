# Revenue Agent System — Project Context

## What We're Building
An AI-powered revenue operations system for Frogslayer (a software development company) 
where agents handle the work of an entire revenue team. This is not a personal assistant — 
it's infrastructure intended to eventually run the full revenue org.

## My Role
VP of Revenue at Frogslayer, overseeing marketing and sales/go-to-market. I have coding 
experience and can build when needed. Currently running things locally, with cloud 
deployment as a future goal.

## Existing Tools & Accounts
- **HubSpot** — CRM, marketing automation, contact/company data
- **Supabase** — already in use for a separate Lovable app (Frogslayer financial planning)
- **n8n** — workflow automation, self-hosted, used for various integrations
- **Apollo.io** — account-based marketing and SDR research
- **Anthropic API** — Claude, primary LLM for all agents
- **Lovable** — used for rapid UI building (familiar with it)
- **Microsoft 365** — Word, Sharepoint, Powerpoint
- **Google Workspace** — Docs, Drive, Slides, Gmail

## Agreed Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Web UI | Lovable + Supabase or React/Next.js | Agent dashboard, approval inbox, chat |
| Agent logic | FastAPI + Anthropic SDK (Python) | Real code, testable, version controlled |
| Memory & state | Supabase (Postgres + pgvector) | Single source of truth |
| Integration bus | n8n (scoped to routing/integrations only) | Not for complex logic |
| Secrets | Doppler | Local and cloud, same config |
| Runtime | Docker Compose locally → Railway/Render later | |

## Key Architectural Decisions
- n8n handles triggers and third-party integrations only — complex logic moves to FastAPI
- All agents call FastAPI endpoints; n8n calls FastAPI, not the other way around
- Human-in-the-loop approval required before ANY create, update, or delete operation
- Approval inbox lives in the web UI with Slack as a fallback notification
- Supabase holds: agent memory, action queue (pending approvals), audit log
- Security is a priority — API keys in Doppler, Supabase RLS per agent scope

## Agent Scoping Principles

An agent has a single coherent identity: one job, one audit trail, one approval context. When deciding whether a capability belongs in an existing agent or a new one, apply this four-question test:

1. **Same trigger schedule?** If the new capability runs on a different cadence or trigger source, it likely belongs in a separate agent.
2. **Same approval context?** Same approver, same risk level, same stakes — if not, separate it.
3. **Same action type categorically?** Sending emails and generating invoices are categorically different even if they're part of the same business domain.
4. **Does it propose actions, or does it just answer?** Read-only "answer questions" agents should be separated from write-proposing "do work" agents, even when they operate on the same data.

**Read-only vs. write-proposing is a hard split.** An analytics agent and an operations agent for the same domain are distinct agents — different audit trails, different inbox behavior, different UI presentation.

**Chat is an interface, not an agent type.** The same agent can be triggered by webhook, schedule, or chat. All paths that propose writes flow through the approval inbox. Chat-only read-only agents never touch the inbox.

## Agent Roster (Planned)

| Agent | Trigger | Approval Required? |
|---|---|---|
| SDR Researcher | New account in HubSpot / manual | Before outreach |
| Outreach Agent | SDR Researcher completes | Always — before any email sends |
| Content Writer | Manual or scheduled | Before publishing |
| Proposal Generator | Deal stage change in HubSpot | Before sending to client |
| Slide Deck Agent | Triggered by Proposal Generator | Before delivery |
| Revenue Recognition | Monthly schedule | Before any log write |
| Invoice Operations | HubSpot webhook / schedule / chat | Always — before generate, edit, send, or digest |
| Invoice Analytics | Chat (read-only) | Never (proposes no actions) |
| Router (future) | Chat | No (proposes no actions, just routes) |

> Each business domain is expected to have both an operations agent (write-proposing) and an analytics agent (read-only) as the roster matures. Revenue Recognition, for example, will eventually have a Revenue Analytics sibling.

## Multi-Agent Orchestration (Future)

Three patterns for routing chat to the right agent, in order of planned adoption:

1. **Explicit agent selection (v1 — UI agent selector):** User picks the agent directly in the chat interface. Simple, no magic. Build this first.
2. **Router agent:** Reads the user's message, determines intent, hands off to the appropriate specialist. Specialist agents remain directly selectable even after a router exists — the router is a convenience front door, not a gatekeeper.
3. **Full orchestrator:** Decomposes multi-step requests that span multiple agents and coordinates execution across them.

**When to build the router:** Only after 4–6 specialists exist and real routing patterns are visible from usage. Building it too early means building it against hypothetical routing needs.

**Specialist agents stay chattable directly.** A user should always be able to go straight to an agent — the router never becomes the only path in.

## Build Sequence (Agreed)
1. Supabase schema — agent memory, action queue, audit log tables
2. FastAPI skeleton — one endpoint per agent, basic structure
3. Approval inbox UI — built once, reused by every agent
4. First agent end-to-end — SDR Researcher (low risk, high value)
5. Move complex n8n logic to FastAPI — revenue recognition first
6. Expand agent roster from there
7. Invoice Operations agent + Invoice Analytics agent (first domain with both write and read agents)
8. Router agent (once 4+ specialists exist)
9. Shared memory patterns between agents (agents leaving notes for each other via memories table)
10. Full orchestrator for multi-step cross-agent workflows

## Things to Avoid
- Putting business logic in n8n function nodes
- Agents taking any CUD action without human approval
- Storing secrets anywhere other than Doppler
- Building agent-specific UI before the shared approval inbox exists

## Open Questions to Revisit
- Whether to use CrewAI/LangGraph for multi-agent orchestration or build lighter with 
  direct Anthropic SDK calls
- Vector search strategy for the knowledge base (pgvector vs. a dedicated store)
- Role-based approvals as the team grows (who approves what)
- When to introduce the router agent (threshold: ~4–6 specialists + observed routing friction)
- How memory sharing between agents should be scoped (per-agent vs. shared-by-domain vs. fully global)

## When to Use Chat vs. Claude Code

### Stay in Chat (this project) for:
- Architecture and schema design decisions
- Writing and refining agent prompts and SOUL definitions
- Reviewing agent outputs before approval
- Planning the next build sprint
- Debugging logic or behavior (not code)
- Any session where you're not at your desk with a codebase open

### Switch to Claude Code when:
- Writing the FastAPI app and its endpoints
- Setting up Docker Compose and local infrastructure
- Creating and running Supabase migrations
- Building the approval inbox UI (if not using Lovable)
- Debugging actual code errors
- Any task where you need to read, write, or run files

### The trigger phrase:
When a chat session ends with a clear build task — e.g. "okay let's write the 
Supabase schema" or "let's scaffold the FastAPI app" — that's the signal to open 
Claude Code pointed at your local repo and continue there.

### Project setup note:
Claude Code can be opened within this same Chat Project so it inherits context.
Keep a /docs folder in your repo with key reference files:
- docs/STACK.md — this document, kept current
- docs/SCHEMA.md — Supabase table definitions as they evolve
- docs/AGENTS.md — agent prompt templates and behavioral rules
- docs/WORKFLOWS.md — which n8n workflows exist and what they hand off to FastAPI