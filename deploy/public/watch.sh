#!/bin/bash
# Keep the model gate and tunnel running, and shut everything down if isolation ever fails.
# A check that hangs counts as a failure, and publishing to the site never holds up the checks.
# A new tunnel gets a new address, so this publishes it to the static site.
#   deploy/public/watch.sh <checkout of the static site>     (stopped by offline.sh)
set -uo pipefail
SITE=${1:?static site checkout}
HERE=$(cd "$(dirname "$0")" && pwd)
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
# tests point these at stubs, so no test opens a real tunnel
TUNNEL=${HARDREAD_WATCH_TUNNEL:-$HERE/tunnel.sh}
CHECK=${HARDREAD_WATCH_CHECK:-$HERE/check_isolation.sh}
INTERVAL=${HARDREAD_WATCH_INTERVAL:-60}
CHECK_LIMIT=${HARDREAD_WATCH_CHECK_LIMIT:-120}
limit() { "$HERE/limit.sh" "$@"; }
publish() {
  (
    printf '{"url": %s}\n' "$1" > "$SITE/live.json"
    git -C "$SITE" add live.json
    git -C "$SITE" -c user.name=HardRead -c user.email=hardread@users.noreply.github.com commit -qm "Update live address"
    GIT_TERMINAL_PROMPT=0 limit 120 git -C "$SITE" push -q origin main
  ) >/dev/null 2>&1 &
}
echo $$ > "$RUN_DIR/watch.pid"
failures=0
while true; do
  date +%s > "$RUN_DIR/watch.beat"
  if ! limit 10 lsof -nP -iTCP:18140 -sTCP:LISTEN >/dev/null 2>&1; then
    nohup python3 "$HERE/glimmer_gate.py" >/dev/null 2>&1 &
    echo $! > "$RUN_DIR/gate.pid"
  fi
  if limit "$CHECK_LIMIT" "$CHECK" >/dev/null 2>&1; then
    failures=0
  else
    failures=$((failures + 1))
    if [ "$failures" -ge 2 ]; then   # two in a row, so a brief hiccup or the Mac waking doesn't take it down
      publish null                   # in the background: never wait on the network before closing the tunnel
      "$HERE/offline.sh"             # not exec: offline.sh stops this watcher by its pid, and must outlive it
      exit 1
    fi
    sleep 10
    continue
  fi
  if ! { [ -f "$RUN_DIR/tunnel.pid" ] && kill -0 "$(cat "$RUN_DIR/tunnel.pid")" 2>/dev/null; }; then
    url=$(limit 120 "$TUNNEL" 2>/dev/null) && publish "\"$url\""
  fi
  sleep "$INTERVAL"
done
