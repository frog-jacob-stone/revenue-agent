"""OpenAI SDK client construction.

The single place that constructs an `openai.AsyncOpenAI`. Consumed by the
OpenAI provider adapter (`app/integrations/_openai_provider.py`), which is
itself consumed only by the LLM dispatcher (`app/integrations/llm.py`).
Nothing else in the codebase should import from the `openai` package.
"""
from __future__ import annotations

import openai

from app.config import settings

_client: openai.AsyncOpenAI | None = None


def get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _client
