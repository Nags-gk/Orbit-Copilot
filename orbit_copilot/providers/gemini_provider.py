"""
Gemini LLM chat backend for Orbit AI.

When no Anthropic API key is configured but GEMINI_API_KEY is present,
this module provides intelligent chat responses using Google Gemini 2.5 Flash
with full tool-use capabilities — no more naive keyword matching.
"""
from __future__ import annotations
import json
import os
from typing import Optional
from .tools import TOOL_SCHEMAS, execute_tool
from .categorizer import auto_categorize


# ── Convert Anthropic tool schemas to Gemini function declarations ──

def _anthropic_to_gemini_tools() -> list[dict]:
    """Convert Anthropic-style tool schemas into Gemini function declarations."""
    declarations = []
    for tool in TOOL_SCHEMAS:
        schema = tool["input_schema"].copy()
        # Gemini doesn't use 'required' at the top level of properties the same way
        # but we keep it for clarity
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": schema,
        })
    return declarations


GEMINI_SYSTEM_PROMPT = """You are **Orbit AI**, an intelligent financial assistant embedded in the Orbit personal finance dashboard.

## Your Capabilities
You help users understand and manage their finances by:
- Querying their transaction history with filters (category, date range, type)
- Adding new transactions — with **intelligent auto-categorization** (if no category specified, set it to "Auto")
- Managing subscriptions
- Providing spending summaries and category breakdowns
- **Managing financial accounts** — creating, listing, and updating accounts
- **Answering net worth and investment questions**
- **Auto-categorizing transactions** — when a user asks "what category is X?", use the auto_categorize_transaction tool
- Searching uploaded financial documents

## CRITICAL Rules
1. **Always use tools** to fetch financial data. Never make up numbers.
2. **When asked "what category is [something]?"** — use the `auto_categorize_transaction` tool with the item name. Do NOT use get_category_breakdown.
3. **Be concise** — use bullet points, short paragraphs.
4. **Format currency** as `$X,XXX.XX`.
5. **Auto-categorize** — When creating transactions without a category, set category to "Auto".
6. **Account management** — Use create_account, get_accounts, get_net_worth tools.

## Personality
- Friendly, professional financial advisor
- Use occasional emoji for warmth (💰, 📊, ✅)
- Acknowledge limitations and suggest alternatives
"""


async def gemini_chat_response(
    user_message: str,
    db,
    user_id: str,
    history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """
    Generate a chat response using Gemini 2.5 Flash with function calling.
    
    Returns:
        (response_text, tool_calls_log)
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "⚠️ No AI API key configured. Please set GEMINI_API_KEY or ANTHROPIC_API_KEY in your backend/.env file.", []
    
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "⚠️ google-genai package not installed. Run: pip install google-genai", []
    
    client = genai.Client(api_key=api_key)
    
    # Build the conversation contents
    contents = []
    if history:
        for msg in history[-10:]:  # Keep last 10 messages for context
            role = "user" if msg.get("role") == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg.get("content", ""))]
            ))
    
    # Add the new user message
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)]
    ))
    
    # Build Gemini function declarations from our tool schemas
    function_declarations = []
    for tool in TOOL_SCHEMAS:
        params = tool["input_schema"].copy()
        function_declarations.append(types.FunctionDeclaration(
            name=tool["name"],
            description=tool["description"],
            parameters=params,
        ))
    
    gemini_tools = types.Tool(function_declarations=function_declarations)
    
    tool_calls_log = []
    max_tool_rounds = 5
    
    for _round in range(max_tool_rounds):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=GEMINI_SYSTEM_PROMPT,
                    tools=[gemini_tools],
                    temperature=0.4,
                ),
            )
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                return "⚠️ Gemini API rate limit reached. Please wait a moment and try again.", []
            if "API_KEY_INVALID" in error_msg or "expired" in error_msg:
                return "⚠️ Gemini API key is invalid or expired. Please update GEMINI_API_KEY in backend/.env.", []
            return f"⚠️ AI error: {error_msg}", []
        
        if not response.candidates or not response.candidates[0].content:
            return "I'm sorry, I couldn't generate a response. Please try again.", []
        
        response_parts = response.candidates[0].content.parts
        
        # Check for function calls
        function_calls = [p for p in response_parts if p.function_call]
        text_parts = [p.text for p in response_parts if hasattr(p, 'text') and p.text]
        
        if not function_calls:
            # No function calls — return the text response
            return " ".join(text_parts) if text_parts else "I'm not sure how to help with that.", tool_calls_log
        
        # Execute each function call
        function_responses = []
        for fc in function_calls:
            tool_name = fc.function_call.name
            tool_args = dict(fc.function_call.args) if fc.function_call.args else {}
            
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
            
            function_responses.append(types.Part.from_function_response(
                name=tool_name,
                response=result_data,
            ))
        
        # Add the model's response and function results to the conversation
        contents.append(response.candidates[0].content)
        contents.append(types.Content(
            role="user",
            parts=function_responses,
        ))
        # Loop to let the model process the function results
    
    # If we exhausted all rounds, return whatever text we have
    return "I've gathered the information but couldn't fully process it. Please try again.", tool_calls_log


def has_gemini_key() -> bool:
    """Check if a Gemini API key is configured."""
    key = os.getenv("GEMINI_API_KEY", "")
    return bool(key and len(key) > 10)
