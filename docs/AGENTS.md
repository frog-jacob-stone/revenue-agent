# Agents

The roster of agents in the system. Update when an agent is added, retired, or changes scope.

For the rules that govern how agents are bounded, see `ARCHITECTURE.md` (Agent Scoping Principles).

## Roster

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

## Prompts and SOUL Definitions

_Agent prompt templates and behavioral rules live alongside the agent code in `app/agents/<agent>/`. Add references here as agents stabilize._
