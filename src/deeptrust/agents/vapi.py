"""VAPI adapter.

    from deeptrust.agents import DeepTrust
    from deeptrust.agents.vapi import Bridge

    bridge = Bridge(DeepTrust(), api_key=os.environ["VAPI_API_KEY"])

    # inside your POST /vapi/webhook route
    await bridge.handle(payload, user=user)

Nothing here holds a connection. VAPI posts server messages to your server URL
while the call is happening, so the adapter is a handler you call from your own
webhook route: it reads the transcript out of those messages, and sends nudges
back by posting to the call's control URL, an HTTPS endpoint VAPI issues per
call.

Two consequences of that, one different from each of the other adapters:

A nudge is delivered as an `add-message` with `triggerResponseEnabled`, so the
agent answers it at once rather than folding it into its next turn. That makes
a VAPI nudge behave like LiveKit's interrupt, not like ElevenLabs' contextual
update.

The webhook carries transcript messages, not audio, so nothing here has access
to the audio stream. The call's `listenUrl` is raw audio and is not used.

The control URL arrives on the call object as `monitor.controlUrl`. It is taken
from the webhook payload when present and on a VAPI host, and otherwise fetched
from VAPI once per call with the API key, so an inbound call, which was never
created by your code, is nudged the same as an outbound one.

The webhook route is yours, so checking that a request came from VAPI is yours
too: set a server URL secret in VAPI and compare the `x-vapi-secret` header
before calling `handle`. A payload that reaches `handle` is trusted.

No extra is needed: the adapter uses httpx, which the client already depends on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

from ..errors import ConfigError
from ..types import User
from . import DeepTrust, Session

API_URL = "https://api.vapi.ai"
# A control URL from a webhook is posted to, so one that does not point at
# VAPI is ignored and the call's own is fetched instead.
CONTROL_HOST_SUFFIX = ".vapi.ai"


def add_message_command(text: str) -> dict[str, Any]:
    """The control URL's command for a system message the agent acts on now.

    `triggerResponseEnabled` is what makes the agent respond to the message
    immediately. Without it the message sits in the conversation until the
    caller speaks again, and the nudge lands a turn late.
    """
    return {
        "type": "add-message",
        "message": {"role": "system", "content": text},
        "triggerResponseEnabled": True,
    }


class _Call:
    """What the bridge keeps per VAPI call between webhooks."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.control_url: str | None = None
        # One fetch of the control URL at a time. Several webhooks for the
        # same call can be in flight at once, and each would otherwise fetch.
        self.lock = asyncio.Lock()


class Bridge:
    """Feeds VAPI webhooks to DeepTrust and nudges the call back."""

    def __init__(
        self,
        dt: DeepTrust,
        *,
        api_key: str,
        deliver: bool = True,
        on_analysis: Callable[[Any], None] | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """`http` is the client used to reach VAPI; it defaults to a fresh
        `httpx.AsyncClient` and exists so a test can supply its own."""
        if not api_key:
            raise ConfigError(
                "Bridge needs a VAPI API key. It fetches the control URL for "
                "calls whose webhooks do not carry one."
            )
        self._dt = dt
        self._key = api_key
        self._deliver = deliver
        self._on_analysis = on_analysis
        self._own_http = http is None
        self._http = http or httpx.AsyncClient(timeout=10.0)
        self._calls: dict[str, _Call] = {}

    async def handle(
        self,
        payload: dict[str, Any],
        *,
        user: User | None = None,
    ) -> None:
        """Handle one server message from VAPI.

        Call it with the parsed JSON body of the webhook, either the whole
        body or its `message` object. Messages other than final transcripts and
        the end-of-call report are ignored, so it is safe to route every VAPI
        message here. Nothing is returned: none of the messages this reads
        expect a response.

        `user` is recorded the first time a call is seen.
        """
        msg = payload.get("message") or payload
        call = msg.get("call") or {}
        cid = str(call.get("id") or "")
        if not cid:
            return

        state = self._calls.get(cid)
        if state is None:
            state = _Call(self._dt.session(external_id=cid, user=user, platform="vapi"))
            self._calls[cid] = state
        url = str((call.get("monitor") or {}).get("controlUrl") or "")
        if url and _is_vapi_url(url):
            state.control_url = url

        if msg.get("type") == "end-of-call-report":
            self._calls.pop(cid, None)
            await state.session.end()
            return

        role, text = _read_turn(msg)
        if not text:
            return
        state.session.append(role, text)
        if role != "user":
            return

        result = await state.session.analyze()
        if result is None:
            return
        if self._on_analysis:
            self._on_analysis(result)
        if not self._deliver:
            return
        for nudge in result.nudges:
            await self._control(state, cid, add_message_command(nudge.render()))

    def session(self, call_id: str) -> Session | None:
        """The DeepTrust session for a call still in progress, if any."""
        state = self._calls.get(call_id)
        return state.session if state else None

    async def aclose(self) -> None:
        """Close the HTTP client, if the bridge created it. A client passed in
        stays open, since its owner may still be using it."""
        if self._own_http:
            await self._http.aclose()

    async def _control(self, state: _Call, cid: str, command: dict[str, Any]) -> None:
        async with state.lock:
            if state.control_url is None:
                state.control_url = await self._fetch_control_url(cid)
        if not state.control_url:
            return
        r = await self._http.post(state.control_url, json=command)
        r.raise_for_status()

    async def _fetch_control_url(self, cid: str) -> str:
        r = await self._http.get(
            f"{API_URL}/call/{cid}",
            headers={"authorization": f"Bearer {self._key}"},
        )
        r.raise_for_status()
        monitor = r.json().get("monitor") or {}
        return str(monitor.get("controlUrl") or "")


def _is_vapi_url(url: str) -> bool:
    u = httpx.URL(url)
    return u.scheme == "https" and u.host.endswith(CONTROL_HOST_SUFFIX)


def _read_turn(msg: dict[str, Any]) -> tuple[str, str]:
    """Extract a turn from a server message, or ("", "") if it is not one.

    The only place VAPI message names appear. Only final transcripts count: a
    partial is the same sentence still being recognised, and a transcript that
    carries it several times over is analysed several times over, with the
    same finding and the same nudge each time. Everything else the server URL
    receives during a call, such as speech and status updates, is ignored.
    """
    kind = str(msg.get("type") or "")
    if not kind.startswith("transcript"):
        return "", ""
    # A server URL subscribed to finals only receives the filtered name,
    # `transcript[transcriptType="final"]`, with no separate field.
    final = msg.get("transcriptType") == "final" or "final" in kind
    if not final:
        return "", ""
    role = "user" if msg.get("role") == "user" else "agent"
    return role, str(msg.get("transcript") or "").strip()
