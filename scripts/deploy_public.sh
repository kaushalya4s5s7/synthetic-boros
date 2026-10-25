#!/usr/bin/env bash
# Public read-only view: website on Vercel, bot stays on this laptop (its IP is the one whitelisted on Delta).
#   Vercel website  ->  Cloudflare quick tunnel  ->  localhost:8000 (bot)  ->  Delta
# Requests through the tunnel are read-only (apps/api/main.py). Keep this terminal open during the demo.
set -euo pipefail
cd "$(dirname "$0")/.."

curl -sf localhost:8000/api/health >/dev/null || { echo "Start the backend first: ./.venv/bin/python -m uvicorn main:app --port 8000"; exit 1; }

LOG=$(mktemp)
cloudflared tunnel --url http://localhost:8000 >"$LOG" 2>&1 &
TUNNEL_PID=$!
trap 'kill $TUNNEL_PID 2>/dev/null' EXIT
for _ in $(seq 1 30); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "${URL:-}" ] && break; sleep 1
done
[ -n "${URL:-}" ] || { echo "Tunnel did not start:"; cat "$LOG"; exit 1; }
echo "Bot API tunnel: $URL"

cd apps/web
vercel link --yes --project synthetic-boros >/dev/null
vercel deploy --prod --yes --build-env NEXT_PUBLIC_API_URL="$URL" --env NEXT_PUBLIC_API_URL="$URL"
echo
echo "Public site is live (read-only). Keep this terminal open: closing it stops the tunnel."
wait $TUNNEL_PID
