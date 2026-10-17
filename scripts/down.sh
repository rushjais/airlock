#!/usr/bin/env bash
for p in 8001 8000 5173 4040; do pid=$(lsof -nP -tiTCP:$p -sTCP:LISTEN 2>/dev/null); [ -n "$pid" ] && kill $pid && echo "  stopped :$p"; done
pkill -f 'ngrok http 8000' 2>/dev/null; true
