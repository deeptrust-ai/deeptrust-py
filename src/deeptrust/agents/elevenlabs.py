"""ElevenLabs adapter.

    from deeptrust.agents import DeepTrust
    from deeptrust.agents.elevenlabs import Monitor

    monitor = Monitor(DeepTrust(), api_key=os.environ["ELEVENLABS_API_KEY"])
    await monitor.watch(conversation_id, user=user)

Nothing runs inside the agent here. ElevenLabs exposes a monitor socket per
conversation, so this connects to it with a workspace API key, reads the
transcript as it happens, and sends nudges back as contextual updates on the
same socket.

Two consequences of that, both different from the LiveKit adapter:

Contextual updates do not interrupt, so a nudge affects the agent's next turn
rather than the one in progress.

The socket carries transcript events, not audio, so nothing here has access to
the audio stream.

This is the self-hosted way to watch ElevenLabs: the socket is held by your
process, with your ElevenLabs key. The hosted alternative needs neither: connect
the workspace once in the DeepTrust dashboard (Settings, Voice Agents) and
DeepTrust holds the socket itself. A backend that already knows a conversation
id can hand it over with `DeepTrust.watch(conversation_id)`.

Install with the extra:  pip install "deeptrust-ai[elevenlabs]"
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from ..errors import ConfigError
from ..types import User
from . import DeepTrust

MONITOR_URL = "wss://api.elevenlabs.io/v1/convai/conversations/{cid}/monitor"


def contextual_update_command(text: str) -> dict[str, Any]:
    """The monitor socket's command for a contextual update.

    The monitor socket takes commands, not the `{"type": ...}` messages the
    main conversation socket takes. A message in the other shape is ignored
    without an error, which is how 0.0.1 delivered nothing.
    """
    return {
        "command_type": "contextual_update",
        "parameters": {"contextual_update": text},
    }


class Monitor:
    """Watches live ElevenLabs conversations and feeds them to DeepTrust."""

    def __init__(
        self,
        dt: DeepTrust,
        *,
        api_key: str,
        deliver: bool = True,
        on_analysis: Callable[[Any], None] | None = None,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        """`connect` opens the socket; it defaults to `websockets.connect` and
        exists so a test can supply a fake."""
        if not api_key:
            raise ConfigError(
                "Monitor needs an ElevenLabs API key with workspace access. "
                "It reads the conversation and sends contextual updates back."
            )
        self._dt = dt
        self._key = api_key
        self._deliver = deliver
        self._on_analysis = on_analysis
        self._connect = connect
        self._watching: dict[str, asyncio.Task[None]] = {}

    async def watch(
        self,
        conversation_id: str,
        *,
        user: User | None = None,
    ) -> None:
        """Begin watching a conversation. Returns once the watcher is running.

        The conversation must already have started. ElevenLabs replays only its
        last hundred or so events on connect, so connect promptly: for an
        outbound call, use the conversation id returned when the call is
        placed.

        Watching the same conversation twice is a no-op.
        """
        if conversation_id in self._watching:
            return
        task = asyncio.create_task(self._loop(conversation_id, user))
        self._watching[conversation_id] = task
        task.add_done_callback(lambda _: self._watching.pop(conversation_id, None))

    async def stop(self, conversation_id: str) -> None:
        task = self._watching.pop(conversation_id, None)
        if task:
            task.cancel()

    async def _loop(self, cid: str, user: User | None) -> None:
        connect = self._connect
        if connect is None:
            try:
                import websockets
            except ImportError as exc:  # pragma: no cover
                raise ConfigError(
                    "the ElevenLabs adapter needs websockets. "
                    'Install with: pip install "deeptrust-ai[elevenlabs]"'
                ) from exc
            connect = websockets.connect

        call = self._dt.session(external_id=cid, user=user, platform="elevenlabs")
        url = MONITOR_URL.format(cid=cid)

        headers = {"xi-api-key": self._key}
        async with connect(url, additional_headers=headers) as ws:
            async for raw in ws:
                try:
                    ev = json.loads(raw)
                except (TypeError, ValueError):
                    continue

                role, text = _read_turn(ev)
                if not text:
                    continue
                call.append(role, text)
                if role != "user":
                    continue

                result = await call.analyze()
                if result is None:
                    continue
                if self._on_analysis:
                    self._on_analysis(result)
                if not self._deliver:
                    continue
                for nudge in result.nudges:
                    await ws.send(json.dumps(contextual_update_command(nudge.render())))


def _read_turn(ev: dict[str, Any]) -> tuple[str, str]:
    """Extract a turn from a monitor event, or ("", "") if it is not one.

    The only place ElevenLabs event names appear. Events other than user and
    agent transcripts, such as audio and interruptions, are ignored.
    """
    kind = ev.get("type")
    if kind == "user_transcript":
        payload = ev.get("user_transcription_event") or {}
        return "user", str(payload.get("user_transcript") or "").strip()
    if kind == "agent_response":
        payload = ev.get("agent_response_event") or {}
        return "agent", str(payload.get("agent_response") or "").strip()
    return "", ""
