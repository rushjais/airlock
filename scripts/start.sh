#!/bin/bash
# Container entrypoint: the executor (only process that may hold a Stripe key) on localhost, then the API on $PORT.
# If either process exits, the container exits so the platform restarts it.
set -euo pipefail
cd /app/backend
: "${DATABASE_URL:?DATABASE_URL is required}"

# The API never needs a Stripe key. If one was provided, hand it only to the executor and drop it from the API's env.
STRIPE_KEY="${STRIPE_SECRET_KEY:-}"
unset STRIPE_SECRET_KEY

STRIPE_SECRET_KEY="$STRIPE_KEY" .venv/bin/uvicorn airlock.executor:app --host 127.0.0.1 --port 8001 --log-level warning &
unset STRIPE_KEY
for _ in $(seq 1 60); do
  .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=1)" 2>/dev/null && break
  sleep 0.5
done

.venv/bin/uvicorn airlock.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*' &
wait -n
exit 1
