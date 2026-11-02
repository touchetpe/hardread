#!/bin/bash
# Open the public tunnel to the isolated copy. Run only after the owner's approval and the security review.
set -euo pipefail
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
LOG=$RUN_DIR/tunnel.log
: > "$LOG"
nohup cloudflared tunnel --no-autoupdate --url http://127.0.0.1:18095 > "$LOG" 2>&1 &
echo $! > "$RUN_DIR/tunnel.pid"
nohup caffeinate -s -w "$(cat "$RUN_DIR/tunnel.pid")" >/dev/null 2>&1 &
for _ in $(seq 1 40); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$url" ] && { echo "$url"; exit 0; }
  sleep 1
done
echo "tunnel did not report an address; see $LOG" >&2
exit 1
