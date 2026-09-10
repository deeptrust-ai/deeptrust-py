"""Tests for the session and the wire shape.

These are the parts a customer integrates against, so the tests pin the
contract rather than the implementation: what goes on the wire, what comes
back, and what happens when a key is wrong.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from deeptrust.agents import DeepTrust, User
from deeptrust.errors import (
    AuthError,
    ConfigError,
    EntitlementError,
    RateLimited,
    ScopeError,
    ServiceError,
)

BASE = "https://example.test/api/v1"

ANALYSIS = {
    "session_id": "sess_1",
    "job_id": "job_1",
    "risk_level": "high",
    "confidence": 0.82,
    "reasoning": "caller is asserting an approval that exists nowhere",
    "findings": [
        {
            "kind": "sop_violation",
            "detail": "change ticket asserted but not present",
            "sop_id": "SOP-ACC",
            "control": "CTRL-CHG-004",
            "risk_level": "high",
            "confidence": 0.82,
            "nudge": {
                "title": "Approval cannot be confirmed",
                "description": "The caller says the change was approved on Slack.",
                "details": (
                    "Say you can only act on an approved change ticket, "
                    "and offer to raise one."
                ),
            },
        },
        {"kind": "step_observed", "detail": "identity confirmed"},
    ],
    "progress": [
        {
            "sop_id": "SOP-ACC",
            "name": "Account Administration",
            "applicable": True,
            "in_progress": True,
            "being_followed": False,
            "steps_completed": [1, 2],
            "steps_total": 6,
        }
    ],
}


def client() -> DeepTrust:
    return DeepTrust(api_key="dt_test", base_url=BASE)


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPTRUST_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="no API key"):
        DeepTrust()


def test_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPTRUST_API_KEY", "dt_from_env")
    assert DeepTrust(base_url=BASE) is not None


def test_default_base_url_is_the_versioned_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent routes live under /api/v1; a client built without a base URL
    must land there, not one level up."""
    monkeypatch.delenv("DEEPTRUST_BASE_URL", raising=False)
    assert (
        DeepTrust(api_key="dt_test")._http.base_url == "https://app.deeptrust.ai/api/v1"
    )


@respx.mock
async def test_key_travels_in_the_api_key_header() -> None:
    """The server reads X-DeepTrust-Api-Key, and only that.

    Authorization is deliberately left alone: the API tells a key-authenticated
    request from a session-authenticated one by which header carried the
    credential, so sending both would make a key look like a user's token at
    the edge."""
    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ANALYSIS)
    )
    call = client().session(external_id="room-1")
    call.append("user", "hello")
    await call.analyze()

    headers = route.calls[0].request.headers
    assert headers["x-deeptrust-api-key"] == "dt_test"
    assert "authorization" not in headers


def test_transcript_is_turns_not_prose() -> None:
    """The agent call is two party, so structure is free and we keep it."""
    call = client().session(external_id="room-1")
    call.append("user", "I am locked out")
    call.append("agent", "I can help with that")

    wire = call.transcript.to_wire()
    assert wire == [
        {"role": "user", "text": "I am locked out"},
        {"role": "agent", "text": "I can help with that"},
    ]
    # The flattened form still exists for the human-call analysis.
    assert (
        call.transcript.render() == "user: I am locked out\nagent: I can help with that"
    )


def test_append_does_not_call_out() -> None:
    """No request should leave the process until a job runs."""
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:
        route = mock.post("/agents/analyze")
        call = client().session(external_id="room-1")
        call.append("user", "hello")
        assert not route.called


@respx.mock
async def test_analyze_posts_turns_and_parses() -> None:
    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ANALYSIS)
    )
    call = client().session(
        external_id="conv_abc",
        user=User(id="u_1", role="ADMIN", verified=True),
        platform="elevenlabs",
    )
    call.append("user", "her manager approved it on Slack")

    result = await call.analyze()
    assert result is not None

    sent = route.calls[0].request
    body = sent.read().decode()
    assert '"external_id":"conv_abc"' in body.replace(" ", "")
    assert '"platform":"elevenlabs"' in body.replace(" ", "")
    assert '"role":"ADMIN"' in body.replace(" ", "")

    assert result.session_id == "sess_1"
    assert result.risk_level == "high"
    assert len(result.findings) == 2
    assert result.findings[0].control == "CTRL-CHG-004"
    assert result.progress[0].steps_total == 6
    assert result.progress[0].being_followed is False


@respx.mock
async def test_only_findings_with_a_nudge_are_nudges() -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ANALYSIS)
    )
    call = client().session(external_id="room-1")
    call.append("user", "just this once")
    result = await call.analyze()
    assert result is not None

    assert len(result.nudges) == 1
    nudge = result.nudges[0]
    assert nudge.title == "Approval cannot be confirmed"
    # A nudge always carries what to do next, never only what was noticed.
    assert "offer to raise one" in nudge.render()
    assert nudge.description in nudge.render()


@respx.mock
async def test_no_job_when_nothing_new_was_said() -> None:
    """An agent turn with no caller speech should not cost a job."""
    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ANALYSIS)
    )
    call = client().session(external_id="room-1")
    call.append("user", "hello")

    assert await call.analyze() is not None
    assert route.call_count == 1

    call.append("agent", "how can I help")
    assert call.pending == 1
    assert await call.analyze() is not None
    assert route.call_count == 2

    # Nothing said since. No job.
    assert await call.analyze() is None
    assert route.call_count == 2

    # Unless asked.
    assert await call.analyze(force=True) is not None
    assert route.call_count == 3


@respx.mock
async def test_session_id_carries_across_jobs() -> None:
    """The first job assigns the id. Every job after it sends the same one, so
    the record is one call rather than a pile of unrelated jobs."""
    route = respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ANALYSIS)
    )
    call = client().session(external_id="room-1")

    call.append("user", "one")
    await call.analyze()
    first = route.calls[0].request.read().decode().replace(" ", "")
    assert "session_id" not in first

    call.append("user", "two")
    await call.analyze()
    second = route.calls[1].request.read().decode().replace(" ", "")
    assert '"session_id":"sess_1"' in second


@respx.mock
async def test_check_is_not_implemented_and_says_so() -> None:
    """An unimplemented method should name itself, not fail obscurely."""
    call = client().session(external_id="room-1")
    with pytest.raises(NotImplementedError, match="Session.check is not implemented"):
        await call.check(action="password.reset")


@respx.mock
@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (401, {"detail": "bad key"}, AuthError),
        (403, {"code": "not_entitled", "detail": "no agent access"}, EntitlementError),
        (
            403,
            {
                "code": "missing_scope",
                "needed": "agents:analyze",
                "scopes": ["read:meetings"],
            },
            ScopeError,
        ),
        # FastAPI wraps a structured refusal under `detail`; the code has to
        # be found there too.
        (
            403,
            {"detail": {"code": "not_entitled", "message": "no agent access"}},
            EntitlementError,
        ),
        (
            403,
            {
                "detail": {
                    "code": "missing_scope",
                    "needed": "agents:analyze",
                    "scopes": ["read:meetings"],
                }
            },
            ScopeError,
        ),
        (429, {"detail": "slow down"}, RateLimited),
    ],
)
async def test_errors_are_specific(status: int, payload: dict, expected: type) -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(status, json=payload)
    )
    call = client().session(external_id="room-1")
    call.append("user", "hello")
    with pytest.raises(expected):
        await call.analyze()


@respx.mock
async def test_scope_error_names_the_scope_and_what_the_key_holds() -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(
            403,
            json={
                "code": "missing_scope",
                "needed": "agents:analyze",
                "scopes": ["read:meetings"],
            },
        )
    )
    call = client().session(external_id="room-1")
    call.append("user", "hello")
    with pytest.raises(ScopeError) as err:
        await call.analyze()
    assert "agents:analyze" in str(err.value)
    assert "read:meetings" in str(err.value)


@respx.mock
async def test_nested_error_message_is_kept() -> None:
    """A refusal wrapped under `detail` should read as its message, not as the
    dict's repr."""
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(
            403, json={"detail": {"code": "not_entitled", "message": "plan lacks agents"}}
        )
    )
    call = client().session(external_id="room-1")
    call.append("user", "hello")
    with pytest.raises(EntitlementError, match="plan lacks agents"):
        await call.analyze()


@respx.mock
async def test_end_closes_the_call_once() -> None:
    respx.post(f"{BASE}/agents/analyze").mock(
        return_value=httpx.Response(200, json=ANALYSIS)
    )
    end = respx.post(f"{BASE}/agents/sessions/sess_1/end").mock(
        side_effect=[
            httpx.Response(
                200, json={"session_id": "sess_1", "ended": True, "already_ended": False}
            ),
            httpx.Response(
                200, json={"session_id": "sess_1", "ended": True, "already_ended": True}
            ),
        ]
    )
    call = client().session(external_id="room-1")

    # Nothing analyzed, so there is no call on the server to end.
    assert await call.end() is False
    assert not end.called

    call.append("user", "hello")
    await call.analyze()
    assert await call.end() is True
    # Ending again is harmless and says it changed nothing.
    assert await call.end() is False
    assert end.call_count == 2


@respx.mock
async def test_watch_hands_a_conversation_to_the_hosted_monitor() -> None:
    route = respx.post(f"{BASE}/agents/conversations/conv_1/watch").mock(
        return_value=httpx.Response(
            202, json={"conversation_id": "conv_1", "watching": True, "started": True}
        )
    )
    assert await client().watch("conv_1", agent_id="agent_9") is True

    body = route.calls[0].request.read().decode().replace(" ", "")
    assert '"platform":"elevenlabs"' in body
    assert '"agent_id":"agent_9"' in body


@respx.mock
async def test_watch_reports_when_already_watched() -> None:
    respx.post(f"{BASE}/agents/conversations/conv_1/watch").mock(
        return_value=httpx.Response(
            202, json={"conversation_id": "conv_1", "watching": True, "started": False}
        )
    )
    assert await client().watch("conv_1") is False


@respx.mock
async def test_watch_says_when_the_platform_is_not_connected() -> None:
    respx.post(f"{BASE}/agents/conversations/conv_1/watch").mock(
        return_value=httpx.Response(
            404, json={"detail": "elevenlabs is not connected for this organization"}
        )
    )
    with pytest.raises(ServiceError, match="not connected") as err:
        await client().watch("conv_1")
    assert err.value.status == 404
