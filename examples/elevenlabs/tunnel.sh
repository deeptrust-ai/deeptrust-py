#!/bin/bash
# Start the live server and an ngrok tunnel, then print the URL to paste into
# ElevenLabs: Workspace Settings -> Conversation Initiation Client Data Webhook.
set -uo pipefail
PORT="${PORT:-8095}"
cd "$(dirname "$0")"

uv run python live.py &
SERVER=$!
trap 'kill $SERVER $NGROK 2>/dev/null' EXIT

ngrok http "$PORT" --log stdout --log-format json > /tmp/dt-ngrok.log 2>&1 &
NGROK=$!

for _ in $(seq 1 40); do
  URL=$(grep -o 'https://[a-z0-9-]*\.ngrok[a-z.-]*' /tmp/dt-ngrok.log 2>/dev/null | head -1)
  [ -n "$URL" ] && break
  sleep 0.5
done

if [ -z "${URL:-}" ]; then
  echo "ngrok did not report a URL. Its log:"
  tail -20 /tmp/dt-ngrok.log
  exit 1
fi

cat <<TXT

  UI          http://localhost:$PORT
  Webhook     $URL/calls     <- paste this into ElevenLabs

  Watch a conversation you already have:
    curl -X POST $URL/calls/<conversation_id>

TXT
wait $SERVER
