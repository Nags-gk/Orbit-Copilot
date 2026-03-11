"""
Chat router — WebSocket streaming + REST history endpoint.
"""
from __future__ import annotations
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from jose import jwt, JWTError

from ..database import get_db, async_session
from ..models import Conversation, Message, MessageRole, User
from ..services.security import get_current_user, SECRET_KEY, ALGORITHM
from ..services.llm import stream_chat_response, non_streaming_chat, should_escalate_to_opus
from ..services.tools import execute_tool
from ..services.demo import is_demo_mode, handle_demo_message
from ..services.gemini_llm import gemini_chat_response, has_gemini_key
from ..config import get_settings

router = APIRouter(prefix="/api/chat", tags=["chat"])
settings = get_settings()


@router.get("/history")
async def get_chat_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all conversations with their messages."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
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
async def get_conversation(
    conversation_id: str, 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get a single conversation with messages."""
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == current_user.id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        return {"error": "Conversation not found"}
    return {
        "id": conv.id,
        "title": conv.title,
        "createdAt": conv.created_at.isoformat(),
        "messages": [m.to_dict() for m in conv.messages],
    }


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, token: str):
    """
    WebSocket endpoint for real-time chat streaming.
    
    Client sends: {"message": "...", "conversation_id": "..." | null}
    Server sends:
      - {"type": "text_delta", "text": "..."}
      - {"type": "tool_call", "name": "...", "input": {...}}
      - {"type": "tool_result", "name": "...", "result": {...}}
      - {"type": "done", "conversation_id": "...", "message_id": "..."}
      - {"type": "error", "error": "..."}
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=1008)
            return
    except JWTError:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            user_message = data.get("message", "")
            conversation_id = data.get("conversation_id")

            if not user_message.strip():
                await websocket.send_json({"type": "error", "error": "Empty message"})
                continue

            try:
                # ── Step 1: Save user message to DB (isolated DB block) ──
                async with async_session() as db:
                    # Find or create conversation (NO relationship loading)
                    conv_id = None
                    if conversation_id:
                        result = await db.execute(
                            select(Conversation.id, Conversation.title)
                            .where(Conversation.id == conversation_id)
                            .where(Conversation.user_id == user_id)
                        )
                        row = result.first()
                        if row:
                            conv_id = row[0]

                    if not conv_id:
                        new_conv = Conversation(user_id=user_id)
                        db.add(new_conv)
                        await db.flush()
                        conv_id = new_conv.id

                    # Load message history directly (avoid ORM relationship)
                    msg_result = await db.execute(
                        select(Message.role, Message.content)
                        .where(Message.conversation_id == conv_id)
                        .order_by(Message.created_at.desc())
                        .limit(20)
                    )
                    raw_messages = msg_result.all()

                    history_messages = []
                    for role, content in reversed(raw_messages):
                        role_str = role.value if hasattr(role, 'value') else role
                        if role_str in ("user", "assistant"):
                            history_messages.append({"role": role_str, "content": content})

                    # Save user message
                    user_msg = Message(
                        conversation_id=conv_id,
                        role=MessageRole.user,
                        content=user_message,
                    )
                    db.add(user_msg)
                    await db.commit()

                # Build API messages from plain data (no ORM access needed)
                api_messages = history_messages + [{"role": "user", "content": user_message}]

                # ── Step 2: Generate response (Anthropic > Gemini > Demo) ──
                _demo_mode = is_demo_mode(settings.anthropic_api_key)
                _has_gemini = has_gemini_key()
                full_text = ""
                tool_calls_log = []

                if _demo_mode and _has_gemini:
                    # Gemini LLM mode: real AI with function calling
                    async with async_session() as db:
                        response_text, tool_calls_log = await gemini_chat_response(
                            user_message, db, user_id, history=history_messages
                        )

                    # Send tool calls to frontend
                    for tc in tool_calls_log:
                        await websocket.send_json({
                            "type": "tool_call",
                            "name": tc["name"],
                            "input": tc["input"],
                        })
                        await websocket.send_json({
                            "type": "tool_result",
                            "name": tc["name"],
                            "result": tc["result"],
                        })

                    # Stream the response word by word
                    words = response_text.split(' ')
                    for i, word in enumerate(words):
                        chunk = word if i == 0 else ' ' + word
                        full_text += chunk
                        await websocket.send_json({"type": "text_delta", "text": chunk})

                elif _demo_mode:
                    # Fallback demo mode: intent detection + tool execution
                    async with async_session() as db:
                        response_text, tool_calls_log = await handle_demo_message(user_message, db, user_id)

                    # Send tool calls to frontend (no DB in scope)
                    for tc in tool_calls_log:
                        await websocket.send_json({
                            "type": "tool_call",
                            "name": tc["name"],
                            "input": tc["input"],
                        })
                        await websocket.send_json({
                            "type": "tool_result",
                            "name": tc["name"],
                            "result": tc["result"],
                        })

                    # Stream the formatted response word by word
                    words = response_text.split(' ')
                    for i, word in enumerate(words):
                        chunk = word if i == 0 else ' ' + word
                        full_text += chunk
                        await websocket.send_json({"type": "text_delta", "text": chunk})

                else:
                    # Live LLM mode — agentic loop
                    while True:
                        collected_text = ""
                        tool_uses = []

                        async for event in stream_chat_response(api_messages):
                            if event["type"] == "text_delta":
                                collected_text += event["text"]
                                await websocket.send_json(event)
                            elif event["type"] == "tool_use":
                                tool_uses.append(event)
                                await websocket.send_json({
                                    "type": "tool_call",
                                    "name": event["name"],
                                    "input": event["input"],
                                })
                            elif event["type"] == "message_stop":
                                pass

                        full_text += collected_text

                        if not tool_uses:
                            break

                        # Build assistant content for API history
                        assistant_content = []
                        if collected_text:
                            assistant_content.append({"type": "text", "text": collected_text})
                        for tu in tool_uses:
                            assistant_content.append({
                                "type": "tool_use",
                                "id": tu["id"],
                                "name": tu["name"],
                                "input": tu["input"],
                            })
                        api_messages.append({"role": "assistant", "content": assistant_content})

                        # Execute tools in isolated DB block
                        tool_results_content = []
                        async with async_session() as db:
                            for tu in tool_uses:
                                result_str = await execute_tool(tu["name"], tu["input"], db, user_id)
                                tool_calls_log.append({
                                    "name": tu["name"],
                                    "input": tu["input"],
                                    "result": json.loads(result_str),
                                })
                                tool_results_content.append({
                                    "type": "tool_result",
                                    "tool_use_id": tu["id"],
                                    "content": result_str,
                                })

                        # Send tool results (no DB in scope)
                        for tc in tool_calls_log[-len(tool_uses):]:
                            await websocket.send_json({
                                "type": "tool_result",
                                "name": tc["name"],
                                "result": tc["result"],
                            })

                        api_messages.append({"role": "user", "content": tool_results_content})
                        # Loop continues — LLM will respond to tool results

                # ── Step 3: Save assistant response to DB (isolated DB block) ──
                async with async_session() as db:
                    assistant_msg = Message(
                        conversation_id=conv_id,
                        role=MessageRole.assistant,
                        content=full_text,
                        tool_calls=json.dumps(tool_calls_log) if tool_calls_log else None,
                    )
                    db.add(assistant_msg)

                    # Auto-title conversation
                    result = await db.execute(
                        select(Conversation).where(Conversation.id == conv_id)
                    )
                    conv = result.scalar_one()
                    if conv.title == "New Chat" and full_text:
                        conv.title = user_message[:60] + ("..." if len(user_message) > 60 else "")

                    await db.commit()
                    msg_id = assistant_msg.id

                await websocket.send_json({
                    "type": "done",
                    "conversation_id": conv_id,
                    "message_id": msg_id,
                })

            except Exception as e:
                import traceback
                traceback.print_exc()
                await websocket.send_json({"type": "error", "error": str(e)})

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


def _build_api_messages(conversation: Conversation, new_user_message: str) -> list[dict]:
    """Build the messages array for the Anthropic API from conversation history."""
    messages = []

    # Include recent conversation history (last 20 messages to stay within context)
    if conversation.messages:
        recent = list(conversation.messages)[-20:]
        for msg in recent:
            if msg.role == MessageRole.user:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == MessageRole.assistant:
                messages.append({"role": "assistant", "content": msg.content})

    # Add the new user message
    messages.append({"role": "user", "content": new_user_message})

    return messages
