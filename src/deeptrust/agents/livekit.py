"""LiveKit adapter.

    from deeptrust.agents import DeepTrust, User
    from deeptrust.agents.livekit import attach

    dt = DeepTrust()
    attach(
        session,
        dt,
        external_id=ctx.room.name,
        user=User(id=account_id, role="MEMBER"),
    )

`attach` subscribes to the session's conversation items, analyzes the
transcript when the user has said something new, and delivers any nudge to the
agent.

A nudge is added to the agent's chat context, and by default also interrupts:
because the agent runs in this process, a nudge can arrive while it is still
generating and stop a reply part-way through. Pass `interrupt=False` to add the
nudge to the context and let the current reply finish.

Install with the extra:  pip install "deeptrust-ai[livekit]"
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..types import Nudge, User
from . import DeepTrust


def attach(
    agent_session: Any,
    dt: DeepTrust,
    *,
    external_id: str,
    agent: Any = None,
    user: User | None = None,
    interrupt: bool = True,
    on_analysis: Callable[[Any], None] | None = None,
) -> Any:
    """Wire a LiveKit AgentSession to DeepTrust. Returns the DeepTrust session.

    Analysis runs on caller turns only. Feeding an agent's own replies back in
    doubles the work and lets its answers reclassify the call.

    Pass `agent` if it is available. Otherwise it is read from
    `agent_session.current_agent`.

    Returns the DeepTrust session, so the transcript and findings remain
    reachable.
    """
    call = dt.session(external_id=external_id, user=user, platform="livekit")
    tasks: set[asyncio.Task[None]] = set()

    def _spawn(coro: Any) -> None:
        # asyncio holds only a weak reference to a task, so a task nobody keeps
        # can be garbage collected mid-flight. Hence the set.
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    async def _deliver(nudge: Nudge) -> None:
        text = nudge.render()
        target = agent or getattr(agent_session, "current_agent", None)
        if target is None:
            return
        chat = target.chat_ctx.copy()
        chat.add_message(role="system", content=text)
        await target.update_chat_ctx(chat)
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

    # The last turn taken, so a repeat of it can be recognised. LiveKit emits a
    # conversation item more than once for the same speech in practice, and a
    # transcript that carries the same sentence twice is analysed twice: the
    # same finding is reported again and the same nudge delivered again.
    last: dict[str, str] = {}

    def _on_item(ev: Any) -> None:
        item = getattr(ev, "item", None)
        text = getattr(item, "text_content", None) or ""
        if not text:
            return
        role = "user" if str(getattr(item, "role", "unknown")) == "user" else "agent"

        # Compared against the previous turn only, not the whole transcript: a
        # caller who says the same thing again later in the call means it, and
        # that repetition is itself worth analysing.
        if last.get(role) == text:
            return
        last[role] = text

        _spawn(_run(role, text))

    # Registered by call rather than by decoration: AgentSession.on is untyped
    # on LiveKit's side, and decorating with it erases our own signature.
    agent_session.on("conversation_item_added")(_on_item)

    return call
