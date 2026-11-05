#!/bin/bash
# Kill switch: stops the watcher, closes the tunnel, then stops the app, the model gate and the VM, and checks they're gone.
# Models and other projects are untouched. To take the link off the static site too, set live.json back to {"url": null}.
HERE=$(cd "$(dirname "$0")" && pwd)
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
limit() { "$HERE/limit.sh" "$@"; }
stop_pid() {   # only what the pid file says, and only if it's still that program
  local pidfile="${RUN_DIR:?}/$1.pid" expected=$2 pid
  [ -f "$pidfile" ] || return 0
  pid=$(cat "$pidfile")
  if ps -o command= -p "$pid" 2>/dev/null | grep -q -- "$expected"; then
    kill "$pid" 2>/dev/null
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -9 "$pid" 2>/dev/null
  fi
  rm -f "${pidfile:?}"
}
stop_pid watch "${HARDREAD_OFFLINE_WATCH_MATCH:-watch.sh}"
stop_pid tunnel "${HARDREAD_OFFLINE_TUNNEL_MATCH:-cloudflared}"
stop_pid gate glimmer_gate
pkill -f '^([^ ]*/)?cloudflared tunnel .*127[.]0[.]0[.]1:18095' 2>/dev/null   # last resort if the pid file was missing or wrong
sleep 1
export DOCKER_HOST=unix://$HOME/.colima/hardread-public/docker.sock
limit 60 docker rm -f hardread-public >/dev/null 2>&1
limit 120 colima stop hardread-public >/dev/null 2>&1 || limit 60 colima stop --force hardread-public >/dev/null 2>&1
status=0
if pgrep -f '^([^ ]*/)?cloudflared tunnel .*127[.]0[.]0[.]1:18095' >/dev/null; then echo "STILL RUNNING: cloudflared"; status=1; fi
for port in 18095 18140; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then echo "STILL OPEN: port $port"; status=1; fi
done
[ $status -eq 0 ] && echo "HardRead public copy is offline"
exit $status
