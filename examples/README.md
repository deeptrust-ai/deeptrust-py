# Examples

Each folder is a self-contained project with its own `pyproject.toml` and a
README showing real output from a real run.

| | |
|---|---|
| [`livekit/`](livekit) | An agent with DeepTrust attached in one line. Nudges can interrupt a reply in progress. |
| [`elevenlabs/`](elevenlabs) | An agent it provisions for you, watched from outside. No code inside the agent at all. |

Both resolve `deeptrust-ai` from this checkout rather than the published
package, so they exercise the code in this repo:

```toml
[tool.uv.sources]
deeptrust-ai = { path = "../..", editable = true }
```

Both read `DEEPTRUST_BASE_URL`, so `just devserver` in the repo root is enough
to run either one end to end with no DeepTrust key and no network.
