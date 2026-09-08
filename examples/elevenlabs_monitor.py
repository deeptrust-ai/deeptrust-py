"""Watch live ElevenLabs conversations.

Nothing here runs inside the agent. This is a service beside it, so the agent
needs no change at all. Point the ElevenLabs conversation initiation webhook at
/calls, or call watch() with the id an outbound call hands back.
"""

import os

from fastapi import FastAPI

from deeptrust.agents import Caller, DeepTrust
from deeptrust.agents.elevenlabs import Monitor

app = FastAPI()
monitor = Monitor(
    DeepTrust(api_key=os.environ["DEEPTRUST_API_KEY"]),
    api_key=os.environ["ELEVENLABS_API_KEY"],
)


@app.post("/calls")
async def call_started(body: dict) -> dict:
    await monitor.watch(
        body["conversation_id"],
        caller=Caller(id=body.get("caller_id", "unknown")),
    )
    # ElevenLabs expects this shape back from the initiation webhook.
    return {"type": "conversation_initiation_client_data"}
