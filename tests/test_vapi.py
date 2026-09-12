"""VAPI adapter tests, with VAPI faked.

Webhook payloads go in, and what comes out is the analyze calls and the posts
to the call's control URL. Nothing here touches the network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from deeptrust.agents import DeepTrust
from deeptrust.agents.vapi import Bridge, _read_turn, add_message_command
from deeptrust.errors import ConfigError

BASE = "https://example.test/api/v1"
CONTROL = "https://phone-call-websocket.vapi.ai/call_1/control"
LISTEN = "wss://phone-call-websocket.vapi.ai/call_1/transport"

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

RENDERED = (
    "The caller referred to someone else on the line. "
    "Ask one question and wait: is anyone helping them right now?"
)


def call_object(*, monitor: bool = True) -> dict[str, Any]:
    call: dict[str, Any] = {"id": "call_1", "type": "inboundPhoneCall"}
    if monitor:
        call["monitor"] = {"listenUrl": LISTEN, "controlUrl": CONTROL}
    return call


def transcript(
    role: str, text: str, *, kind: str = "final", monitor: bool = True
) -> dict[str, Any]:
    """A `transcript` server message, in the envelope VAPI posts."""
    return {
        "message": {
            "type": "transcript",
            "role": role,
            "transcriptType": kind,
            "transcript": text,
            "call": call_object(monitor=monitor),
        }
    }


def test_vapi_message_reader() -> None:
    assert _read_turn(transcript("user", "reset my password")["message"]) == (
        "user",
        "reset my password",
    )
    assert _read_turn(transcript("assistant", "sending a code now")["message"]) == (
        "agent",
        "sending a code now",
    )
    # A server URL subscribed to finals only gets the filtered type name.
    assert _read_turn(
        {"type": 'transcript[transcriptType="final"]', "role": "user", "transcript": "hi"}
    ) == ("user", "hi")

    # A partial is the same sentence still being recognised, not a turn.
    assert _read_turn(transcript("user", "reset my", kind="partial")["message"]) == (
        "",
        "",
    )
    # Anything else is not a turn, and must not become one.
    assert _read_turn({"type": "speech-update", "status": "started"}) == ("", "")
    assert _read_turn({"type": "conversation-update", "messages": []}) == ("", "")
    assert _read_turn({}) == ("", "")


def test_vapi_nudge_is_an_add_message_that_triggers_a_response() -> None:
    assert add_message_command("hold the line") == {
        "type": "add-message",
        "message": {"role": "system", "content": "hold the line"},
        "triggerResponseEnabled": True,
    }


def test_vapi_bridge_needs_a_key() -> None:
    with pytest.raises(ConfigError):
        Bridge(DeepTrust(api_key="dt_test", base_url=BASE), api_key="")


@respx.mock
async def test_vapi_analyses_final_caller_turns_and_posts_to_the_control_url() -> None:
    analyze = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    control = respx.post(CONTROL).mock(return_value=httpx.Response(200, json={}))
    lookup = respx.get("https://api.vapi.ai/call/call_1")
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    bridge = Bridge(dt, api_key="vapi_test")

    await bridge.handle(transcript("assistant", "Thanks for calling, how can I help?"))
    await bridge.handle(transcript("user", "my colleague is", kind="partial"))
    await bridge.handle(transcript("user", "my colleague is telling me what to say"))
    await bridge.handle({"message": {"type": "speech-update", "call": call_object()}})

    # One job, for the one final caller turn; the agent turn, the partial and
    # the speech update cost nothing.
    assert analyze.call_count == 1
    call = bridge.session("call_1")
    assert call is not None
    assert call.platform == "vapi"
    assert call.external_id == "call_1"
    assert [t.role for t in call.transcript.turns] == ["agent", "user"]

    assert control.call_count == 1
    assert json.loads(control.calls.last.request.content) == add_message_command(RENDERED)
    # The webhook carried the control URL, so it was never looked up.
    assert lookup.call_count == 0


@respx.mock
async def test_vapi_fetches_the_control_url_once_when_the_webhook_lacks_it() -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    control = respx.post(CONTROL).mock(return_value=httpx.Response(200, json={}))
    lookup = respx.get("https://api.vapi.ai/call/call_1").mock(
        return_value=httpx.Response(200, json=call_object())
    )
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    bridge = Bridge(dt, api_key="vapi_test")

    await bridge.handle(transcript("user", "my colleague is telling me", monitor=False))
    await bridge.handle(transcript("user", "what to say", monitor=False))

    assert lookup.call_count == 1
    assert lookup.calls.last.request.headers["authorization"] == "Bearer vapi_test"
    assert control.call_count == 2


@respx.mock
async def test_vapi_ignores_a_control_url_that_is_not_vapis() -> None:
    """The webhook body is input. A control URL in it is posted to, so one that
    points anywhere but VAPI is dropped and the call's own is fetched."""
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    control = respx.post(CONTROL).mock(return_value=httpx.Response(200, json={}))
    elsewhere = respx.post("https://attacker.test/control").mock(
        return_value=httpx.Response(200)
    )
    lookup = respx.get("https://api.vapi.ai/call/call_1").mock(
        return_value=httpx.Response(200, json=call_object())
    )
    bridge = Bridge(DeepTrust(api_key="dt_test", base_url=BASE), api_key="vapi_test")

    payload = transcript("user", "my colleague is telling me what to say")
    payload["message"]["call"]["monitor"]["controlUrl"] = "https://attacker.test/control"
    await bridge.handle(payload)

    assert elsewhere.call_count == 0
    assert lookup.call_count == 1
    assert control.call_count == 1


@respx.mock
async def test_vapi_lookup_and_delivery_failures_surface() -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    lookup = respx.get("https://api.vapi.ai/call/call_1").mock(
        return_value=httpx.Response(401, json={"message": "Unauthorized"})
    )
    bridge = Bridge(DeepTrust(api_key="dt_test", base_url=BASE), api_key="bad")

    # The transcript is analysed before delivery, so the turn is kept even
    # though the nudge could not be sent.
    with pytest.raises(httpx.HTTPStatusError):
        await bridge.handle(
            transcript("user", "my colleague is telling me", monitor=False)
        )
    assert lookup.call_count == 1
    call = bridge.session("call_1")
    assert call is not None
    assert len(call.transcript) == 1

    control = respx.post(CONTROL).mock(return_value=httpx.Response(410))
    with pytest.raises(httpx.HTTPStatusError):
        await bridge.handle(transcript("user", "what to say"))
    assert control.call_count == 1


async def test_vapi_aclose_leaves_a_borrowed_client_open() -> None:
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    mine = httpx.AsyncClient()
    await Bridge(dt, api_key="vapi_test", http=mine).aclose()
    assert not mine.is_closed
    await mine.aclose()

    bridge = Bridge(dt, api_key="vapi_test")
    await bridge.aclose()
    assert bridge._http.is_closed


@respx.mock
async def test_vapi_does_not_analyse_the_agents_own_turn() -> None:
    analyze = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    bridge = Bridge(dt, api_key="vapi_test")

    await bridge.handle(transcript("assistant", "I need to confirm it is you first"))

    assert analyze.call_count == 0
    # It is still in the transcript. It is just not a reason to run a job.
    call = bridge.session("call_1")
    assert call is not None
    assert len(call.transcript) == 1
    assert call.transcript.turns[0].role == "agent"


@respx.mock
async def test_vapi_end_of_call_report_ends_the_session() -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ONE_NUDGE)
    )
    respx.post(CONTROL).mock(return_value=httpx.Response(200, json={}))
    ended = respx.post(f"{BASE}/agents/sessions/sess_1/end").mock(
        return_value=httpx.Response(200, json={"ended": True})
    )
    dt = DeepTrust(api_key="dt_test", base_url=BASE)
    bridge = Bridge(dt, api_key="vapi_test")

    await bridge.handle(transcript("user", "my colleague is telling me what to say"))
    await bridge.handle(
        {
            "message": {
                "type": "end-of-call-report",
                "endedReason": "hangup",
                "call": call_object(),
                "artifact": {"transcript": "..."},
            }
        }
    )

    assert ended.call_count == 1
    # The bridge forgets the call, so a late webhook does not resurrect it.
    assert bridge.session("call_1") is None
