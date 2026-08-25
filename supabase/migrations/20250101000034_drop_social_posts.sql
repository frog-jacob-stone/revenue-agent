-- 0034: Drop `social_posts`.
--
-- The social-content feature is removed (see docs/adr/0006). It was the last
-- piece of this system built for one person's LinkedIn presence rather than for
-- the firm's revenue operations, and nothing in the codebase reads or writes
-- this table any more: the `linkedin` agent, the six content tools, the
-- `post_to_linkedin` executor, and `app/services/social_posts.py` are all gone.
--
-- Destructive. Any remaining draft rows are discarded — that is the intent, not
-- a side effect. Per DEPLOY.md the API deploy that stops selecting from this
-- table must land before this migration is pushed.
--
-- Not dropped here: the `action_type` enum still carries a `post_to_linkedin`
-- label (added by 0008). Postgres cannot remove a single enum value, and the
-- type has been orphaned since 0014 dropped `actions` — no column references
-- it. Removing the whole type is a separate cleanup, not part of this change.
begin;

drop table if exists social_posts cascade;

commit;
