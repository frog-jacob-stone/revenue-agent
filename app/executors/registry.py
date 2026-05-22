"""Executor registry.

The single source of truth for which executors exist and what name each
goes by. Looked up by the approval-grant handler when an approval has
`executor IS NOT NULL`.

Executors are added here in their respective migration phases:
  - `post_to_linkedin`         — plan 17 (migrate content_publish)
  - `write_rev_rec_entries`    — plan 18 (migrate rev_rec_monthly)
"""
from app.executors.base import ExecutorDefinition
from app.executors.post_to_linkedin import POST_TO_LINKEDIN


EXECUTORS: tuple[ExecutorDefinition, ...] = (
    POST_TO_LINKEDIN,
)


EXECUTORS_BY_NAME: dict[str, ExecutorDefinition] = {e.name: e for e in EXECUTORS}
