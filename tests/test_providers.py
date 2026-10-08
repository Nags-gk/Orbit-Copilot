"""Provider behavior with the network mocked out: demo, local (OpenAI-compatible) and schema conversion."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from orbit_copilot.providers import local_provider
from orbit_copilot.providers.anthropic_provider import should_escalate_to_opus
from orbit_copilot.providers.demo_provider import detect_intent, handle_demo_message, is_demo_mode
from orbit_copilot.providers.gemini_provider import _anthropic_to_gemini_tools
from orbit_copilot.tools import TOOL_SCHEMAS

from .conftest import ALICE


@pytest.mark.parametrize("key, demo", [("", True), ("placeholder", True), ("sk-ant-" + "x" * 30, False)])
def test_is_demo_mode(key, demo):
    assert is_demo_mode(key) is demo


@pytest.mark.parametrize("message, intent", [
    ("what's my net worth?", "get_net_worth"),
    ("cancel my netflix subscription", "cancel_subscription"),
    ("add my Schwab brokerage with $15,000", "create_account"),
    ("any unusual spending?", "anomalies"),
])
def test_demo_intent_detection(message, intent):
    assert detect_intent(message)["intent"] == intent


@pytest.mark.asyncio
async def test_demo_net_worth_answer(db):
    text, calls = await handle_demo_message("what's my net worth?", db, ALICE)
    assert "18,800" in text  # 5000 + 15000 - 1200
    assert calls and calls[0]["name"] == "get_net_worth"


@pytest.mark.asyncio
async def test_demo_help_when_no_intent(db):
    text, calls = await handle_demo_message("hello there", db, ALICE)
    assert "demo mode" in text and calls == []


def test_tool_schemas_convert_for_every_provider():
    gemini = _anthropic_to_gemini_tools()
    openai = local_provider._anthropic_to_openai_tools()
    assert len(gemini) == len(openai) == len(TOOL_SCHEMAS) == 12
    assert openai[0]["type"] == "function" and openai[0]["function"]["parameters"]["type"] == "object"


def test_opus_escalation_signal_detection():
    assert should_escalate_to_opus("needs_opus: true")
    assert not should_escalate_to_opus("Your balance is $5,000.")


class _FakeCompletions:
    """Two rounds: first asks for get_net_worth, then answers using the result."""

    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            tool_call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="get_net_worth", arguments="{}"))
            msg = SimpleNamespace(content=None, tool_calls=[tool_call], role="assistant")
        else:
            tool_msg = kwargs["messages"][-1]
            net = json.loads(tool_msg["content"])["netWorth"]
            msg = SimpleNamespace(content=f"Your net worth is ${net:,.2f}.", tool_calls=None, role="assistant")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.mark.asyncio
async def test_local_provider_runs_tool_loop(monkeypatch, db):
    fake = _FakeCompletions()

    class FakeClient:
        def __init__(self, **_):
            self.chat = SimpleNamespace(completions=fake)

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)

    text, calls = await local_provider.local_chat_response("net worth?", db, ALICE, history=[])
    assert text == "Your net worth is $18,800.00."
    assert [c["name"] for c in calls] == ["get_net_worth"]
    assert len(fake.calls) == 2 and len(fake.calls[0]["tools"]) == 12
