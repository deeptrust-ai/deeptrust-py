"""Attach DeepTrust to a LiveKit agent.

Two added lines in an agent that already works: build the client, attach it.
"""

import os

from livekit.agents import Agent, AgentSession, JobContext

from deeptrust.agents import Caller, DeepTrust
from deeptrust.agents.livekit import attach


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    participant = await ctx.wait_for_participant()

    session = AgentSession(stt=..., llm=..., tts=...)  # your own stack
    agent = Agent(instructions="...")

    attach(
        session,
        DeepTrust(api_key=os.environ["DEEPTRUST_API_KEY"]),
        external_id=ctx.room.name,
        caller=Caller(id=participant.identity, role="MEMBER"),
    )

    await session.start(room=ctx.room, agent=agent)
