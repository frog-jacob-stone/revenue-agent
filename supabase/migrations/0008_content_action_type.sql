-- Add post_to_linkedin to the action_type enum for the content publishing workflow.
ALTER TYPE action_type ADD VALUE IF NOT EXISTS 'post_to_linkedin';
