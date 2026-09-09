"""A local convenience for DeepTrust developers. Not part of the SDK.

This file exists for one situation: a DeepTrust engineer running the demo
against a local backend, who wants to watch nudges appear in the panel while
they work on the analysis pipeline.

Real analysis finishes in a worker, after the `analyze()` that dispatched it
has already returned. So the demo's SSE stream shows nothing for a real call:
`session.analyze()` answers with the nudges of *previous* dispatches, and on
the first turns of a call there are none. In production that gap is not the
browser's problem, because **delivery is the backend's job** -- it holds the
org's platform credential and pushes the nudge into the LiveKit room or the
ElevenLabs conversation itself. Nothing in the browser or in this repo is
needed for a nudge to reach an agent.

This module is a shortcut around that for local work only: given a database
URL, it polls the `notifications` table for the call and puts each new row on
the SSE stream the panel already reads. It is:

  * **off unless `DEEPTRUST_DEV_DB_URL` is set.** With it unset, nothing here
    runs, nothing is imported, and the demo behaves exactly as it does for a
    contributor who has no database -- which is most people reading this.
  * **not an integration.** No customer runs this. No published code path
    reaches it. If you are looking for how nudges get to an agent, the answer
    is the backend, not this file.
  * **not a fallback.** If it stops working, nothing a customer uses is
    affected.

It also needs a postgres driver, which the examples do not depend on. If
`asyncpg` is not installed the poller declines to start and says so once. That
is deliberate: adding a database driver to an example that does not need one
would make every contributor install it to run a demo that never queries a
database.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from typing import Any

# The one switch. Anything falsy -- unset or empty -- and this module is inert.
DB_URL_ENV = "DEEPTRUST_DEV_DB_URL"

# How often to look for new rows. A nudge is worth seeing within a turn, and
# the query is a single indexed lookup, so this is cheap enough to leave alone.
POLL_INTERVAL_S = float(os.getenv("DEEPTRUST_DEV_DB_POLL_S", "1.0"))

# Only rows the backend routed to the agent. `notification_type = 'nudge'`
# separates advice-for-the-call from ALERT rows, which are reports to a
# security team; `destination = 'agent'` is written only by the voice-agent
# branch of the nudge router, so a human's Slack DM can never turn up here.
# This is the same filter `/agents/analyze` applies -- it has to be, or the
# panel would show a different set of nudges than the agent was given.
_QUERY = """
    SELECT n.id, n.title, n.message, n.details, n.source, n.severity
      FROM notifications n
      JOIN calls c ON c.id = n.call_id
     WHERE c.platform_call_id = $1
       AND c.source = 'voice_agent'
       AND n.notification_type = 'nudge'
       AND n.destination = 'agent'
       AND n.id > $2
     ORDER BY n.id
"""

_warned: set[str] = set()


def enabled() -> bool:
    """Whether a dev database URL was supplied."""
    return bool(os.getenv(DB_URL_ENV))


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        # No URL, no key, no query text. A connection string carries a
        # password, and this is the one place tempted to print one.
        print(f"[dev-nudge-db] {message}", flush=True)


def _row_event(row: Any) -> dict[str, Any]:
    """One notifications row in the shape `Panel.tsx` already renders.

    Deliberately the same keys `analysis._record` emits for a nudge, so the
    panel needs no new arm and the row is indistinguishable from one that
    arrived the ordinary way. `note`/`next_step` are the description and the
    action, and `nudge` is the two joined -- which is what `Nudge.render()`
    produces and what the agent is actually told.

    `latency_ms` and `queue_lag_ms` are present and zero because the panel's
    expander prints them unconditionally and renders `undefinedms` otherwise.
    Zero is also honest: this row was not produced by the request that is
    showing it, so there is no latency to attribute to one.
    """
    description = row["message"] or ""
    details = row["details"] or ""
    return {
        "type": "nudge",
        "analyzer": "deeptrust",
        "kind": row["source"] or "nudge",
        "detail": description,
        "risk_level": row["severity"],
        "confidence": None,
        "title": row["title"] or None,
        "note": description or None,
        "next_step": details or None,
        "nudge": " ".join(p for p in (description, details) if p).strip(),
        "role": "user",
        "latency_ms": 0.0,
        "queue_lag_ms": 0.0,
        "fanout_ms": 0.0,
        # Marks the row's provenance for anyone debugging the panel. The UI
        # does not read it; extra keys pass through unread.
        "dev_db": True,
    }


async def poll(session_id: str, emit: Callable[[dict[str, Any]], None]) -> None:
    """Put every new agent nudge for `session_id` on the stream, forever.

    `session_id` is the *platform's* id for the call -- a LiveKit room name or
    an ElevenLabs conversation id -- which is what the demo keys its streams
    by and what the backend stored in `calls.platform_call_id`. Matching on
    that rather than on our own call id means the poller works from the moment
    the browser subscribes, before any `analyze()` has come back with an id.

    Returns immediately when disabled or when no driver is installed. Runs
    until cancelled; a query that fails is retried on the next tick, because
    the usual cause is a database that has not started yet.
    """
    url = os.getenv(DB_URL_ENV)
    if not url:
        return

    try:
        import asyncpg
    except ImportError:
        _warn_once(
            "driver",
            f"{DB_URL_ENV} is set but asyncpg is not installed, so live nudges "
            "from the database are off. `uv add asyncpg` in this example to "
            "enable them.",
        )
        return

    last_id = 0
    connection = None
    try:
        while True:
            try:
                if connection is None or connection.is_closed():
                    connection = await asyncpg.connect(url)
                rows = await connection.fetch(_QUERY, session_id, last_id)
                for row in rows:
                    last_id = max(last_id, row["id"])
                    emit(_row_event(row))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Type only. An asyncpg connection error stringifies the DSN,
                # and the DSN has the password in it.
                _warn_once(
                    "query",
                    f"could not read nudges from the dev database "
                    f"({type(exc).__name__}); will keep retrying quietly.",
                )
                if connection is not None:
                    stale, connection = connection, None
                    # Already failing; a failure to close the failed connection
                    # says nothing further and must not mask the retry.
                    with contextlib.suppress(Exception):
                        await stale.close()
            await asyncio.sleep(POLL_INTERVAL_S)
    finally:
        if connection is not None:
            with contextlib.suppress(Exception):
                await connection.close()
