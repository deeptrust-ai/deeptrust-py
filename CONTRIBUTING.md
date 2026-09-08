# Contributing

```bash
just install     # uv sync, adapters and dev tools included
just check       # lint, types, tests. what CI runs
```

`just` on its own lists everything else.

## What goes where

`src/deeptrust/types.py` is the vocabulary, and it is shared with the human-led
product on purpose. A finding, a nudge and a control id mean the same thing in
both, because the alternative is two products that cannot share a dashboard or
an evidence record. Changing a shape here is a bigger decision than it looks.

`src/deeptrust/_http.py` is transport and stays dull. It is the layer a code
generator would own if we go spec-first for other languages.

`src/deeptrust/agents/` is the surface customers hold. Two verbs, and adapters
that are thin enough to read in one sitting.

## Adapters

An adapter translates a platform's events into turns and a nudge into whatever
that platform accepts. It does not interpret findings and it does not decide
policy. When a platform cannot do something, say so in the adapter rather than
emulating it: contextual updates on ElevenLabs cannot interrupt, so that
adapter shapes the next turn and the docstring explains why.

Each adapter is an optional extra. Someone integrating their own stack should
not be made to install a platform SDK they do not use.

## Tests

Platform SDKs are faked, never imported in tests. The tests pin the contract:
what goes on the wire, what comes back, and what happens when a key is wrong.
