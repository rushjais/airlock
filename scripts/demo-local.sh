#!/bin/bash
# Fallback for recording the demo when hosting isn't available: boots the same container as production on
# http://localhost:8000 (Docker only, no secrets), waits until it's healthy, and opens the browser.
#   scripts/demo-local.sh          # start (Ctrl-C stops it)
set -euo pipefail
cd "$(dirname "$0")/.."
docker info >/dev/null 2>&1 || { echo "Start Docker Desktop first."; exit 1; }
docker compose up --build -d
printf "Waiting for Airlock"
for _ in $(seq 1 90); do
  curl -sf http://localhost:8000/health >/dev/null && break
  printf "."; sleep 1
done
echo
curl -sf http://localhost:8000/health >/dev/null || { docker compose logs app | tail -30; exit 1; }
echo "Airlock is running at http://localhost:8000 (Ctrl-C to stop)"
command -v open >/dev/null && open http://localhost:8000 || true
trap 'docker compose down' EXIT
docker compose logs -f app
