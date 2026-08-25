# Remove the social-content feature

The LinkedIn agent, its six content tools, the `post_to_linkedin` executor, the
`social_posts` table, and every doc section describing them are removed. Nothing
replaces them. This is a deletion, not a migration — there is no successor
feature to point a reader at.

Removed in code:

- `app/agents/linkedin_agent.py` — the `linkedin` domain agent
- `app/agents/tools/content/` — `create_post`, `rewrite_post`, `reject_post`,
  `get_posts`, `export_posts`, `publish_post`, and the voice/strategy prompts
- `app/executors/post_to_linkedin.py` and its registry entry
- `app/services/social_posts.py`
- `tests/test_create_post_tool.py`, `tests/test_publish_post_tool.py`
- The `linkedin` entries in `ui/src/agents.ts`, the `create_post:*` step labels in
  `nodeLabels.ts` / `activity_builder.py`, and the `post_to_linkedin` payload
  preview in `InboxList.tsx`
- Migration `0034` drops `social_posts`

## Why

The feature was built for one person's LinkedIn presence. This system is the
firm's revenue operations infrastructure — [ADR-0005](0005-revenue-operations-platform-identity.md)
already recorded that identity shift, and social content is the one remaining
subsystem that shift left behind. Keeping it means every reader of the registry,
the schema, and the architecture doc has to work out which parts are RevOps and
which are a personal tool that happens to share a database.

Cost of keeping it was not zero. It was the largest agent surface in the repo by
tool count (six of the eight tools an LLM could reach), which made it the
implicit example in the tool-layer docs, the fixture in unrelated tests
(`test_activity_builder`, `test_chat_turn` both used `create_post` as their
generic tool name), and the reason `app/agents/tools/` needed a `content/`
domain at all. None of that was paying for itself: `post_to_linkedin` never had a
LinkedIn integration — it logged the would-be post and flipped a status column —
so the feature could not actually publish anything at any point in its life.

## What this changes about the agent framework

The roster is two workers instead of three: `bdr` and `revenue-ops` behind the
`chief-of-staff` front door. `write_rev_rec_entries` is now the only registered
executor, and `trigger_revenue_recognition` the only tool returning
`AwaitingApproval`. Per [ADR-0004](0004-operator-initiated-writes.md) it is
still unreachable from any agent, so the inbox is still empty by construction —
`tests/test_no_agent_approval_tools.py` now uses that tool as the fixture proving
its scan can detect a violation, a job `publish_post` used to do.

## What was deliberately left alone

**Historical audit rows.** `audit_log` holds 32 `content.*` events and `approvals`
holds one rejected `post_to_linkedin` row. `audit_log` is append-only by trigger,
and these record work that really happened. Deleting them would be falsifying the
record to tidy a vocabulary.

**The retired `agents` row.** `seed_agents` only inserts, so the `linkedin` row
stays behind and is pointed at by `audit_log.agent_id`, `llm_calls`, and
`agent_messages`. Dropping it would erase attribution. Instead `GET /agents` now
filters to the Python registry, so a retired slug never reaches the UI — this
also cleared a pre-existing `content-orchestrator` orphan.

**Migrations `0007` and `0008`.** Already applied everywhere; deleting the files
would put local and remote migration history out of sync. `0034` drops what
`0007` created. `0008`'s `action_type` enum value cannot be removed — Postgres
has no `DROP VALUE` — and the type has been orphaned since `0014` dropped
`actions`, the only table that used it.

**Earlier ADRs.** ADR-0002's chain-count table and ADR-0004's record of removing
`PUBLISH_POST` from `LinkedInAgent` describe decisions accurately as of when they
were made. Per ADR-0005's own rule — "not a retroactive rewrite… those ADRs
describe decisions accurately as of when they were made" — they keep their text
and get a pointer here.

## Data discarded

`social_posts` held 7 rows at the time of this decision (6 `ready`, 1 `draft`) —
drafts that were never published, because publishing never worked. They go with
the table. If any of that text was wanted, it needed exporting before `0034` runs.
