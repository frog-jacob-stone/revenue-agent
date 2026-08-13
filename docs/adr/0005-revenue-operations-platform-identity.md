# The system is a Revenue Operations platform, not an agent framework applied to revenue ops

Every top-level doc — CLAUDE.md, README.md, PRD.md, CONTEXT.md — described the system through an agent-first lens: agents as the core mechanism, revenue operations as the domain they happen to operate in. That framing is now inverted. The system's identity is a Revenue Operations platform — deterministic Harvest billing/invoicing automation, revenue recognition, and (planned) revenue/project reporting — with an approval-gated agent framework as one supporting capability, used for conversational and judgment-requiring tasks.

This doesn't change any code. It renames the system in prose (CLAUDE.md, README.md, PRD.md, PROGRESS.md, CONTEXT.md, docs/ARCHITECTURE.md, docs/SCHEMA.md all updated alongside this ADR) and corrects framing so a new reader forms the right mental model on first pass, instead of assuming agents are the primary mechanism and discovering the billing engine as an afterthought.

## Why

[ADR-0004](0004-operator-initiated-writes.md) already recorded the underlying shift in passing: "The system was designed agent-first... The system is becoming automation-first, with chat kept for synthesis and question-answering." That ADR changed the write-authorization rule to fit the shift. It did not update any of the docs that still describe the system's *identity* in agent-first terms.

By the numbers, the shift is not close. The billing/invoicing engine (`app/services/billing/`) is 18 modules and roughly 25 endpoints, with no agent or LLM anywhere in its write path — by design, per ADR-0004: "An LLM in this path would add a failure mode without adding a capability." The agent framework is four agents (`chief-of-staff`, `bdr`, `revenue-ops`, `linkedin`), and per ADR-0004 none of them currently holds a tool that can propose a write at all — the inbox is empty by construction. Describing this system primarily as an agent framework understates its largest, most load-bearing subsystem and misdescribes the one that's smaller.

A related, unforced error compounded this: PRD.md had drifted into describing itself as a "masterclass" for learning to build agent systems, with RevOps as the applied subject matter. That framing was never revisited once the system stopped being a teaching artifact and started being Jacob's actual day-to-day revenue-operations tool.

## What this is not

It is not a claim that the agent framework is deprecated or unimportant. `chief-of-staff` and its domain agents remain the front door for conversational tasks (revenue Q&A, drafting), and the Propose/Approve/Execute pattern remains the correct shape for any future agent-initiated write. This ADR is about which subsystem is the system's *identity* for a reader forming a first impression — not about removing or deprioritizing either one.

It is also not a retroactive rewrite of ADR-0001 through ADR-0004. Those ADRs describe decisions accurately as of when they were made, including ADR-0004's own agent-first framing of the system's origin. This ADR records the point at which the top-level docs caught up to what ADR-0004 had already implied.

## Consequences

- CLAUDE.md, README.md, PRD.md, PROGRESS.md, CONTEXT.md, docs/ARCHITECTURE.md, and docs/SCHEMA.md are retitled and reframed to lead with Revenue Operations, with the agent framework described as a supporting capability.
- PRD.md drops its "masterclass" / teaching framing entirely in favor of a straightforward product PRD for an internal RevOps platform.
- PRD.md and PROGRESS.md gain explicit, marked-not-built roadmap scope for revenue dashboards, project-completion tracking, and revenue-per-project-type reporting — none of which exist in code or schema today.
- No code, tests, migrations, package names, or UI strings changed. The rename is prose-only; a follow-up ADR would be needed to extend it into `pyproject.toml`, `ui/package.json`, the FastAPI app title, or UI copy, if that's ever wanted.
