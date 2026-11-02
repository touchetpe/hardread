#!/bin/bash
# Keep the model gate and tunnel running, and shut everything down if isolation ever fails.
# A new tunnel gets a new address, so this publishes it to the static site.
#   deploy/public/watch.sh <checkout of the static site>     (stopped by offline.sh)
set -uo pipefail
SITE=${1:?static site checkout}
HERE=$(cd "$(dirname "$0")" && pwd)
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
publish() {
  printf '{"url": %s}\n' "$1" > "$SITE/live.json"
  git -C "$SITE" add live.json
  git -C "$SITE" -c user.name=HardRead -c user.email=hardread@users.noreply.github.com commit -qm "Update live address" \
    && git -C "$SITE" push -q origin main
}
echo $$ > "$RUN_DIR/watch.pid"
while true; do
  if ! "$HERE/check_isolation.sh" >/dev/null 2>&1; then
    publish null
    "$HERE/offline.sh"
    exit 1
  fi
  if ! lsof -nP -iTCP:18140 -sTCP:LISTEN >/dev/null 2>&1; then
    nohup python3 "$HERE/glimmer_gate.py" >/dev/null 2>&1 &
    echo $! > "$RUN_DIR/gate.pid"
  fi
  if ! { [ -f "$RUN_DIR/tunnel.pid" ] && kill -0 "$(cat "$RUN_DIR/tunnel.pid")" 2>/dev/null; }; then
    url=$("$HERE/tunnel.sh" 2>/dev/null) && publish "\"$url\""
  fi
  sleep 60
done
