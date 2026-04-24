import json
import logging
from typing import Any
from uuid import UUID

from app.agents.base import ConversationalAgent
from app.integrations.openai_client import get_client
from app.services.agent_runner import AGENT_CLASSES

logger = logging.getLogger(__name__)


def _get_agent(slug: str) -> ConversationalAgent:
    cls = AGENT_CLASSES.get(slug)
    if not cls:
        raise ValueError(f"Chat not supported for agent '{slug}'")
    agent = cls(agent_id=UUID(int=0), config={})  # no workflow DB row for chat
    if not isinstance(agent, ConversationalAgent):
        raise ValueError(f"Agent '{slug}' does not support chat")
    return agent


async def agent_chat(agent_slug: str, messages: list[dict]) -> dict[str, Any]:
    agent = _get_agent(agent_slug)
    client = get_client()

    # OpenAI takes system as first message in the list
    msg_list: list[dict] = [{"role": "system", "content": agent.get_system_prompt()}] + list(messages)
    tools = agent.get_tools()
    last_tool_used: str | None = None

    while True:
        logger.info(
            "LLM REQUEST | model=gpt-4o | messages=%d\n%s",
            len(msg_list),
            json.dumps(msg_list, default=str, indent=2),
        )

        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=msg_list,
            tools=tools,
        )

        usage = response.usage
        logger.info(
            "LLM RESPONSE | prompt_tokens=%s | completion_tokens=%s | total_tokens=%s\n%s",
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
            usage.total_tokens if usage else "?",
            json.dumps(response.choices[0].message.model_dump(), default=str, indent=2),
        )

        choice = response.choices[0]

        if choice.finish_reason == "tool_calls":
            tool_calls = choice.message.tool_calls
            msg_list.append(choice.message)
            for tc in tool_calls:
                last_tool_used = tc.function.name
                try:
                    result = await agent.execute_tool(tc.function.name, json.loads(tc.function.arguments))
                except Exception as exc:
                    logger.exception("Tool %s failed", tc.function.name)
                    result = {"error": str(exc)}
                msg_list.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })
        else:
            return {"answer": choice.message.content or "", "tool_used": last_tool_used}
