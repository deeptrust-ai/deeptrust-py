"""The standalone path, from the customer's side, with everything logged.

A customer already receives ElevenLabs' conversation-initiation webhook at a URL
they own. This is that URL. It takes the call-start event and uses the DeepTrust
SDK to start watching the conversation, with the monitor socket held in this
process using the customer's own ElevenLabs key. DeepTrust never talks to
ElevenLabs on this path.

    uv run python customer_webhook.py

Every step prints: the raw webhook body, the reply handed back to ElevenLabs,
the socket opening, every event on it, every turn appended, every analysis with
its findings, and every nudge delivered back to the agent.

Environment (see .env):
    DEEPTRUST_API_KEY     organization key, sent as X-DeepTrust-Api-Key
    DEEPTRUST_BASE_URL    https://app.dev.deeptrust.ai/api/v1 for dev
    ELEVENLABS_API_KEY    a key that can see the agent taking the call
    PORT                  defaults to 8090
    DYNAMIC_VARS          JSON returned to ElevenLabs at call setup; the agent
                          prompt's variables must all be present or the prompt
                          renders with placeholders
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request

from deeptrust.agents import DeepTrust, User
from deeptrust.agents.elevenlabs import Monitor

load_dotenv()

PORT = int(os.getenv("PORT", "8090"))

# The agent's prompt interpolates these. ElevenLabs expects the initiation
# response to carry every variable the agent defines; a missing one renders as
# an unresolved placeholder in the system prompt.
DEFAULT_DYNAMIC_VARS = {
    "caller_name": "Guest",
    "caller_username": "M-000000",
    "caller_role": "MEMBER",
    "caller_verified": "not yet",
}


def log(tag: str, message: str = "") -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    print(f"{stamp}  {tag:<9} {message}", flush=True)


def _dynamic_vars() -> dict[str, str]:
    raw = os.getenv("DYNAMIC_VARS")
    if not raw:
        return dict(DEFAULT_DYNAMIC_VARS)
    return json.loads(raw)


# ── socket plumbing, wrapped only so the run can be read afterwards ──────────


class _LoggedSocket:
    """Passes the socket through, printing what crosses it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __aiter__(self):
        return self._events()

    async def _events(self):
        async for raw in self._inner:
            try:
                event = json.loads(raw)
                kind = event.get("type")
            except (TypeError, ValueError):
                kind = "?"
                event = raw
            if kind in ("user_transcript", "agent_response", "agent_response_correction"):
                log("EVENT", kind)
            else:
                log("EVENT", f"{kind} {json.dumps(event)[:160] if isinstance(event, dict) else ''}")
            yield raw

    async def send(self, data: str) -> None:
        payload = json.loads(data)
        text = payload.get("parameters", {}).get("contextual_update", "")
        log("DELIVER", f"contextual_update -> agent ({len(text)} chars)")
        for line in text.splitlines():
            log("", f"    | {line}")
        await self._inner.send(data)
        log("DELIVER", "sent")


class _LoggedConnect:
    def __init__(self, cm: Any, url: str) -> None:
        self._cm = cm
        self._url = url

    async def __aenter__(self) -> _LoggedSocket:
        inner = await self._cm.__aenter__()
        log("SOCKET", f"open {self._url}")
        return _LoggedSocket(inner)

    async def __aexit__(self, *exc: Any) -> Any:
        log("SOCKET", f"closed{'' if exc[0] is None else f' ({exc[0].__name__}: {exc[1]})'}")
        return await self._cm.__aexit__(*exc)


def logged_connect(url: str, **kwargs: Any) -> _LoggedConnect:
    import websockets

    return _LoggedConnect(websockets.connect(url, **kwargs), url)


class LoggingDeepTrust(DeepTrust):
    """The ordinary client, with each appended turn printed."""

    def session(self, **kwargs: Any):
        call = super().session(**kwargs)
        original = call.append

        def append(role: str, text: str, **kw: Any):
            log("TURN", f"{role:<5} {text}")
            return original(role, text, **kw)

        call.append = append  # type: ignore[method-assign]
        log("SESSION", f"external_id={call.external_id} platform={call.platform}")
        return call


def report(result: Any) -> None:
    log(
        "ANALYSIS",
        f"risk={result.risk_level} findings={len(result.findings)} "
        f"nudges={len(result.nudges)} session={result.session_id} "
        f"in {result.latency_ms:.0f}ms",
    )
    for finding in result.findings:
        detail = finding.detail if len(finding.detail) <= 150 else finding.detail[:147] + "..."
        log("FINDING", f"{finding.kind} (risk={finding.risk_level}): {detail}")
    for nudge in getattr(result, "nudges", []) or []:
        log("NUDGE", str(getattr(nudge, "title", "")))
    if not (getattr(result, "findings", None) or getattr(result, "nudges", None)):
        log("ANALYSIS", "nothing to say about this turn")


monitor: Monitor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One Monitor for the process. It keeps a task per conversation, so the
    same instance serves every call this receiver is told about."""
    global monitor
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        log("FATAL", "ELEVENLABS_API_KEY is not set")
        raise SystemExit(1)
    monitor = Monitor(
        LoggingDeepTrust(), api_key=key, on_analysis=report, connect=logged_connect
    )
    base = os.getenv("DEEPTRUST_BASE_URL", "app.deeptrust.ai (default)")
    log("READY", f"listening on :{PORT}, DeepTrust base {base}")
    yield


app = FastAPI(title="Customer webhook, standalone DeepTrust SDK path", lifespan=lifespan)


@app.post("/calls")
async def call_started(request: Request) -> dict[str, Any]:
    body = await request.body()
    log("WEBHOOK", f"POST /calls {len(body)} bytes")
    try:
        payload = json.loads(body)
    except ValueError:
        log("WEBHOOK", f"body is not JSON: {body[:200]!r}")
        payload = {}
    log("", f"    {json.dumps(payload)[:900]}")

    conversation_id = str(payload.get("conversation_id") or "").strip()
    agent_id = str(payload.get("agent_id") or "").strip()
    caller = payload.get("caller_id") or payload.get("called_number") or "unknown"
    log("WEBHOOK", f"conversation={conversation_id or '(none)'} agent={agent_id or '(none)'} caller={caller}")

    if conversation_id and monitor is not None:
        await monitor.watch(conversation_id, user=User(id=str(caller), role="MEMBER"))
        log("WATCH", f"SDK now watching {conversation_id}")
        task = monitor._watching.get(conversation_id)
        if task is not None:
            task.add_done_callback(_watch_finished)
    else:
        log("WATCH", "no conversation_id in the payload, nothing to watch")

    reply = {
        "type": "conversation_initiation_client_data",
        "dynamic_variables": _dynamic_vars(),
    }
    log("REPLY", json.dumps(reply))
    return reply


def _watch_finished(task: asyncio.Task) -> None:
    if task.cancelled():
        log("WATCH", "watcher cancelled")
        return
    exc = task.exception()
    if exc is not None:
        log("ERROR", f"watcher stopped: {type(exc).__name__}: {exc}")
    else:
        log("WATCH", "watcher finished, conversation closed")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "watching": list((monitor._watching if monitor else {}).keys())}


if __name__ == "__main__":
    log("BOOT", " ".join(sys.argv))
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
