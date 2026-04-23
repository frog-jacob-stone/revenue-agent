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

## Agent Roster (Planned)

| Agent | Trigger | Approval Required? |
|---|---|---|
| SDR Researcher | New account in HubSpot / manual | Before outreach |
| Outreach Agent | SDR Researcher completes | Always — before any email sends |
| Content Writer | Manual or scheduled | Before publishing |
| Proposal Generator | Deal stage change in HubSpot | Before sending to client |
| Slide Deck Agent | Triggered by Proposal Generator | Before delivery |
| Revenue Recognition | Monthly schedule | Before any log write |

## Build Sequence (Agreed)
1. Supabase schema — agent memory, action queue, audit log tables
2. FastAPI skeleton — one endpoint per agent, basic structure
3. Approval inbox UI — built once, reused by every agent
4. First agent end-to-end — SDR Researcher (low risk, high value)
5. Move complex n8n logic to FastAPI — revenue recognition first
6. Expand agent roster from there

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