"""
Chat router — WebSocket streaming + REST history endpoints.

Wire protocol (unchanged from the in-Orbit version, so Orbit's widget works as is):

  Client sends: {"message": "...", "conversation_id": "..." | null}
  Server sends:
    - {"type": "text_delta", "text": "..."}
    - {"type": "tool_call", "name": "...", "input": {...}}
    - {"type": "tool_result", "name": "...", "result": {...}}
    - {"type": "done", "conversation_id": "...", "message_id": "..."}
    - {"type": "error", "error": "..."}

Provider priority per message: local LLM > Gemini > demo (no keys) > Anthropic.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..auth import NoUserError, resolve_user_id
from ..config import get_settings
from ..db import copilot_session, orbit_session
from ..models.chat import Conversation, Message, MessageRole
from ..providers.anthropic_provider import stream_chat_response
from ..providers.demo_provider import handle_demo_message, is_demo_mode
from ..providers.gemini_provider import gemini_chat_response, has_gemini_key
from ..providers.local_provider import local_chat_response
from ..tools import execute_tool

log = logging.getLogger("orbit_copilot.chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

HISTORY_LIMIT = 20  # messages of prior context sent to the model


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def _user_or_401(authorization: Optional[str]) -> str:
    try:
        return await resolve_user_id(_bearer(authorization))
    except NoUserError as e:
        raise HTTPException(status_code=401, detail=str(e))


@router.get("/history")
async def get_chat_history(authorization: Optional[str] = Header(default=None)):
    """All of the user's conversations with their messages, newest first."""
    user_id = await _user_or_401(authorization)
    async with copilot_session() as db:
        result = await db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .options(selectinload(Conversation.messages))
            .order_by(Conversation.updated_at.desc())
        )
        conversations = result.scalars().all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "createdAt": c.created_at.isoformat(),
            "messages": [m.to_dict() for m in c.messages],
        }
        for c in conversations
    ]


@router.get("/history/{conversation_id}")
async def get_conversation(conversation_id: str, authorization: Optional[str] = Header(default=None)):
    """One conversation with its messages."""
    user_id = await _user_or_401(authorization)
    async with copilot_session() as db:
        result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id)
            .where(Conversation.user_id == user_id)
        )
        conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "id": conv.id,
        "title": conv.title,
        "createdAt": conv.created_at.isoformat(),
        "messages": [m.to_dict() for m in conv.messages],
    }


async def _replay(websocket: WebSocket, response_text: str, tool_calls_log: list[dict]) -> str:
    """Send a finished (non-streamed) provider answer: tool calls first, then text word by word."""
    for tc in tool_calls_log:
        await websocket.send_json({"type": "tool_call", "name": tc["name"], "input": tc["input"]})
        await websocket.send_json({"type": "tool_result", "name": tc["name"], "result": tc["result"]})

    full_text = ""
    for i, word in enumerate(response_text.split(" ")):
        chunk = word if i == 0 else " " + word
        full_text += chunk
        await websocket.send_json({"type": "text_delta", "text": chunk})
    return full_text


async def _anthropic_agent_loop(websocket: WebSocket, api_messages: list[dict], user_id: str,
                                tool_calls_log: list[dict]) -> str:
    """Stream from Claude, run any requested tools, feed results back, repeat until no tools."""
    full_text = ""
    while True:
        collected_text = ""
        tool_uses = []

        async for event in stream_chat_response(api_messages):
            if event["type"] == "text_delta":
                collected_text += event["text"]
                await websocket.send_json(event)
            elif event["type"] == "tool_use":
                tool_uses.append(event)
                await websocket.send_json({"type": "tool_call", "name": event["name"], "input": event["input"]})

        full_text += collected_text
        if not tool_uses:
            return full_text

        assistant_content = []
        if collected_text:
            assistant_content.append({"type": "text", "text": collected_text})
        for tu in tool_uses:
            assistant_content.append({"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu["input"]})
        api_messages.append({"role": "assistant", "content": assistant_content})

        tool_results_content = []
        async with orbit_session() as db:
            for tu in tool_uses:
                result_str = await execute_tool(tu["name"], tu["input"], db, user_id)
                tool_calls_log.append({"name": tu["name"], "input": tu["input"], "result": json.loads(result_str)})
                tool_results_content.append({"type": "tool_result", "tool_use_id": tu["id"], "content": result_str})

        for tc in tool_calls_log[-len(tool_uses):]:
            await websocket.send_json({"type": "tool_result", "name": tc["name"], "result": tc["result"]})

        api_messages.append({"role": "user", "content": tool_results_content})


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, token: Optional[str] = None):
    await websocket.accept()
    settings = get_settings()

    try:
        user_id = await resolve_user_id(token)
    except NoUserError as e:
        await websocket.send_json({"type": "error", "error": str(e)})
        await websocket.close(code=4401)
        return

    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("message", "")
            conversation_id = data.get("conversation_id")

            if not user_message.strip():
                await websocket.send_json({"type": "error", "error": "Empty message"})
                continue

            try:
                # ── 1. Find or create the conversation, load history, save the user turn ──
                async with copilot_session() as db:
                    conv_id = None
                    if conversation_id:
                        result = await db.execute(
                            select(Conversation.id)
                            .where(Conversation.id == conversation_id)
                            .where(Conversation.user_id == user_id)
                        )
                        conv_id = result.scalar_one_or_none()

                    if not conv_id:
                        new_conv = Conversation(user_id=user_id)
                        db.add(new_conv)
                        await db.flush()
                        conv_id = new_conv.id

                    msg_result = await db.execute(
                        select(Message.role, Message.content)
                        .where(Message.conversation_id == conv_id)
                        .order_by(Message.created_at.desc())
                        .limit(HISTORY_LIMIT)
                    )
                    history_messages = []
                    for role, content in reversed(msg_result.all()):
                        role_str = role.value if hasattr(role, "value") else role
                        if role_str in ("user", "assistant"):
                            history_messages.append({"role": role_str, "content": content})

                    db.add(Message(conversation_id=conv_id, role=MessageRole.user, content=user_message))
                    await db.commit()

                # ── 2. Generate the answer ──
                tool_calls_log: list[dict] = []
                demo_mode = is_demo_mode(settings.anthropic_api_key)

                if settings.use_local_llm:
                    async with orbit_session() as db:
                        text, tool_calls_log = await local_chat_response(user_message, db, user_id, history=history_messages)
                    full_text = await _replay(websocket, text, tool_calls_log)
                elif demo_mode and has_gemini_key():
                    async with orbit_session() as db:
                        text, tool_calls_log = await gemini_chat_response(user_message, db, user_id, history=history_messages)
                    full_text = await _replay(websocket, text, tool_calls_log)
                elif demo_mode:
                    async with orbit_session() as db:
                        text, tool_calls_log = await handle_demo_message(user_message, db, user_id)
                    full_text = await _replay(websocket, text, tool_calls_log)
                else:
                    api_messages = history_messages + [{"role": "user", "content": user_message}]
                    full_text = await _anthropic_agent_loop(websocket, api_messages, user_id, tool_calls_log)

                # ── 3. Save the assistant turn and auto-title the conversation ──
                async with copilot_session() as db:
                    assistant_msg = Message(
                        conversation_id=conv_id,
                        role=MessageRole.assistant,
                        content=full_text,
                        tool_calls=json.dumps(tool_calls_log, default=str) if tool_calls_log else None,
                    )
                    db.add(assistant_msg)
                    conv = (await db.execute(select(Conversation).where(Conversation.id == conv_id))).scalar_one()
                    if conv.title == "New Chat" and full_text:
                        conv.title = user_message[:60] + ("..." if len(user_message) > 60 else "")
                    await db.commit()
                    msg_id = assistant_msg.id

                await websocket.send_json({"type": "done", "conversation_id": conv_id, "message_id": msg_id})

            except Exception as e:  # keep the socket alive for the next message
                log.exception("chat turn failed")
                await websocket.send_json({"type": "error", "error": str(e)})

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
