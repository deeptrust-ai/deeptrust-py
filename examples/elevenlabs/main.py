"""Watch live ElevenLabs conversations with DeepTrust.

Nothing here runs inside the agent. ElevenLabs exposes a monitor socket per
conversation, so this connects to it with a workspace API key, reads the
transcript as it happens, and sends nudges back as contextual updates on the
same socket. The agent needs no code change at all.

Two ways to run it:

    uv run python main.py watch <conversation_id>
        Watch one conversation that is already running.

    uv run python main.py serve
        Run the webhook receiver. Point the ElevenLabs conversation initiation
        webhook at http://<host>/calls and every inbound call is watched
        automatically.
"""

import asyncio
import os
import sys

from deeptrust.agents import DeepTrust, User
from deeptrust.agents.elevenlabs import Monitor
from dotenv import load_dotenv

load_dotenv()


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
    # watch() returns as soon as the watcher is running, so hold the process
    # open while it reads the socket.
    while True:
        await asyncio.sleep(1)


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
    if command == "watch" and len(sys.argv) > 2:
        asyncio.run(watch_one(sys.argv[2]))
    elif command == "serve":
        serve()
    else:
        print(__doc__)
        raise SystemExit(1)
