"""The browser's half of the LiveKit example: a room token, and analysis.

The demo UI in ../demo-ui talks to one origin per platform, and this is the
LiveKit one. It mints the token the browser joins the room with, and mounts the
shared analysis router so the panel's utterance, nudge and record endpoints
answer from the same origin.

The caller profile travels in the token as JWT metadata. That is the only place
it cannot be lost or contradicted: the worker reads the name, the role and the
gate off the participant that actually joined, rather than trusting a separate
call that may never arrive.

Run with:  uv run python server.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from pydantic import BaseModel

from deeptrust.agents import User

# The analysis half is platform-neutral and shared with the other examples, so
# it lives one directory up and is imported as a sibling module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

LOG_TAG = "livekit"

app = FastAPI(title="DeepTrust LiveKit example")
# A demo served from a Vite dev server on whatever port it picked today.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# Every request logged with its outcome and duration. When a call does not
# behave, the first question is always which endpoint the browser actually hit
# and what it got back, and that is not answerable from the UI alone.
@app.middleware("http")
async def _log_requests(request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        print(
            f"[{LOG_TAG}] {request.method} {request.url.path} RAISED "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        raise
    ms = (time.perf_counter() - started) * 1000
    # SSE never completes, so its duration is not interesting.
    if request.url.path.startswith("/nudges/"):
        print(
            f"[{LOG_TAG}] {request.method} {request.url.path} -> stream open", flush=True
        )
    else:
        print(
            f"[{LOG_TAG}] {request.method} {request.url.path} -> "
            f"{response.status_code} in {ms:.1f}ms",
            flush=True,
        )
    return response


try:
    import analysis

    app.include_router(analysis.router)
except ImportError:
    # The token half is still useful on its own, and saying so beats a stack
    # trace at boot for anyone running only this file.
    analysis = None  # type: ignore[assignment]
    print(
        "analysis.py not found next to this example: /utterance, /nudges and "
        "/record are not being served.",
        flush=True,
    )

# The authorisation roles policy is written against. They come from the
# caller's own identity system, so an example can only offer the two the SDK
# documents.
ROLES = ["MEMBER", "ADMIN"]

# Measured on a real help desk turn plus a tool call. Reasoning models think
# before answering, which on a phone call is dead air, so the picker says which
# ones do it.
MODELS: list[dict[str, Any]] = [
    {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "provider": "anthropic",
        "reasoning": False,
        "ms": 1265,
        "note": "Fastest of the Claude models. No thinking by default.",
    },
    {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "provider": "anthropic",
        "reasoning": True,
        "ms": 1528,
        "note": "Balanced.",
    },
    {
        "id": "gpt-5.4",
        "label": "GPT-5.4",
        "provider": "openai",
        "reasoning": False,
        "ms": 936,
        "note": "Fastest of the hosted models in testing.",
    },
    {
        "id": "gpt-5.4-mini",
        "label": "GPT-5.4 mini",
        "provider": "openai",
        "reasoning": False,
        "ms": 962,
        "note": "Near-identical latency, cheaper.",
    },
    {
        "id": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "label": "Llama 3.3 70B Turbo",
        "provider": "together",
        "reasoning": False,
        "ms": 643,
        "note": "Fastest measured. Serverless, open weights.",
    },
    {
        "id": "openai/gpt-oss-120b",
        "label": "GPT-OSS 120B",
        "provider": "together",
        "reasoning": False,
        "ms": 898,
        "note": "Open weights, serverless.",
    },
]
DEFAULT_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo"

PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "together": "TOGETHER_API_KEY",
}


def _availability() -> dict[str, bool]:
    """Which providers the worker could actually build an LLM for. Checked here
    so the picker never offers a model that will fail on the first turn."""
    return {p: bool(os.getenv(k)) for p, k in PROVIDER_KEYS.items()}


class TokenReq(BaseModel):
    name: str | None = None
    role: str | None = None
    llm: str | None = None
    # False runs the call with the analysis in shadow: it still watches and
    # still records, and nothing it finds reaches the agent.
    enforcement: bool | None = None


def _account_id(name: str) -> str:
    """An id for the caller, from the name they typed.

    There is no directory behind this example, so the id is the name in a
    stable form. Minting something that looks like an employee number would
    put an account reference in the call record that resolves to nothing.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "caller"


def _profile(req: TokenReq) -> dict[str, Any]:
    """The caller, as the browser asked for them.

    Whoever the caller says they are is a claim, and this example has no way to
    check it, so the profile carries the claim and nothing more.
    """
    name = (req.name or "Caller").strip() or "Caller"
    return {
        "name": name,
        "username": _account_id(name),
        "role": req.role if req.role in ROLES else "MEMBER",
        "llm": req.llm or DEFAULT_MODEL,
        "enforcement": True if req.enforcement is None else bool(req.enforcement),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "providers": _availability()}


@app.get("/profiles")
def profiles() -> dict[str, Any]:
    available = _availability()
    models = [
        {
            **m,
            "available": available[m["provider"]],
            "note": m["note"]
            if available[m["provider"]]
            else f"{m['provider'].title()} key missing",
        }
        for m in MODELS
    ]
    return {"roles": ROLES, "models": models, "default_model": DEFAULT_MODEL}


def _bind_caller(room: str, profile: dict[str, Any]) -> None:
    """Tell the analysis half who is about to call, keyed by the room name the
    worker also uses as the external id. Bound here rather than on the first
    turn, because on LiveKit the room exists before anyone speaks."""
    if analysis is None:
        return
    try:
        analysis.register(
            room,
            user=User(
                id=profile["username"],
                role=profile["role"],
                name=profile["name"],
            ),
            platform="livekit",
        )
    except Exception as exc:
        # A call still happens without analysis configured. It is the token
        # that cannot fail here.
        print(f"caller not bound to {room}: {exc}", flush=True)


@app.post("/token")
def token(req: TokenReq) -> dict[str, Any]:
    """One room per call, so a stale browser tab can never rejoin a call that
    has already been analyzed and recorded under that name."""
    profile = _profile(req)
    room = f"desk-{uuid.uuid4().hex[:8]}"
    _bind_caller(room, profile)
    grant = api.VideoGrants(
        room_join=True, room=room, can_publish=True, can_subscribe=True
    )
    jwt = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity(profile["username"])
        .with_name(profile["name"])
        .with_metadata(json.dumps(profile))
        .with_grants(grant)
        .to_jwt()
    )
    return {
        "token": jwt,
        "url": os.environ["LIVEKIT_URL"],
        "room": room,
        "profile": profile,
    }


if __name__ == "__main__":
    # Loopback only: this holds LiveKit credentials and mints join tokens.
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8101")))
