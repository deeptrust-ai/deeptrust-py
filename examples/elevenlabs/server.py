"""The demo UI's server for the ElevenLabs path: their agent, our analysis.

One process serves both halves the browser talks to.

The platform half provisions an agent on your ElevenLabs workspace and mints a
conversation token per call, so the API key never reaches the browser. The
agent's tools are *client* tools: ElevenLabs calls them in the browser, and the
browser relays each one to POST /tool here, so the tool bodies stay server-side
even though a demo has to run on a laptop with no public URL. In production
these would be server tools, a webhook straight from ElevenLabs to a URL the
customer owns.

The DeepTrust half is `examples/analysis.py`, mounted whole. It is the same
router the LiveKit example serves, because nothing about analysis is
platform-specific: turns in, findings and nudges out.

    uv run python server.py          # http://127.0.0.1:8102

Run the UI in examples/demo-ui against it, and `just devserver` in the repo
root if you want the analysis to run with no DeepTrust key and no network.
"""

from __future__ import annotations

import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from deeptrust.agents import User

# analysis.py is a sibling of this example rather than a package, because both
# examples mount it and neither owns it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import analysis  # noqa: E402

load_dotenv(Path(__file__).with_name(".env"))

LOG_TAG = "eleven"

app = FastAPI(title="DeepTrust example · ElevenLabs")
# The UI is served from another port, so every request from it is cross-origin.
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


app.include_router(analysis.router)

API = "https://api.elevenlabs.io/v1"
AGENT_NAME = "DeepTrust example · IT service desk (browser demo)"

VOICE_ID = "cgSgspJ2msm6clMCkdW9"  # Jessica
# Pinned in the agent's config. A non-reasoning model, because a model that
# thinks before it answers is dead air on a phone call.
LLM = "gemini-2.0-flash"

# The authorisation roles policy is written against. They come from the
# caller's own identity system, so an example can only offer the two the SDK
# documents.
ROLES = ["MEMBER", "ADMIN"]

# The UI's model picker only applies to a platform where we choose the model.
# Here the agent config owns it, so the list has one entry and it is pinned.
MODELS = [
    {
        "id": LLM,
        "label": "Gemini 2.0 Flash",
        "provider": "elevenlabs",
        "reasoning": False,
        # No measured figure to give: the turn is ElevenLabs' round trip, not
        # ours. The picker is hidden on this platform, so nothing renders it.
        "ms": 0,
        "note": "Pinned by the ElevenLabs agent config.",
        "available": True,
    }
]


# ── the agent on the ElevenLabs workspace ───────────────────────────────────
#
# One entry per tool the browser registers, with the same names and parameters,
# because the browser relays straight through to the handler of the same name.
# A mismatch shows up as the agent calling a tool that does not exist.
#
# There is no tool that confirms who the caller is. Doing that means the
# organisation's own directory and challenge service, and an example that
# accepted a code of its own choosing would be telling the agent, the panel and
# the call record that someone had proved their identity when nothing had.

TOOLS: list[dict[str, Any]] = [
    {
        "name": "propose_action",
        "description": (
            "Describe the action you are about to take and get a read-back "
            "sentence for the caller. Always call this before acting."
        ),
        "properties": {
            "action": {
                "type": "string",
                "description": (
                    "one of password.reset, password.reset_other, mfa.reset, "
                    "account.disable, group.add_member, device.enroll, "
                    "mailbox.add_forwarding"
                ),
            },
            "target_account": {
                "type": "string",
                "description": "employee or account id the action touches",
            },
            "group": {"type": "string", "description": "group, for group.add_member"},
            "serial": {"type": "string", "description": "device serial"},
            "destination": {"type": "string", "description": "forwarding address"},
        },
        "required": ["action"],
    },
    {
        "name": "apply_action",
        "description": "Execute the action you proposed, once the caller confirms it.",
        "properties": {},
        "required": [],
    },
    {
        "name": "open_ticket",
        "description": (
            "Raise a ticket for work the desk cannot do on this call. One per call."
        ),
        "properties": {
            "summary": {
                "type": "string",
                "description": "what the caller asked for, in one line",
            }
        },
        "required": ["summary"],
    },
    {
        "name": "transfer_to_human",
        "description": "Hand the caller to a person. Last resort.",
        "properties": {
            "reason": {"type": "string", "description": "why a person is needed"}
        },
        "required": ["reason"],
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}

INSTRUCTIONS = """\
You are an agent on an IT service desk.

You reset passwords, re-enroll two-factor, unlock accounts, enroll devices and
change mailbox rules, and you work from a change ticket that has already been
approved. Anything you cannot do that way goes to a person.

The caller on this line says they are {{caller_name}}, username
{{caller_username}}, role {{caller_role}}. Nothing on this call has confirmed
that, so treat it as a claim and satisfy yourself who you are speaking to
before you change anything on an account.

Describe every change with propose_action first, read the sentence it returns
back to the caller, and only call apply_action once they confirm it.

Keep replies to one or two sentences. You are on a phone call, so do not read
out lists and do not explain internal procedure. If you are told something
about this call that you did not know, take it into account on your next turn.
"""


def _headers() -> dict[str, str]:
    return {
        "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
        "content-type": "application/json",
    }


def _config() -> dict[str, Any]:
    return {
        "name": AGENT_NAME,
        "conversation_config": {
            "agent": {
                "first_message": (
                    "IT service desk, you're speaking with an automated assistant, "
                    "and this call is recorded. How can I help?"
                ),
                "language": "en",
                "dynamic_variables": {
                    "dynamic_variable_placeholders": {
                        "caller_name": "Caller",
                        "caller_username": "caller",
                        "caller_role": "MEMBER",
                    }
                },
                "prompt": {
                    "prompt": INSTRUCTIONS,
                    "llm": LLM,
                    "temperature": 0.3,
                    "tools": [
                        {
                            "type": "client",
                            "name": t["name"],
                            "description": t["description"],
                            "expects_response": True,
                            # The handler answers in about a millisecond. The
                            # rest of the budget is the browser's round trip.
                            "response_timeout_secs": 20,
                            "parameters": {
                                "type": "object",
                                "properties": t["properties"],
                                "required": t["required"],
                            },
                        }
                        for t in TOOLS
                    ],
                },
            },
            "tts": {"voice_id": VOICE_ID, "optimize_streaming_latency": 3},
            "conversation": {
                "max_duration_seconds": 900,
                # The browser drives the transcript, the analysis and the call
                # UI off these, so ask for them explicitly.
                "client_events": [
                    "audio",
                    "interruption",
                    "user_transcript",
                    "agent_response",
                    "agent_response_correction",
                ],
            },
        },
    }


_agent: dict[str, str] = {}


async def provision() -> str:
    """Create the demo agent, or update it in place if it already exists.

    Matching on name keeps this idempotent without storing an id anywhere, and
    it means the agent tracks this file every time the server boots.
    ELEVENLABS_AGENT_ID is deliberately not read here: the browser registers
    the client tools above, and an agent that does not declare them would hold
    a perfectly good conversation and never call one.
    """
    if "id" in _agent:
        return _agent["id"]

    async with httpx.AsyncClient(timeout=60) as c:
        listing = await c.get(
            f"{API}/convai/agents", headers=_headers(), params={"page_size": 100}
        )
        listing.raise_for_status()
        existing = next(
            (a for a in listing.json().get("agents", []) if a.get("name") == AGENT_NAME),
            None,
        )
        if existing:
            agent_id = existing["agent_id"]
            r = await c.patch(
                f"{API}/convai/agents/{agent_id}", headers=_headers(), json=_config()
            )
            r.raise_for_status()
        else:
            created = await c.post(
                f"{API}/convai/agents/create", headers=_headers(), json=_config()
            )
            created.raise_for_status()
            agent_id = created.json()["agent_id"]

    _agent["id"] = agent_id
    return agent_id


async def session_token(agent_id: str) -> str:
    """A short-lived WebRTC token, so the API key never reaches the browser."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/convai/conversation/token",
            headers=_headers(),
            params={"agent_id": agent_id},
        )
        r.raise_for_status()
        return str(r.json()["token"])


# ── the platform half ───────────────────────────────────────────────────────


class SessionReq(BaseModel):
    name: str | None = None
    role: str | None = None
    llm: str | None = None
    enforcement: bool | None = None


def _account_id(name: str) -> str:
    """An id for the caller, from the name they typed.

    There is no directory behind this example, so the id is the name in a
    stable form. Minting something that looks like an employee number would
    put an account reference in the call record that resolves to nothing.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "caller"


def _profile(req: SessionReq) -> dict[str, Any]:
    """The caller, as the browser asked for them.

    Whoever the caller says they are is a claim, and this example has no way to
    check it, so the profile carries the claim and nothing more.
    """
    name = (req.name or "Caller").strip() or "Caller"
    return {
        "name": name,
        "username": _account_id(name),
        "role": req.role if req.role in ROLES else "MEMBER",
        "llm": LLM,
        "enforcement": True if req.enforcement is None else bool(req.enforcement),
    }


def _user(profile: dict[str, Any]) -> User:
    return User(
        id=str(profile["username"]),
        role=str(profile["role"]),
        name=str(profile["name"]),
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/profiles")
def profiles() -> dict[str, Any]:
    """What the caller picker can offer: the roles, and the model."""
    return {"roles": ROLES, "models": MODELS, "default_model": LLM}


@app.post("/eleven/session")
async def eleven_session(req: SessionReq) -> dict[str, Any]:
    """A conversation token, plus the caller values bound to it.

    The caller fields travel as ElevenLabs dynamic variables, so one agent
    serves every call without being re-provisioned.
    """
    agent_id = await provision()
    token = await session_token(agent_id)
    profile = _profile(req)

    # The conversation id is minted when the browser connects, so the analysis
    # side cannot be told the caller by id yet. Name them now; the first turn,
    # or the first tool call, binds them to the id that turns up.
    analysis.expect_caller(_user(profile), platform="elevenlabs")

    return {
        "token": token,
        "agent_id": agent_id,
        "profile": profile,
        "dynamic_variables": {
            "caller_name": profile["name"],
            "caller_username": profile["username"],
            "caller_role": profile["role"],
        },
    }


# ── the tool relay ──────────────────────────────────────────────────────────
#
# Per-call state, because the tools carry it: the pending proposal and the
# ticket.


class Desk:
    def __init__(self, profile: dict[str, Any]) -> None:
        # A copy: the browser holds the profile it was issued and keeps sending
        # it, so anything the call establishes has to be kept here rather than
        # read back off the request.
        self.profile = dict(profile)
        self.pending: dict[str, Any] | None = None
        self.ticket: str | None = None


_desks: dict[str, Desk] = {}


def _desk(session_id: str, profile: dict[str, Any]) -> Desk:
    desk = _desks.get(session_id)
    if desk is None:
        desk = Desk(profile)
        _desks[session_id] = desk
    return desk


READBACK = {
    "password.reset": "reset your password",
    "password.reset_other": "reset the password on {target}",
    "mfa.reset": "start MFA re-enrollment on {target}",
    "account.disable": "disable {target} and revoke its sessions",
    "group.add_member": "add {target} to {group}",
    "device.enroll": "enroll device {serial}",
    "mailbox.add_forwarding": "forward {target}'s mail to {destination}",
}


def _sentence(action: str, args: dict[str, Any], profile: dict[str, Any]) -> str:
    target = str(args.get("target_account") or profile["username"])
    template = READBACK.get(action, action.replace(".", " ").replace("_", " "))
    return template.format(
        target=target,
        group=args.get("group") or "the group",
        serial=args.get("serial") or "that serial",
        destination=args.get("destination") or "that address",
    )


class ToolReq(BaseModel):
    session_id: str
    profile: dict[str, Any]
    name: str
    args: dict[str, Any] = {}
    enforced: bool = True


@app.post("/tool")
async def run_tool(req: ToolReq) -> dict[str, Any]:
    """Run one tool and hand back both the string the agent reads and the
    events the panel renders.

    No action-plane check runs here. `Session.check` raises
    NotImplementedError in this release, which covers analysis and nudge
    delivery, so every action is allowed and the record says why rather than
    showing a decision nothing made. The findings and nudges on the panel are
    real; a block would not be.
    """
    if req.name not in TOOL_NAMES:
        return {"result": f"Refused: no tool named {req.name}.", "events": []}

    desk = _desk(req.session_id, req.profile)
    events: list[dict[str, Any]] = []
    args = req.args or {}
    result = ""

    if req.name == "propose_action":
        action = str(args.get("action") or "")
        desk.pending = {"action": action, "args": args}
        sentence = _sentence(action, args, desk.profile)
        events.append(
            {
                "type": "policy",
                "decision": "allow",
                "blocked": False,
                "enforced": req.enforced,
                "control": None,
                "control_name": None,
                "sop": "",
                "sop_name": "",
                "step": 0,
                "severity": None,
                # Not a verdict. This release of the SDK analyzes and nudges;
                # deciding a single action is `Session.check`, which is not
                # implemented, so nothing evaluated this.
                "reason": "check_not_implemented",
                "resolution": "resolve_on_call",
                "message": (
                    "No action check ran. This example uses Session.analyze, "
                    "which watches the call; Session.check, which decides a "
                    "single action, is not implemented in this release."
                ),
                "action": action,
                "args": args,
                "fingerprint": uuid.uuid4().hex[:12],
                "checks": [],
                "warnings": [],
                "steps_completed": [],
                "steps_total": 0,
                "latency_ms": 0.0,
                "phase": "propose",
            }
        )
        result = (
            f"Read this back and wait for a yes: I'm going to {sentence}. "
            "Then call apply_action."
        )

    elif req.name == "apply_action":
        if desk.pending is None:
            result = "Refused: nothing proposed. Call propose_action first."
        else:
            action = str(desk.pending["action"])
            sentence = _sentence(action, desk.pending["args"], desk.profile)
            desk.pending = None
            events.append({"type": "executed", "action": action, "detail": sentence})
            result = f"Done: {sentence}."

    elif req.name == "open_ticket":
        # A reference this example minted, so the caller leaves the call with
        # something. It names no queue and promises no turnaround: both belong
        # to the service desk this is standing in for.
        if desk.ticket is None:
            desk.ticket = f"SD-{uuid.uuid4().hex[:6].upper()}"
            events.append(
                {
                    "type": "ticket",
                    "ticket": desk.ticket,
                    "reason": str(args.get("summary") or ""),
                }
            )
        result = f"Ticket {desk.ticket} raised. Give the caller the reference."

    elif req.name == "transfer_to_human":
        reason = str(args.get("reason") or "caller asked for a person")
        events.append({"type": "escalated", "reason": reason})
        result = "Warm transfer started. Stay on the line until someone picks up."

    # The browser holds both the caller and the conversation id, so a tool call
    # is the first chance the server gets to put the two together.
    analysis.register(req.session_id, user=_user(desk.profile), platform="elevenlabs")
    for event in events:
        analysis.note_event(req.session_id, event)
    return {"result": result, "events": events}


if __name__ == "__main__":
    uvicorn.run(
        app, host="127.0.0.1", port=int(os.getenv("PORT", "8102")), log_level="info"
    )
