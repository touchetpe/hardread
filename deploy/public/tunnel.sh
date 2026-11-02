#!/bin/bash
# Open the public tunnel to the isolated copy. Run only after the owner's approval and the security review.
# It refuses to open unless the isolation check passes now.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
"$HERE/check_isolation.sh" >&2 || { echo "isolation check failed; tunnel not opened" >&2; exit 1; }
LOG=$RUN_DIR/tunnel.log
EMPTY=$RUN_DIR/cloudflared-empty.yml
: > "$LOG"; : > "$EMPTY"   # an empty config, so nothing else can add a route: the only origin is the one below
nohup cloudflared tunnel --config "$EMPTY" --no-autoupdate --url http://127.0.0.1:18095 > "$LOG" 2>&1 &
echo $! > "$RUN_DIR/tunnel.pid"
nohup caffeinate -s -w "$(cat "$RUN_DIR/tunnel.pid")" >/dev/null 2>&1 &
for _ in $(seq 1 40); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$url" ] && { echo "$url"; exit 0; }
  sleep 1
done
echo "tunnel did not report an address; see $LOG" >&2
exit 1
