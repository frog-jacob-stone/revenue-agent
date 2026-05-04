import openai

from app.config import settings

_client: openai.AsyncOpenAI | None = None


def get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def call_openai(
    system: str,
    user: str,
    *,
    model: str,
    max_tokens: int = 800,
) -> str:
    """Single OpenAI chat completion with JSON response format."""
    client = get_client()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or "{}"
