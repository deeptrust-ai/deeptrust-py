"""Adapter tests, with the platform SDKs faked.

The point of these is the wiring, not the platform: analysis runs on caller
turns only, and a nudge is delivered exactly once per finding that carries one.
"""

from __future__ import annotations

from typing import Any

import httpx
import respx

from deeptrust.agents import DeepTrust
from deeptrust.agents.elevenlabs import Monitor, _read_turn, contextual_update_command
from deeptrust.agents.livekit import attach

BASE = "https://example.test/api/v1"

ONE_NUDGE = {
    "session_id": "sess_1",
    "job_id": "job_1",
    "findings": [
        {
            "kind": "coercion",
            "detail": "third party instructing",
            "nudge": {
                "title": "Someone else may be coaching the caller",
                "description": "The caller referred to someone else on the line.",
                "details": "Ask one question and wait: is anyone helping them right now?",
            },
        }
    ],
}


class FakeAgent:
    def __init__(self) -> None:
        self.chat_ctx = FakeChat()
        self.updated: list[Any] = []

    async def update_chat_ctx(self, chat: Any) -> None:
        self.updated.append(chat)


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def copy(self) -> FakeChat:
        c = FakeChat()
        c.messages = list(self.messages)
        return c

    def add_message(self, role: str, content: str) -> None:
        self.messages.append((role, content))


class FakeSession:
    """Stands in for a LiveKit AgentSession."""

    def __init__(self) -> None:
        self.current_agent = FakeAgent()
        self.handlers: dict[str, Any] = {}
        self.interrupted = 0
        self.replies: list[str] = []

    def on(self, event: str) -> Any:
        def deco(fn: Any) -> Any:
            self.handlers[event] = fn
            return fn

        return deco

    def interrupt(self) -> None:
        self.interrupted += 1

    def generate_reply(self, instructions: str) -> None:
        self.replies.append(instructions)

    async def say(self, role: str, text: str) -> None:
        item = type("Item", (), {"text_content": text, "role": role})()
        ev = type("Ev", (), {"item": item})()
        self.handlers["conversation_item_added"](ev)


@respx.mock
async def test_livekit_analyses_caller_turns_and_delivers_once() -> None:
    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    lk = FakeSession()
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    call = attach(lk, dt, external_id="room-1")

    await lk.say("user", "my colleague is telling me what to say")
    # The handler spawns a task; let it run.
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0.05)

    assert route.call_count == 1
    assert len(call.transcript) == 1
    assert lk.interrupted == 1
    assert len(lk.replies) == 1
    assert "Ask one question" in lk.replies[0]
    # The nudge went into the agent's context as well as being spoken.
    assert lk.current_agent.updated


@respx.mock
async def test_livekit_does_not_analyse_the_agents_own_turn() -> None:
    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    lk = FakeSession()
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    call = attach(lk, dt, external_id="room-1")

    await lk.say("assistant", "I need to confirm it is you first")
    import asyncio

    await asyncio.sleep(0.05)

    assert route.call_count == 0
    # It is still in the transcript. It is just not a reason to run a job.
    assert len(call.transcript) == 1
    assert call.transcript.turns[0].role == "agent"


def test_elevenlabs_event_reader() -> None:
    assert _read_turn(
        {
            "type": "user_transcript",
            "user_transcription_event": {"user_transcript": "reset my password"},
        }
    ) == ("user", "reset my password")

    assert _read_turn(
        {
            "type": "agent_response",
            "agent_response_event": {"agent_response": "sending a code now"},
        }
    ) == ("agent", "sending a code now")

    # Anything else is not a turn, and must not become one.
    assert _read_turn({"type": "audio"}) == ("", "")
    assert _read_turn({"type": "interruption"}) == ("", "")
    assert _read_turn({}) == ("", "")


def test_elevenlabs_contextual_update_is_a_monitor_command() -> None:
    """The monitor socket takes commands; the `{"type": ...}` shape of the
    main socket is silently ignored there, which is how 0.0.1 delivered
    nothing."""
    assert contextual_update_command("hold the line") == {
        "command_type": "contextual_update",
        "parameters": {"contextual_update": "hold the line"},
    }


class FakeMonitorSocket:
    """A monitor socket that replays scripted events and records sends."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events
        self.sent: list[dict[str, Any]] = []
        self.url: str | None = None
        self.headers: dict[str, str] | None = None

    def __call__(
        self, url: str, *, additional_headers: dict[str, str]
    ) -> FakeMonitorSocket:
        self.url = url
        self.headers = additional_headers
        return self

    async def __aenter__(self) -> FakeMonitorSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> FakeMonitorSocket:
        return self

    async def __anext__(self) -> str:
        import json

        if not self._events:
            raise StopAsyncIteration
        return json.dumps(self._events.pop(0))

    async def send(self, raw: str) -> None:
        import json

        self.sent.append(json.loads(raw))


@respx.mock
async def test_elevenlabs_monitor_sends_nudges_in_the_command_envelope() -> None:
    import asyncio

    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    socket = FakeMonitorSocket(
        [
            {
                "type": "agent_response",
                "agent_response_event": {"agent_response": "IT desk, how can I help?"},
            },
            {
                "type": "user_transcript",
                "user_transcription_event": {
                    "user_transcript": "my colleague is telling me what to say"
                },
            },
            {"type": "audio"},
        ]
    )
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    monitor = Monitor(dt, api_key="xi_test", connect=socket)

    await monitor.watch("conv_1")
    for _ in range(20):
        await asyncio.sleep(0.01)
        if "conv_1" not in monitor._watching:
            break

    assert socket.url == "wss://api.elevenlabs.io/v1/convai/conversations/conv_1/monitor"
    assert socket.headers == {"xi-api-key": "xi_test"}
    # One job, for the one caller turn; the agent turn and the audio frame cost nothing.
    assert route.call_count == 1
    assert socket.sent == [
        {
            "command_type": "contextual_update",
            "parameters": {
                "contextual_update": (
                    "The caller referred to someone else on the line. "
                    "Ask one question and wait: is anyone helping them right now?"
                )
            },
        }
    ]
