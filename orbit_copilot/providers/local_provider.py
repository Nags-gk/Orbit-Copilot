"""
Local LLM chat backend for Orbit AI.

When configured, this routes chat through a local OpenAI-compatible
inference engine like Ollama or LM Studio.
"""
from __future__ import annotations
import json
import os
from typing import Optional
from .tools import TOOL_SCHEMAS, execute_tool
from .gemini_llm import GEMINI_SYSTEM_PROMPT as SYSTEM_PROMPT
from ..config import get_settings

settings = get_settings()

def _anthropic_to_openai_tools() -> list[dict]:
    """Convert Anthropic tool schemas into OpenAI/Ollama function definitions."""
    tools = []
    for tool in TOOL_SCHEMAS:
        schema = tool["input_schema"].copy()
        tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": schema,
            }
        })
    return tools


async def local_chat_response(
    user_message: str,
    db,
    user_id: str,
    history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """
    Generate a chat response using a Local LLM via the OpenAI compatible SDK.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return "⚠️ OpenAI package not installed. Run: pip install openai", []

    # Connect to the local model (e.g. Ollama via http://localhost:11434/v1)
    client = AsyncOpenAI(
        base_url=settings.local_model_url,
        api_key="local-no-key-required"
    )
    
    # Build conversation content
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    
    if history:
        for msg in history[-10:]:
            # Ensure roles align with what OpenAI expects (user/assistant)
            role = "user" if msg.get("role") == "user" else "assistant"
            # OpenAI requires string content
            content = msg.get("content", "")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                
    messages.append({"role": "user", "content": user_message})

    openai_tools = _anthropic_to_openai_tools()
    tool_calls_log = []
    max_tool_rounds = 5

    for _round in range(max_tool_rounds):
        try:
            # We explicitly check if tools are available to pass to the model
            # Some smaller local models don't support tools natively, so we pass
            # them but fallback gracefully.
            kwargs = {
                "model": settings.local_model_name,
                "messages": messages,
                "temperature": 0.4,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            response = await client.chat.completions.create(**kwargs)
            
        except Exception as e:
            return f"⚠️ Local AI connection error: {str(e)}. Please ensure your local engine (e.g. Ollama) is running at {settings.local_model_url}", []

        choice = response.choices[0].message

        # Check for function calls (tools)
        if choice.tool_calls:
            # Append the assistant's tool-call request to messages so the model keeps context
            messages.append(choice)

            for tool_call in choice.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_args = json.loads(tool_call.function.arguments)
                except Exception:
                    tool_args = {}

                # Execute our internal app tools
                try:
                    result_str = await execute_tool(tool_name, tool_args, db, user_id)
                    result_data = json.loads(result_str)
                except Exception as e:
                    result_data = {"error": str(e)}

                tool_calls_log.append({
                    "name": tool_name,
                    "input": tool_args,
                    "result": result_data,
                })

                # Append the result from the tool execution
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_name,
                    "content": json.dumps(result_data)
                })
            
            # Continue the loop so the model can read the tool results and answer
            continue

        else:
            # No tool calls made, just return the text response
            return choice.content or "I'm not sure how to help with that.", tool_calls_log

    return "I've gathered the information but couldn't process it locally within limits. Please try again.", tool_calls_log
