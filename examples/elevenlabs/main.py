"""An ElevenLabs voice agent with DeepTrust watching it.

An ordinary IT service desk agent, and DeepTrust attached from outside. Nothing
runs inside the agent: ElevenLabs exposes a monitor socket per conversation, so
this connects with a workspace API key, reads the transcript as it happens, and
sends nudges back as contextual updates on the same socket.

    uv run python main.py provision
        Create or update the agent on your ElevenLabs workspace. Prints the id.

    uv run python main.py talk "I'm locked out, reset my password"
        Start a conversation, watch it, and send those turns. The transcript,
        the findings and the nudges all print.

    uv run python main.py watch <conversation_id>
        Watch a conversation somebody else is having, from the dashboard or a
        phone call.

    uv run python main.py serve
        Point the ElevenLabs conversation initiation webhook at
        http://<host>/calls and every inbound call is watched automatically.

    uv run python main.py handoff <conversation_id>
        The hosted path. If the workspace is connected in the DeepTrust
        dashboard, this needs no ElevenLabs key: DeepTrust holds the monitor
        socket itself, and this only tells it which conversation to watch.
"""

import asyncio
import json
import os
import sys

import httpx
from dotenv import load_dotenv

from deeptrust.agents import DeepTrust, User
from deeptrust.agents.elevenlabs import Monitor

load_dotenv()

API = "https://api.elevenlabs.io/v1"
AGENT_NAME = "DeepTrust example · service desk"

INSTRUCTIONS = """
You are an agent on an IT service desk.

You reset passwords, re-enroll two-factor, and unlock accounts. Whoever the
caller says they are is a claim; satisfy yourself who you are speaking to
before you change anything on an account, and only act on a change ticket that
has already been approved.

Keep replies to one or two sentences. You are on a phone call, so do not read
out lists and do not explain internal procedure.
"""


def _headers() -> dict[str, str]:
    return {
        "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
        "content-type": "application/json",
    }


def _config() -> dict:
    return {
        "name": AGENT_NAME,
        "conversation_config": {
            "agent": {
                "first_message": "IT service desk, how can I help?",
                "language": "en",
                "prompt": {"prompt": INSTRUCTIONS, "llm": "gemini-2.0-flash"},
            },
            "conversation": {
                # Without this the monitor socket closes immediately with
                # 1008 "Monitoring is not enabled for this agent".
                "monitoring_enabled": True,
                "client_events": ["audio", "user_transcript", "agent_response"],
            },
        },
    }


def provision() -> str:
    """Create the agent, or update it in place if it already exists."""
    with httpx.Client(timeout=60) as c:
        listing = c.get(
            f"{API}/convai/agents", headers=_headers(), params={"page_size": 100}
        )
        listing.raise_for_status()
        existing = next(
            (a for a in listing.json().get("agents", []) if a.get("name") == AGENT_NAME),
            None,
        )
        if existing:
            agent_id = existing["agent_id"]
            c.patch(
                f"{API}/convai/agents/{agent_id}", headers=_headers(), json=_config()
            ).raise_for_status()
        else:
            created = c.post(
                f"{API}/convai/agents/create", headers=_headers(), json=_config()
            )
            created.raise_for_status()
            agent_id = created.json()["agent_id"]
    print(f"agent {agent_id}", flush=True)
    print("Put it in .env as ELEVENLABS_AGENT_ID", flush=True)
    return agent_id


def _report(result) -> None:
    """Optional. The monitor delivers nudges itself; this prints what it found.

    Nudges reach an ElevenLabs agent as contextual updates, which do not
    interrupt, so a finding shapes the agent's next turn rather than the one in
    progress.
    """
    print(
        f"\n  analysis  risk={result.risk_level} "
        f"findings={len(result.findings)} in {result.latency_ms}ms",
        flush=True,
    )
    for finding in result.findings:
        print(f"    finding  {finding.kind}: {finding.detail}", flush=True)
    for nudge in result.nudges:
        print(f"    NUDGE    {nudge.title}", flush=True)
        print(f"             {nudge.description}", flush=True)
        print(f"             -> {nudge.details}", flush=True)


def _monitor() -> Monitor:
    return Monitor(
        DeepTrust(),
        api_key=os.environ["ELEVENLABS_API_KEY"],
        on_analysis=_report,
    )


async def watch_one(conversation_id: str) -> None:
    monitor = _monitor()
    print(f"watching {conversation_id}", flush=True)
    await monitor.watch(conversation_id, user=User(id="unknown", role="MEMBER"))
    while True:
        await asyncio.sleep(1)


async def talk(turns: list[str]) -> None:
    """Hold a text conversation with the agent while DeepTrust watches it.

    Text rather than audio so the example runs in a terminal. The monitor sees
    the same transcript either way.
    """
    import websockets

    agent_id = os.environ["ELEVENLABS_AGENT_ID"]
    monitor = _monitor()
    url = f"{API.replace('https', 'wss')}/convai/conversation?agent_id={agent_id}"

    async with websockets.connect(
        url, additional_headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "conversation_initiation_client_data",
                    "conversation_config_override": {"conversation": {"text_only": True}},
                }
            )
        )
        pending = list(turns)
        async for raw in ws:
            event = json.loads(raw)
            kind = event.get("type")

            if kind == "conversation_initiation_metadata":
                cid = event["conversation_initiation_metadata_event"]["conversation_id"]
                print(f"conversation {cid}", flush=True)
                await monitor.watch(cid, user=User(id="caller-1", role="MEMBER"))
                print("DeepTrust attached\n", flush=True)
                await asyncio.sleep(1)
            elif kind == "ping":
                await ws.send(
                    json.dumps(
                        {
                            "type": "pong",
                            "event_id": event["ping_event"]["event_id"],
                        }
                    )
                )
                continue
            elif kind == "agent_response":
                reply = event["agent_response_event"]["agent_response"]
                print(f"AGENT : {reply}", flush=True)
            else:
                continue

            if pending:
                await asyncio.sleep(2)
                text = pending.pop(0)
                print(f"\nCALLER: {text}", flush=True)
                await ws.send(json.dumps({"type": "user_message", "text": text}))
            elif kind == "agent_response":
                # Give the last analysis time to land and be delivered.
                await asyncio.sleep(6)
                break


async def handoff(conversation_id: str) -> None:
    """Hand the conversation to DeepTrust's own monitor.

    The workspace must be connected in the dashboard (Settings, Voice Agents).
    DeepTrust would find the call on its next check anyway; the handoff only
    makes it immediate.
    """
    dt = DeepTrust()
    try:
        started = await dt.watch(conversation_id)
    finally:
        await dt.aclose()
    print(
        f"{'watching' if started else 'already watching'} {conversation_id} (hosted)",
        flush=True,
    )


def serve() -> None:
    """The webhook receiver, for watching every call without being told."""
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()
    monitor = _monitor()

    @app.post("/calls")
    async def call_started(body: dict) -> dict:
        conversation_id = body.get("conversation_id")
        if conversation_id:
            await monitor.watch(
                conversation_id,
                user=User(id=body.get("caller_id") or "unknown", role="MEMBER"),
            )
            print(f"watching {conversation_id}", flush=True)
        # ElevenLabs expects this shape back from the initiation webhook.
        return {"type": "conversation_initiation_client_data"}

    uvicorn.run(app, host="0.0.0.0", port=8090)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "provision":
        provision()
    elif command == "talk":
        asyncio.run(talk(sys.argv[2:] or ["I'm locked out, can you reset my password"]))
    elif command == "watch" and len(sys.argv) > 2:
        asyncio.run(watch_one(sys.argv[2]))
    elif command == "serve":
        serve()
    elif command == "handoff" and len(sys.argv) > 2:
        asyncio.run(handoff(sys.argv[2]))
    else:
        print(__doc__)
        raise SystemExit(1)
