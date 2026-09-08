"""A LiveKit agent with DeepTrust attached.

Everything below the `attach` call is an ordinary LiveKit agent. `attach` is the
only DeepTrust-specific line, and it wires both directions: caller turns go out
for analysis, and any nudge that comes back is delivered to the agent.

Run with:  uv run python main.py dev
"""

import os

from deeptrust.agents import DeepTrust, User
from deeptrust.agents.livekit import attach
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import deepgram, openai

load_dotenv()

INSTRUCTIONS = """
You are an IT service desk agent for Meridian Industrial Group.

You reset passwords, re-enroll MFA, and unlock accounts. Confirm who the caller
is before changing anything on an account.

Keep replies to one or two sentences. You are on a phone call, so do not read
out lists and do not explain internal procedure.
"""


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()
    participant = await ctx.wait_for_participant()

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM.with_together(
            model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            api_key=os.environ["TOGETHER_API_KEY"],
        ),
        tts=deepgram.TTS(model="aura-2-thalia-en"),
    )
    agent = Agent(instructions=INSTRUCTIONS)

    # The one DeepTrust line. Returns the session, so the transcript and the
    # findings stay reachable if this example wants to print them.
    call = attach(
        session,
        DeepTrust(),
        external_id=ctx.room.name,
        agent=agent,
        user=User(id=participant.identity, role="MEMBER", verified=False),
        on_analysis=_report,
    )
    print(f"DeepTrust attached to room {ctx.room.name}", flush=True)

    await session.start(room=ctx.room, agent=agent)
    await session.generate_reply(
        instructions="Greet the caller as the IT service desk in one sentence."
    )
    return call


def _report(result) -> None:
    """Optional. `attach` already delivers nudges to the agent; this only prints
    what came back so the example shows its work."""
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


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
