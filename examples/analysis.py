"""The DeepTrust half of the demo servers, shared by both platforms.

The platform half of a demo differs on every platform: LiveKit mints a room
token, ElevenLabs mints a conversation token, and the tools live in a different
process in each. The DeepTrust half does not differ at all, so it lives here
and both servers mount it:

    import analysis

    app.include_router(analysis.router)
    analysis.expect_caller(User(id="caller-1", role="MEMBER"), platform="livekit")

It serves four routes the demo UI is written against:

    POST /utterance         a caller turn. Enqueues, answers immediately.
    GET  /nudges/{id}       SSE of everything the analysis produced.
    GET  /record/{id}       the call's evidence artifact.
    GET  /sop               the organisation's runbook, procedures and controls.

Analysis goes through the SDK in this repo: one `Session` per call, a turn
appended for every line spoken, and `analyze()` on a background worker so a
caller's turn is never waiting on a round trip to the API. Nudges leave over
SSE because that is what a sidecar's back channel carries in production, and it
is the one shape a browser can subscribe to without a socket of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from deeptrust.agents import Analysis, DeepTrust, Session, User

router = APIRouter()

# The reply to /utterance carries this turn's signals so the transcript can
# mark the caller's own words. That means waiting for the analysis, which is a
# network round trip, so the wait is bounded and the reply goes out without the
# marks if the budget runs out. Nudges never wait on it: they are already on
# their way out of /nudges.
SETTLE_MS = float(os.getenv("UTTERANCE_SETTLE_MS", "1200"))

# What the UI's transcript treats as a signal worth marking on a caller's line.
SIGNAL_KINDS = {"social_engineering", "coercion", "risk_signal"}

# An organisation API key travels in its own header, and the base URL is the
# same one the SDK's client reads, so a server configured for analysis is
# already configured for this.
API_KEY_HEADER = "X-DeepTrust-Api-Key"
DEFAULT_BASE_URL = "https://app.deeptrust.ai/api/v1"

# Where the organisation's runbook, procedures and controls are read from. The
# analyze contract does not carry them back, so a UI that wants to show what
# the analysis is working against has to ask for them separately.
CONTEXT_PATH = "/agents/context"

# Both an answer and a refusal are cached: the browser asks on every reload,
# and neither the organisation's policy nor the set of routes the API serves
# changes between two of them.
CONTEXT_TTL_S = 60.0

_client: DeepTrust | None = None


def client() -> DeepTrust:
    """The API client, built on first use.

    Not at import: `DeepTrust()` wants a key, and a server should still be able
    to boot and serve its platform half when analysis is not configured.
    """
    global _client
    if _client is None:
        _client = DeepTrust()
    return _client


@dataclass
class Call:
    """One call, and everything the demo needs to say about it afterwards."""

    id: str
    session: Session
    platform: str
    user: User
    transcript: list[str] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    # Whatever the platform server saw the agent do: tool calls, tickets,
    # escalations. Recorded here so one artifact covers the whole call.
    events: list[dict[str, Any]] = field(default_factory=list)
    progress: list[dict[str, Any]] = field(default_factory=list)
    risk_level: str | None = None
    # Bounded, so a slow analysis sheds load instead of growing memory.
    inbox: asyncio.Queue[tuple[str, float, asyncio.Future[list[str]]]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=64)
    )
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)
    worker: asyncio.Task[None] | None = None
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "queued": 0,
            "processed": 0,
            "dropped": 0,
            "inflight": 0,
            "last_lag_ms": 0.0,
        }
    )


_calls: dict[str, Call] = {}

# The caller a session gets when it turns up unannounced. The ElevenLabs
# conversation id is minted in the browser, so the server cannot bind a caller
# to it before the call starts; it registers who it is about to put on the
# phone instead, and the first turn picks that up.
_expected: tuple[User, str] = (User(id="unknown", role="MEMBER"), "custom")


def expect_caller(user: User, *, platform: str = "custom") -> None:
    """Name the caller for the next session that appears without one."""
    global _expected
    _expected = (user, platform)


def register(session_id: str, *, user: User, platform: str | None = None) -> Call:
    """Bind a caller to a session id, whether or not it has been seen yet.

    Idempotent, and it upgrades the caller on a call that started with the
    placeholder: the SDK sends the user with every job, so a later job carries
    the identity even though the first one could not.
    """
    call = _call(session_id)
    call.user = user
    call.session.user = user
    if platform:
        call.platform = platform
        call.session.platform = platform
    return call


def _call(session_id: str) -> Call:
    call = _calls.get(session_id)
    if call is None:
        user, platform = _expected
        call = Call(
            id=session_id,
            session=client().session(
                external_id=session_id, platform=platform, user=user
            ),
            platform=platform,
            user=user,
        )
        _calls[session_id] = call
    return call


def publish(session_id: str, payload: dict[str, Any]) -> None:
    """Put an event on the call's SSE stream and in its record."""
    call = _call(session_id)
    call.events.append(payload)
    _fanout(call, payload)


def note_event(session_id: str, payload: dict[str, Any]) -> None:
    """Record an event without streaming it.

    For events the UI already has: a tool relay renders what came back in its
    own reply, and streaming the same event would draw it twice.
    """
    _call(session_id).events.append(payload)


def _fanout(call: Call, payload: dict[str, Any]) -> None:
    for sub in call.subscribers:
        with contextlib.suppress(asyncio.QueueFull):
            sub.put_nowait(payload)


def _role(role: str) -> str:
    """The UI speaks in the platforms' vocabulary, the SDK in its own."""
    return "user" if role == "user" else "agent" if role != "system" else "system"


# ── background analysis ─────────────────────────────────────────────────────


def _settle(future: asyncio.Future[list[str]], signals: list[str]) -> None:
    if not future.done():
        future.set_result(signals)


async def _worker(session_id: str) -> None:
    call = _calls[session_id]
    while True:
        role, enqueued_at, waiting = await call.inbox.get()
        call.stats["inflight"] += 1
        try:
            result = await call.session.analyze()
            lag_ms = round((time.perf_counter() - enqueued_at) * 1000, 2)
            call.stats["processed"] += 1
            call.stats["last_lag_ms"] = lag_ms
            # None means every turn in the transcript was covered by an earlier
            # job, which happens when two turns land back to back and the first
            # job picks up both.
            _settle(waiting, _record(call, result, role, lag_ms) if result else [])
        except Exception as exc:  # never let one bad turn kill the worker
            _settle(waiting, [])
            _fanout(call, {"type": "analyzer_error", "detail": str(exc)})
        finally:
            call.stats["inflight"] -= 1
            call.inbox.task_done()


def _record(call: Call, result: Analysis, role: str, lag_ms: float) -> list[str]:
    """Turn one analysis into the events the panel renders. Returns the signal
    names for the turn that triggered it."""
    signals: list[str] = []
    call.risk_level = result.risk_level

    for finding in result.findings:
        nudge = finding.nudge
        rec: dict[str, Any] = {
            "analyzer": "deeptrust",
            "kind": finding.kind,
            "detail": finding.detail,
            "risk_level": finding.risk_level,
            "confidence": finding.confidence,
            "title": nudge.title if nudge else None,
            "note": nudge.description if nudge else None,
            "next_step": nudge.details if nudge else None,
            # The one string that reaches the agent, which is exactly what
            # `Nudge.render()` is for.
            "nudge": nudge.render() if nudge else None,
            "role": role,
            "latency_ms": result.latency_ms,
            "fanout_ms": result.latency_ms,
            "queue_lag_ms": lag_ms,
        }
        call.findings.append(rec)
        _fanout(call, {"type": "nudge" if nudge else "finding", **rec})
        if finding.kind in SIGNAL_KINDS:
            signals.append(finding.detail)

    call.progress = [
        {
            "sop_id": p.sop_id,
            "name": p.name,
            "applicable": p.applicable,
            "in_progress": p.in_progress,
            "being_followed": p.being_followed,
            "steps_completed": p.steps_completed,
            "steps_total": p.steps_total,
        }
        for p in result.progress
    ]
    return signals


def _ensure_worker(call: Call) -> None:
    if call.worker is None or call.worker.done():
        call.worker = asyncio.create_task(_worker(call.id))


# ── endpoints ───────────────────────────────────────────────────────────────


class UtteranceReq(BaseModel):
    session_id: str
    role: str
    text: str


@router.post("/utterance", status_code=202)
async def utterance(req: UtteranceReq) -> dict[str, Any]:
    """Ingest one turn. The transcript is kept here, analysis runs behind it."""
    t0 = time.perf_counter()
    call = _call(req.session_id)
    role = _role(req.role)
    call.transcript.append(f"{role}: {req.text}")
    call.session.append(role, req.text)

    # Only a caller's turn gets a job of its own. An agent turn is context for
    # the next one, and the whole transcript goes with every job anyway.
    if role != "user":
        return {
            "accepted": True,
            "depth": call.inbox.qsize(),
            "warning_signs": [],
            "ingest_ms": round((time.perf_counter() - t0) * 1000, 3),
        }

    _ensure_worker(call)
    waiting: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
    item = (role, time.perf_counter(), waiting)
    try:
        call.inbox.put_nowait(item)
        call.stats["queued"] += 1
    except asyncio.QueueFull:
        # Shed the oldest rather than stall ingest.
        try:
            _, _, stale = call.inbox.get_nowait()
            call.inbox.task_done()
            _settle(stale, [])
            call.inbox.put_nowait(item)
        except asyncio.QueueEmpty:
            _settle(waiting, [])
        call.stats["dropped"] += 1

    signals: list[str] = []
    if SETTLE_MS > 0:
        # On a timeout the analysis is still running and will still publish.
        # Only the marks on this one line are lost.
        with contextlib.suppress(TimeoutError):
            signals = await asyncio.wait_for(asyncio.shield(waiting), SETTLE_MS / 1000)

    return {
        "accepted": True,
        "depth": call.inbox.qsize(),
        "warning_signs": signals,
        "ingest_ms": round((time.perf_counter() - t0) * 1000, 3),
    }


@router.get("/nudges/{session_id}")
async def nudges(session_id: str) -> StreamingResponse:
    """SSE of every finding and nudge on this call.

    Subscribing creates the call if the first turn has not arrived yet, which
    is the usual order: the browser opens this stream as soon as it has a
    conversation id.
    """
    call = _call(session_id)
    sub: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
    call.subscribers.append(sub)

    async def stream() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(sub.get(), timeout=15)
                    yield f"data: {json.dumps(item)}\n\n"
                except TimeoutError:
                    # Proxies and browsers both drop an idle stream.
                    yield ": keepalive\n\n"
        finally:
            if sub in call.subscribers:
                call.subscribers.remove(sub)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/record/{room}")
def record(room: str) -> dict[str, Any]:
    """The call's evidence artifact: what was said, and what was found in it."""
    call = _calls.get(room)
    if call is None:
        return {"session_id": room, "found": False}
    return {
        "session_id": room,
        "found": True,
        "platform": call.platform,
        "deeptrust_session_id": call.session.id,
        "caller": call.user.to_wire(),
        "transcript": call.transcript,
        "findings": call.findings,
        "decisions": call.events,
        "progress": call.progress,
        "risk_level": call.risk_level,
        "pipeline": call.stats,
    }


# ── the organisation's context ──────────────────────────────────────────────
#
# The runbook, the procedures and the controls belong to the organisation the
# API key was issued to, and only the API knows what they are. Nothing here
# supplies a default for them: an example that ships policy of its own would be
# showing an organisation rules nobody in it wrote, and every screen built on
# top of that would be describing a company that does not exist.


def _empty_context(source: str, reason: str, status: int | None = None) -> dict[str, Any]:
    """An organisation with no context to show, and why none is being shown.

    `reason` is a code rather than a sentence so the caller decides how to say
    it. An empty context with no reason is indistinguishable from a fully
    configured organisation whose fields happen to be blank.
    """
    return {
        "runbook": None,
        "sops": [],
        "documents": [],
        "controls": [],
        "protected_accounts": [],
        "source": source,
        "status": status,
        "reason": reason,
    }


async def _fetch_context() -> dict[str, Any]:
    key = os.getenv("DEEPTRUST_API_KEY")
    base = (os.getenv("DEEPTRUST_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    source = f"{base}{CONTEXT_PATH}"

    if not key:
        return _empty_context(source, "api_key_missing")

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(source, headers={API_KEY_HEADER: key})
    except httpx.HTTPError:
        # Only the code travels. An httpx error carries the request with it,
        # headers included, and the key is in one of those headers.
        return _empty_context(source, "backend_unreachable")

    if r.status_code == 404:
        return _empty_context(source, "context_endpoint_not_found", r.status_code)
    if r.status_code in (401, 403):
        return _empty_context(source, "api_key_rejected", r.status_code)
    if not r.is_success:
        return _empty_context(source, "backend_error", r.status_code)

    try:
        body = r.json()
    except ValueError:
        return _empty_context(source, "malformed_response", r.status_code)
    if not isinstance(body, dict):
        return _empty_context(source, "malformed_response", r.status_code)

    context = {
        "runbook": body.get("runbook") or None,
        "sops": body.get("sops") or [],
        "documents": body.get("documents") or [],
        "controls": body.get("controls") or [],
        "protected_accounts": body.get("protected_accounts") or [],
    }
    populated = any(bool(v) for v in context.values())
    return {
        **context,
        "source": source,
        "status": r.status_code,
        # An organisation that has been created and not filled in is the common
        # case, and it is not an error. It reads differently from a request the
        # API refused, so it gets a code of its own.
        "reason": "ok" if populated else "organisation_context_empty",
    }


_context: tuple[float, dict[str, Any]] | None = None


async def org_context() -> dict[str, Any]:
    """The organisation's runbook, procedures and controls, from the API."""
    global _context
    if _context is not None and time.monotonic() - _context[0] < CONTEXT_TTL_S:
        return _context[1]
    fetched = await _fetch_context()
    _context = (time.monotonic(), fetched)
    return fetched


@router.get("/sop")
async def sop() -> dict[str, Any]:
    """What the analysis is working against, and where it came from."""
    return await org_context()
