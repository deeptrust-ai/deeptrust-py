"""LiveKit adapter.

    from deeptrust.agents import DeepTrust, Caller
    from deeptrust.agents.livekit import attach

    dt = DeepTrust()
    attach(
        session,
        dt,
        external_id=ctx.room.name,
        caller=Caller(id=account_id, role="MEMBER"),
    )

`attach` subscribes to the AgentSession's conversation items, runs a job when
the caller has said something new, and delivers any nudge to the agent.

On LiveKit a nudge can interrupt. The analysis lands while the agent is still
generating, so a finding about coercion can stop a sentence on its way out
rather than correcting it afterwards. That is not true on every platform, so
`interrupt` is a parameter and the record says which happened.

Install with the extra:  pip install "deeptrust[livekit]"
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..types import Caller, Nudge
from . import DeepTrust


def attach(
    agent_session: Any,
    dt: DeepTrust,
    *,
    external_id: str,
    caller: Caller | None = None,
    interrupt: bool = True,
    on_analysis: Callable[[Any], None] | None = None,
) -> Any:
    """Wire a LiveKit AgentSession to DeepTrust. Returns the DeepTrust session.

    Analysis runs on caller turns only. Feeding an agent's own replies back in
    doubles the work and lets its answers reclassify the call.
    """
    call = dt.session(external_id=external_id, caller=caller, platform="livekit")
    tasks: set[asyncio.Task[None]] = set()

    def _spawn(coro: Any) -> None:
        # asyncio keeps only weak references to tasks, so an unheld task can be
        # collected while it is still running.
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    async def _deliver(nudge: Nudge) -> None:
        text = nudge.render()
        chat = agent_session.current_agent.chat_ctx.copy()
        chat.add_message(role="system", content=text)
        await agent_session.current_agent.update_chat_ctx(chat)
        if interrupt:
            agent_session.interrupt()
            agent_session.generate_reply(instructions=text)

    async def _run(role: str, text: str) -> None:
        call.append(role, text)
        if role != "user":
            return
        result = await call.analyze()
        if result is None:
            return
        if on_analysis:
            on_analysis(result)
        for nudge in result.nudges:
            await _deliver(nudge)

    def _on_item(ev: Any) -> None:
        item = getattr(ev, "item", None)
        text = getattr(item, "text_content", None) or ""
        if not text:
            return
        role = str(getattr(item, "role", "unknown"))
        _spawn(_run("user" if role == "user" else "agent", text))

    # Registered by call rather than by decoration: AgentSession.on is untyped
    # on LiveKit's side, and decorating with it erases our own signature.
    agent_session.on("conversation_item_added")(_on_item)

    return call
