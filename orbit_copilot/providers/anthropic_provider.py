"""
Anthropic LLM client with Sonnet → Opus routing.

Default: Claude Sonnet 4.6 for all requests.
Escalation: Claude Opus 4.6 when the model flags needs_opus=true
            or for pre-defined complex task types.
"""
from __future__ import annotations
import json
from anthropic import AsyncAnthropic
from ..config import get_settings
from ..prompts.system import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS


settings = get_settings()

client = AsyncAnthropic(api_key=settings.anthropic_api_key)


async def stream_chat_response(
    messages: list[dict],
    model: str | None = None,
    tools_enabled: bool = True,
):
    """
    Stream a chat response from Claude.
    
    Yields dicts with:
      - {"type": "text_delta", "text": "..."}
      - {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
      - {"type": "message_stop", "stop_reason": "..."}
    """
    model = model or settings.default_model

    kwargs = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }

    if tools_enabled:
        kwargs["tools"] = TOOL_SCHEMAS

    async with client.messages.stream(**kwargs) as stream:
        current_tool_use = None
        tool_input_json = ""

        async for event in stream:
            if event.type == "content_block_start":
                if hasattr(event.content_block, "type"):
                    if event.content_block.type == "tool_use":
                        current_tool_use = {
                            "id": event.content_block.id,
                            "name": event.content_block.name,
                        }
                        tool_input_json = ""

            elif event.type == "content_block_delta":
                if hasattr(event.delta, "text"):
                    yield {"type": "text_delta", "text": event.delta.text}
                elif hasattr(event.delta, "partial_json"):
                    tool_input_json += event.delta.partial_json

            elif event.type == "content_block_stop":
                if current_tool_use:
                    try:
                        parsed_input = json.loads(tool_input_json) if tool_input_json else {}
                    except json.JSONDecodeError:
                        parsed_input = {}
                    yield {
                        "type": "tool_use",
                        "id": current_tool_use["id"],
                        "name": current_tool_use["name"],
                        "input": parsed_input,
                    }
                    current_tool_use = None
                    tool_input_json = ""

            elif event.type == "message_stop":
                pass

        # Get the final message for stop reason
        final_message = await stream.get_final_message()
        yield {
            "type": "message_stop",
            "stop_reason": final_message.stop_reason,
        }


async def non_streaming_chat(
    messages: list[dict],
    model: str | None = None,
    tools_enabled: bool = True,
) -> dict:
    """
    Non-streaming call — used internally for tool-result follow-ups
    where we need the full response before deciding what to do next.
    """
    model = model or settings.default_model

    kwargs = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": messages,
    }

    if tools_enabled:
        kwargs["tools"] = TOOL_SCHEMAS

    response = await client.messages.create(**kwargs)

    return {
        "content": response.content,
        "stop_reason": response.stop_reason,
        "model": response.model,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }


def should_escalate_to_opus(content: str) -> bool:
    """
    Check if the model's response suggests escalating to Opus.
    The system prompt tells the model to flag complex tasks.
    """
    lower = content.lower()
    escalation_signals = [
        "needs_opus",
        "this requires deeper analysis",
        "complex multi-goal",
        "multi-document reconciliation",
    ]
    return any(signal in lower for signal in escalation_signals)
