# deeptrust-python

QA and runtime nudges for voice agents.

Your agent runs wherever it already runs. This client sends the transcript as
it happens, gets back what the analysis found, and delivers the nudge to the
agent while the caller is still on the line.

## Two ways in

**Connect, no code.** If your agents run on ElevenLabs, connect the workspace
once in the DeepTrust dashboard (Settings, Voice Agents) with an API key that
has the ElevenLabs Agents Write permission, and pick the agents to watch.
DeepTrust finds their calls, listens along, nudges the agent mid-call, and
records the transcript. Nothing to install and nothing in this package to run.
Phone calls are picked up at call setup; other channels within a few seconds.

**This client, in your process.** For your own stack, for LiveKit, or when you
want the socket held by you rather than by DeepTrust. Append turns, call
`analyze`, deliver the nudge. The rest of this README is about this path.

The two meet in the same place: every call, either way, lands in the Calls list
with the `voice_agent` source.

```bash
pip install deeptrust-ai
```

The distribution is `deeptrust-ai` and it imports as `deeptrust`.

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
controls, and returns findings. A finding worth telling the agent about carries
a nudge, which has both what was seen and what to do about it. An agent given
only the first has to pick a response itself, and the one it usually picks is
handing the call to a person.

## Three methods

`analyze` reviews the transcript and returns findings. It does not block the
agent, so a result arrives after the turn that caused it has been spoken, and a
nudge affects what the agent says next.

`end` tells DeepTrust the call is over, so post-call processing starts now
rather than after the server's inactivity timeout. Calling it twice is harmless.

```python
await call.end()
```

`check` decides whether a single action may run, and does block. It is meant to
be called from a tool handler before the action executes. Not implemented in
this version.

## The transcript is turns

An agent call has two participants with fixed roles, so every turn has an
unambiguous speaker and the transcript stays structured rather than flattened
to prose.

`append` only adds to a local list. Nothing is sent until `analyze` is called,
and `analyze` returns `None` when no turns have been added since the last one,
so it is safe to call on every turn.

```python
call.append("user", "I'm locked out")
call.pending          # 1
await call.analyze()  # one job, the whole transcript
await call.analyze()  # None. nothing new was said
```

## LiveKit

```bash
pip install "deeptrust-ai[livekit]"
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
pip install "deeptrust-ai[elevenlabs]"
```

```python
from deeptrust.agents import DeepTrust
from deeptrust.agents.elevenlabs import Monitor

monitor = Monitor(DeepTrust(), api_key=os.environ["ELEVENLABS_API_KEY"])
await monitor.watch(conversation_id, user=caller)
```

This needs no code inside your agent. ElevenLabs exposes a per-conversation
monitor socket, so this connects to it from your process with your workspace
key, reads the transcript, and sends findings back as contextual updates on the
same socket.

Two differences from LiveKit, which the client reports rather than hides.
Contextual updates are documented as non-interrupting, so a finding shapes the
next turn. And the socket carries events, not audio, which suits a client that
reads what was said and does not analyse the audio itself.

### Or let DeepTrust hold the socket

If the workspace is connected in the dashboard, you do not need `Monitor` or an
ElevenLabs key here at all. DeepTrust finds live calls on its own. When your
backend already knows a conversation id, for instance from the
`conversation_initiation_metadata` client event, hand it over and the call is
watched from its first turn instead of from the next check:

```python
from deeptrust.agents import DeepTrust

await DeepTrust().watch(conversation_id)          # platform="elevenlabs"
```

`watch` returns `True` when it started the monitor and `False` when DeepTrust
was already watching. It raises `ServiceError` with status 404 when the
platform is not connected for your organisation.

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

The key is sent as `X-DeepTrust-Api-Key`. The default base URL is
`https://app.deeptrust.ai/api/v1`; a `DEEPTRUST_BASE_URL` from 0.0.1 that ends
in `/api` needs `/v1` appended.

## Development

```bash
just install
just check      # lint, types, tests
```

Everything runs through [uv](https://docs.astral.sh/uv/), so there is no
virtualenv to activate. `just` on its own lists the rest.

## Local development

`dev/server.py` is a local stand-in for the API, so this client, both adapters
and both examples run with no key and no network:

```bash
just devserver     # http://127.0.0.1:8080
```

It is not the analysis. The hosted API runs a reasoning model against an
organisation's runbook, SOPs and controls; this matches a handful of patterns,
which is enough to see a finding arrive and a nudge get delivered. A rule can
never separate a caller relaying a real approval from one inventing it, which
is the whole reason the real thing is not this.

Point a client at it with `DEEPTRUST_BASE_URL`.

## Status

`0.0.2`. `analyze`, `end`, `watch` and both adapters work against the hosted
API. `check` is defined and raises `NotImplementedError`. The shapes in
`deeptrust.types` are the part most likely to move.

Changes since 0.0.1: the key travels in `X-DeepTrust-Api-Key` (the bearer form
is still sent, and goes away in 0.1); the default base URL gained `/v1`; the
ElevenLabs adapter sends contextual updates in the monitor socket's command
envelope, which 0.0.1 got wrong, so its nudges never arrived; `Session.end`
and `DeepTrust.watch` are new.

Apache 2.0.
