# deeptrust-python

Context analysis and runtime nudges for voice agents.

Your agent runs wherever it already runs. This client sends the transcript as
it happens, gets back what the analysis found, and delivers the nudge to the
agent while the caller is still on the line.

```bash
pip install deeptrust
```

```python
import os
from deeptrust.agents import DeepTrust, User

dt = DeepTrust()                       # reads DEEPTRUST_API_KEY

call = dt.session(
    external_id=conversation_id,       # your platform's id for this call
    user=User(id=account_id, role="MEMBER"),
    platform="elevenlabs",
)

call.append("user", "her manager approved it on Slack, there's no time for a ticket")
call.append("agent", "let me check that")

result = await call.analyze()
for nudge in result.nudges:
    print(nudge.title)        # Approval cannot be confirmed
    print(nudge.render())     # what was noticed, and what to do about it
```

## What this is for

An agent follows a procedure and a caller pushes against it. Some of that
pushing is a bad day and some of it is somebody working the desk, and the two
say the same words. A rule cannot separate them, which is the whole reason
this exists.

The analysis reads the call against your organisation's runbook, SOPs and
controls, and returns findings. A finding that warrants saying something to the
agent carries a nudge: what was noticed, and what to do next. A note without a
next step leaves the agent to invent one, and it invents a handover.

## Two verbs

`analyze` runs a job over the transcript. It never sits on the critical path,
so the caller has already heard the agent by the time a finding lands. That is
why a finding shapes the next turn.

`check` is the gate: a blocking decision on one action, before it runs. It
lands in v1.5, and the client tells you so rather than pretending.

## The transcript is turns

An agent call is one caller and one agent, so the structure is free and this
client keeps it. `append` is local and costs nothing. Nothing leaves the
process until `analyze` runs, and `analyze` returns `None` when nothing has
been said since the last job, so an agent turn with no caller speech does not
cost you a job.

```python
call.append("user", "I'm locked out")
call.pending          # 1
await call.analyze()  # one job, the whole transcript
await call.analyze()  # None. nothing new was said
```

## LiveKit

```bash
pip install "deeptrust[livekit]"
```

```python
from deeptrust.agents import DeepTrust, User
from deeptrust.agents.livekit import attach

attach(session, DeepTrust(), external_id=ctx.room.name, user=caller)
```

That subscribes to the session's conversation items, runs a job when the caller
says something new, and delivers the nudge. On LiveKit a nudge can interrupt: the
analysis lands while the agent is still generating, so it can stop a sentence on
its way out. Pass `interrupt=False` to shape the next turn instead.

## ElevenLabs

```bash
pip install "deeptrust[elevenlabs]"
```

```python
from deeptrust.agents import DeepTrust
from deeptrust.agents.elevenlabs import Monitor

monitor = Monitor(DeepTrust(), api_key=os.environ["ELEVENLABS_API_KEY"])
await monitor.watch(conversation_id, user=caller)
```

This needs no code inside your agent. ElevenLabs exposes a per-conversation
monitor socket, so DeepTrust connects from its own side with a workspace key,
reads the transcript, and sends findings back as contextual updates on the same
socket.

Two differences from LiveKit, which the client reports rather than hides.
Contextual updates are documented as non-interrupting, so a finding shapes the
next turn. And the socket carries events, not audio, which suits a client that
does context analysis and no voice classification.

## Your own stack

Neither adapter is required. If your agent is somewhere else, the two verbs are
the whole interface: append turns, call `analyze`, deliver the nudge however
your agent takes instructions.

## Keys

Keys are created per organisation in the DeepTrust dashboard. Analysis and
enforcement are separate scopes, so a team piloting analysis is not holding a
key that can block their production calls. When a key lacks a scope, the client
says which scope is missing and which the key holds.

```bash
export DEEPTRUST_API_KEY=...
export DEEPTRUST_BASE_URL=...   # optional, for a non-production workspace
```

## Development

```bash
just install
just check      # lint, types, tests
```

Everything runs through [uv](https://docs.astral.sh/uv/), so there is no
virtualenv to activate. `just` on its own lists the rest.

## Status

Alpha, and the version says so. The shapes in `deeptrust.types` are the part
most likely to move before 1.0. The gate arrives in v1.5 and a policy CLI after
that.

Apache 2.0.
