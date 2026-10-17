#!/usr/bin/env bash
check() { if lsof -nP -iTCP:"$2" -sTCP:LISTEN >/dev/null 2>&1; then echo "  ok    $1 (:$2)"; else echo "  DOWN  $1 (:$2)"; fi; }
check executor 8001; check api 8000; check ui 5173; check tunnel 4040
T=$(curl -s -m 3 localhost:4040/api/tunnels | python3 -c 'import sys,json;t=json.load(sys.stdin)["tunnels"];print(t[0]["public_url"] if t else "")' 2>/dev/null)
echo "  tunnel url: ${T:-none}"
[ -n "$T" ] && echo "  public health: $(curl -s -m 5 "$T/health")"
echo "  screen: http://localhost:5173"
