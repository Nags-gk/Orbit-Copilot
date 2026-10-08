"""End-to-end over HTTP and WebSocket, plus identity resolution."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from orbit_copilot import auth
from orbit_copilot.api import chat as chat_api
from orbit_copilot.config import get_settings
from orbit_copilot.main import app

from .conftest import ALICE, BOB


def token_for(user_id: str, secret: str = "test-secret") -> str:
    return jwt.encode({"sub": user_id, "exp": datetime.utcnow() + timedelta(hours=1)}, secret, algorithm="HS256")


def collect_turn(ws) -> list[dict]:
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in ("done", "error"):
            return events


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("token, expected", [
    (None, ALICE),                                   # local mode → oldest user
    ("local-bypass-token", ALICE),                   # Orbit's placeholder token is not a JWT
    (token_for(BOB), BOB),                           # real Orbit JWT
    (token_for(BOB, secret="wrong"), ALICE),         # bad signature is ignored
])
async def test_resolve_user(token, expected):
    assert await auth.resolve_user_id(token) == expected


@pytest.mark.asyncio
async def test_local_user_email_overrides_oldest(monkeypatch):
    monkeypatch.setenv("LOCAL_USER_EMAIL", "bob@example.com")
    get_settings.cache_clear()
    assert await auth.resolve_user_id(None) == BOB
    monkeypatch.setenv("LOCAL_USER_EMAIL", "nobody@example.com")  # unknown → oldest
    get_settings.cache_clear()
    assert await auth.resolve_user_id(None) == ALICE


@pytest.mark.asyncio
async def test_resolve_user_refuses_without_local_mode(monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_USER", "false")
    get_settings.cache_clear()
    with pytest.raises(auth.NoUserError):
        await auth.resolve_user_id(None)
    assert await auth.resolve_user_id(token_for(BOB)) == BOB


def test_websocket_demo_turn_and_history(client):
    with client.websocket_connect("/api/chat/ws") as ws:
        ws.send_json({"message": "What's my net worth?", "conversation_id": None})
        events = collect_turn(ws)

    kinds = [e["type"] for e in events]
    assert kinds[:2] == ["tool_call", "tool_result"] and kinds[-1] == "done"
    text = "".join(e["text"] for e in events if e["type"] == "text_delta")
    assert "18,800" in text
    conv_id = events[-1]["conversation_id"]

    history = client.get("/api/chat/history").json()
    assert [c["id"] for c in history] == [conv_id]
    assert history[0]["title"] == "What's my net worth?"
    assert [m["role"] for m in history[0]["messages"]] == ["user", "assistant"]

    one = client.get(f"/api/chat/history/{conv_id}")
    assert one.status_code == 200 and len(one.json()["messages"]) == 2


def test_conversations_are_per_user(client):
    with client.websocket_connect(f"/api/chat/ws?token={token_for(BOB)}") as ws:
        ws.send_json({"message": "show my accounts", "conversation_id": None})
        bob_conv = collect_turn(ws)[-1]["conversation_id"]

    # Alice (local default) can't see or read Bob's conversation.
    assert client.get("/api/chat/history").json() == []
    assert client.get(f"/api/chat/history/{bob_conv}").status_code == 404
    bob_headers = {"Authorization": f"Bearer {token_for(BOB)}"}
    assert [c["id"] for c in client.get("/api/chat/history", headers=bob_headers).json()] == [bob_conv]


def test_follow_up_reuses_conversation_and_empty_message_errors(client):
    with client.websocket_connect("/api/chat/ws") as ws:
        ws.send_json({"message": "list my subscriptions", "conversation_id": None})
        conv_id = collect_turn(ws)[-1]["conversation_id"]
        ws.send_json({"message": "   ", "conversation_id": conv_id})
        assert ws.receive_json() == {"type": "error", "error": "Empty message"}
        ws.send_json({"message": "show recent transactions", "conversation_id": conv_id})
        assert collect_turn(ws)[-1]["conversation_id"] == conv_id

    msgs = client.get(f"/api/chat/history/{conv_id}").json()["messages"]
    assert len(msgs) == 4


def test_anthropic_agent_loop_runs_tools_until_done(client, monkeypatch):
    """Claude path: a tool_use round, then a final text round, streamed in order."""
    rounds = []

    async def fake_stream(messages, model=None, tools_enabled=True):
        rounds.append([m["role"] for m in messages])
        if len(rounds) == 1:
            yield {"type": "text_delta", "text": "Checking. "}
            yield {"type": "tool_use", "id": "tu_1", "name": "get_net_worth", "input": {}}
        else:
            assert messages[-1]["content"][0]["tool_use_id"] == "tu_1"
            yield {"type": "text_delta", "text": "You're at $18,800."}
        yield {"type": "message_stop", "stop_reason": "end_turn"}

    monkeypatch.setattr(chat_api, "stream_chat_response", fake_stream)
    monkeypatch.setattr(chat_api, "is_demo_mode", lambda key: False)

    with client.websocket_connect("/api/chat/ws") as ws:
        ws.send_json({"message": "net worth?", "conversation_id": None})
        events = collect_turn(ws)

    assert [e["type"] for e in events] == ["text_delta", "tool_call", "tool_result", "text_delta", "done"]
    assert events[2]["result"]["netWorth"] == 18800
    assert rounds == [["user"], ["user", "assistant", "user"]]
