#!/usr/bin/env bash
# Start every Airlock service detached (survives terminal/chat sessions). Idempotent: skips what's already up.
#   scripts/up.sh      start      scripts/status.sh   check      scripts/down.sh   stop
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/.run"; mkdir -p "$RUN"
source "$HOME/.nvm/nvm.sh" >/dev/null 2>&1; nvm use 22 >/dev/null 2>&1 || true

up() {  # name port command...
  local name=$1 port=$2; shift 2
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then echo "  $name already up (:$port)"; return; fi
  ( cd "$ROOT" && nohup "$@" > "$RUN/$name.log" 2>&1 < /dev/null & echo $! > "$RUN/$name.pid" )
  echo "  $name starting (:$port) log: .run/$name.log"
}

echo "Airlock:"
up executor 8001 bash -c "cd backend && exec uv run uvicorn airlock.executor:app --port 8001 --log-level warning"
up api      8000 bash -c "cd backend && exec uv run uvicorn airlock.main:app --port 8000 --log-level info"
up ui       5173 bash -c "cd frontend && exec npm run dev -- --port 5173 --strictPort"
up tunnel   4040 ngrok http 8000 --log stdout --log-format logfmt
sleep 8
"$ROOT/scripts/status.sh"
