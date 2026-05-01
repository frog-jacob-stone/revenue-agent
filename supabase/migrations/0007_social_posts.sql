-- Social post drafts and conversational approval queue for the content creation workflow.
-- Separate from workflows/actions because content creation has no external writes until published;
-- approval is conversational (chat), not inbox-based.

CREATE TABLE social_posts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    topic       TEXT        NOT NULL,
    idea_title  TEXT,
    core_angle  TEXT,
    post_text   TEXT,
    status      TEXT        NOT NULL DEFAULT 'draft',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Status values:
--   draft            text written, not yet voice-reviewed (first status a row ever has)
--   needs_revision   voice agent flagged issues; rewrite → back to draft
--   ready            passed voice review, available for publishing
--   rejected         user rejected
--   published        went through publishing workflow and was approved

CREATE INDEX social_posts_status ON social_posts (status);

ALTER TABLE social_posts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service_role_all" ON social_posts FOR ALL USING (true) WITH CHECK (true);

CREATE TRIGGER set_social_posts_updated_at
    BEFORE UPDATE ON social_posts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
