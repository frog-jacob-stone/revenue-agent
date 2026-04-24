# UI Requirements — Frogslayer Revenue Agent System

## Meta / Constraints
- Single-user for now; auth should exist (Supabase Auth) but no role/permission UI needed in v1
- Design data layer to support multi-user later (`user_id` on all relevant tables)
- Built in React (Lovable or Next.js) + Supabase
- All components should be reusable across agents
- All destructive/write actions require explicit approval — no fire-and-forget buttons
- Desktop-first responsive design (this is an ops tool, not mobile)
- **Unimplemented features must display a visible `[NOT IMPLEMENTED]` badge or banner** — see implementation status convention below

---

## Implementation Status Convention

Every feature that is stubbed but not yet wired to the backend must be visually marked.

- Use a small amber/yellow badge labeled `NOT IMPLEMENTED` next to buttons, sections, or inputs that are stubs
- Stub API calls should return `null` or an empty array — never throw
- A global `STUB_MODE = true` flag in a `config.ts` (or equivalent) controls which features show the badge
- When a feature is fully implemented, remove the badge and flip the flag for that feature
- This makes it easy to scan the UI and know what's real vs. what's scaffolding

---

## Module 1 — Approval Inbox *(build first — blocks everything else)*

### Core Functionality
- List view of all pending approval items, newest first
- Each item displays:
  - Agent name
  - Action type: `create` / `update` / `delete`
  - Target entity (contact name, deal name, etc.)
  - Timestamp queued
  - Brief summary of proposed action
- Click item → detail view with:
  - Full agent output
  - Exact proposed action payload (formatted JSON or readable summary)
- **Approve** button — calls FastAPI approval endpoint, marks item approved, logs to audit log
- **Reject** button — requires free-text reason; logs to audit log
- **Edit & Approve** — inline editing of proposed output before approval (critical for outreach copy, proposals)
- Badge/count on inbox nav item showing pending count
- Empty state when queue is clear

### Filtering & Organization
- Filter by: agent name, action type, status (pending / approved / rejected)
- Sort by: date queued (default newest first)

### Realtime
- Supabase Realtime subscription on the action queue table — new items appear without page refresh

---

## Module 2 — Agent Dashboard *(home screen)*

- Summary cards for each agent showing:
  - Last run time
  - Current status: `idle` / `running` / `waiting for approval` / `error`
  - Items pending approval (count)
  - Items actioned today (count)
- Quick-trigger button per agent (for agents that support manual triggering: SDR Researcher, Content Writer)
- Global system status indicator: all agents healthy / any errors
- Recent activity feed — last 10–20 actions across all agents with status and timestamp

---

## Module 3 — Agent Detail Pages *(shared template, one page per agent)*

### Shared Sections (every agent)
- Agent status indicator (`idle` / `running` / `paused`)
- Enable / Disable toggle (pauses agent without deleting config)
- Last run summary (when, what it did, outcome)
- Pending approvals panel (filtered inbox view for this agent only)
- Run history (last N runs — timestamp, outcome, items produced)
- Manual trigger button (where applicable)
- For read-only analytics agents, the "Pending approvals panel" and "Run history" sections are hidden; replaced with "Recent queries" showing chat history and questions answered.

### Agent-Specific Config Panels

| Agent | Config Fields |
|---|---|
| SDR Researcher | Target account criteria, research depth, HubSpot list selector |
| Outreach Agent | Email tone/persona selector, sequence steps (list), daily send cap |
| Content Writer | Content type, target audience, topic/keyword input, publishing destination |
| Proposal Generator | Template selector, default sections checklist, pricing tier inputs |
| Slide Deck Agent | Theme/template selector, output format (PPTX / Google Slides) |
| Revenue Recognition | Reporting period selector, source accounts to include |
| Invoice Operations | Invoice template selector, default payment terms, Slack channel for review digest, send schedule |
| Invoice Analytics | Data sources to query (invoice store, HubSpot), default reporting period |
| Router (future) | Registered specialist agents, routing prompt, fallback behavior when intent is unclear |

---

## Module 4 — Audit Log

- Full chronological log of all agent actions
- Columns: timestamp, agent, action type, target entity, outcome, approved/rejected by, reason (if rejected)
- Filters: agent, date range, outcome
- Click row → expand to see full action payload
- Export to CSV button

---

## Module 5 — Agent Chat Interface

- Conversational interface to interact with any agent directly
- Agent selector at top of chat (context switches per agent)
- Supports: researching a specific account, drafting copy on demand, explaining last decision
- Chat history persisted in Supabase per agent session
- Actions proposed in chat still route to approval inbox — no direct execution from chat
- Markdown rendering in agent responses (tables, bullets, code blocks)
- Ability to attach context (paste company description, deal notes, etc.)

### Routing Behavior

- **v1 — Explicit agent selector:** Agent selector at top of chat; user picks which agent they're addressing. Simple, no routing logic required.
- **Future — Router agent as default option:** Router agent appears as the default selection in the agent selector. It reads the message and dispatches to the appropriate specialist. Specialist agents remain directly selectable even after the router exists.
- **Analytics agents** respond inline with answers — no inbox entry is created.
- **Ops agents** respond with a "proposed action — see inbox" confirmation when a write is triggered.

---

## Module 6 — Knowledge Base / Memory Viewer

- Read-only view of each agent's vector memory (Supabase/pgvector)
- List of memory entries per agent: content preview, source, date stored, relevance tags
- Delete memory entry (with confirmation dialog)
- Add memory entry manually (text input → saved to agent memory store)
- Search across memory entries for a given agent

---

## Module 7 — Analytics

- Date range picker: last 7 / 30 / 90 days, custom range
- **Pipeline metrics**: accounts researched, outreach sent, reply rate, deals progressed
- **Content metrics**: pieces drafted, published, type breakdown
- **Proposal metrics**: proposals generated, sent, win/loss
- **Agent activity**: runs per day per agent (bar or line chart)
- **Approval behavior**: approval rate, avg time to approve, most-rejected agent
- All data sourced from Supabase audit log and action queue — no separate analytics DB needed in v1

---

## Module 8 — Settings

- HubSpot: connection status + reconnect (OAuth)
- Apollo.io: API key status
- Anthropic API: health ping display
- Slack: webhook URL input (fallback notifications)
- Agent scheduling: cron expression editor per scheduled agent (Revenue Recognition, Content Writer)
- Timezone setting

---

## Global UI Requirements

- Supabase Realtime on approval inbox (no manual refresh)
- Loading states on all async operations
- Error states on all async operations (don't silently fail)
- Consistent confirm pattern — approve/reject/trigger shows confirm step or is clearly reversible
- Sidebar navigation: all modules listed, inbox badge always visible
- `NOT IMPLEMENTED` badges visible on all stubbed features (see convention above)
