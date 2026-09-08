# Contributing

```bash
just install     # uv sync, adapters and dev tools included
just check       # lint, types, tests. what CI runs
```

`just` on its own lists everything else.

## What goes where

`src/deeptrust/types.py` holds the request and response shapes. They match the
API's own vocabulary, so a field renamed here is an API change rather than a
local one.

`src/deeptrust/_http.py` is transport only: base URL, auth header, retries, and
turning error responses into exceptions. It knows nothing about analyses or
verdicts.

`src/deeptrust/agents/` is the public surface. Two methods, `analyze` and
`check`, plus the adapters.

## Adapters

An adapter translates a platform's events into turns, and a nudge into whatever
that platform accepts. It should not interpret findings or make policy
decisions.

Where a platform cannot do something, document the limit rather than emulating
it. ElevenLabs contextual updates cannot interrupt a reply, so that adapter
affects the next turn and its docstring says so.

Each adapter is an optional extra, so installing the library does not pull in a
platform SDK that will not be used.

## Tests

Platform SDKs are faked rather than imported, so the suite runs without them
and without network access. The tests cover the request body, the parsing of
responses, and the exception raised for each error status.
