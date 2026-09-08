"""ElevenLabs adapter.

    from deeptrust.agents import DeepTrust
    from deeptrust.agents.elevenlabs import Monitor

    monitor = Monitor(DeepTrust(), api_key=os.environ["ELEVENLABS_API_KEY"])
    await monitor.watch(conversation_id, caller=caller)

This one needs no code inside the agent. ElevenLabs exposes a per-conversation
monitor socket, so DeepTrust connects from its own side with a workspace key,
reads the transcript as it happens, and sends findings back as contextual
updates on the same socket.

Two things differ from LiveKit and the SDK should not pretend otherwise.

Contextual updates are documented as non-interrupting, so a finding shapes the
next turn rather than the current one. Every delivery reports which it was.

The socket carries events, not audio. That suits this SDK, which does context
analysis and not voice classification, and it is the reason a HIPAA
conversation about it is short.

Install with the extra:  pip install "deeptrust[elevenlabs]"
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from ..errors import ConfigError
from ..types import Caller
from . import DeepTrust

MONITOR_URL = "wss://api.elevenlabs.io/v1/convai/conversations/{cid}/monitor"


class Monitor:
    """Watches live ElevenLabs conversations and feeds them to DeepTrust."""

    def __init__(
        self,
        dt: DeepTrust,
        *,
        api_key: str,
        deliver: bool = True,
        on_analysis: Callable[[Any], None] | None = None,
    ) -> None:
        if not api_key:
            raise ConfigError(
                "Monitor needs an ElevenLabs API key with workspace access. "
                "It reads the conversation and sends contextual updates back."
            )
        self._dt = dt
        self._key = api_key
        self._deliver = deliver
        self._on_analysis = on_analysis
        self._watching: dict[str, asyncio.Task[None]] = {}

    async def watch(
        self,
        conversation_id: str,
        *,
        caller: Caller | None = None,
    ) -> None:
        """Start watching one conversation. Returns as soon as it is running.

        Connect after the conversation has started. ElevenLabs replays only
        about the last hundred cached events, so for an outbound call use the
        id the outbound API hands back rather than waiting for a webhook.
        """
        if conversation_id in self._watching:
            return
        task = asyncio.create_task(self._loop(conversation_id, caller))
        self._watching[conversation_id] = task
        task.add_done_callback(lambda _: self._watching.pop(conversation_id, None))

    async def stop(self, conversation_id: str) -> None:
        task = self._watching.pop(conversation_id, None)
        if task:
            task.cancel()

    async def _loop(self, cid: str, caller: Caller | None) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover
            raise ConfigError(
                "the ElevenLabs adapter needs websockets. "
                'Install with: pip install "deeptrust[elevenlabs]"'
            ) from exc

        call = self._dt.session(external_id=cid, caller=caller, platform="elevenlabs")
        url = MONITOR_URL.format(cid=cid)

        headers = {"xi-api-key": self._key}
        async with websockets.connect(url, additional_headers=headers) as ws:
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
                    await ws.send(
                        json.dumps(
                            {
                                "type": "contextual_update",
                                "text": nudge.render(),
                            }
                        )
                    )


def _read_turn(ev: dict[str, Any]) -> tuple[str, str]:
    """Pull a turn out of a monitor event.

    The event names are ElevenLabs', so this is the one function that has to
    change when they add another. Everything above it works in our vocabulary.
    """
    kind = ev.get("type")
    if kind == "user_transcript":
        payload = ev.get("user_transcription_event") or {}
        return "user", str(payload.get("user_transcript") or "").strip()
    if kind == "agent_response":
        payload = ev.get("agent_response_event") or {}
        return "agent", str(payload.get("agent_response") or "").strip()
    return "", ""
