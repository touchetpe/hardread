#!/bin/bash
# Kill switch: stops the watcher, closes the tunnel, then stops the app, the model gate and the VM, and checks they're gone.
# Models and other projects are untouched. To take the link off the static site too, set live.json back to {"url": null}.
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
for f in watch tunnel gate; do
  pidfile="${RUN_DIR:?}/${f:?}.pid"
  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile")
    kill "$pid" 2>/dev/null
    for _ in 1 2 3 4 5; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -9 "$pid" 2>/dev/null
    rm -f "${pidfile:?}"
  fi
done
DOCKER_HOST=unix://$HOME/.colima/hardread-public/docker.sock docker rm -f hardread-public >/dev/null 2>&1
colima stop hardread-public >/dev/null 2>&1
status=0
if pgrep -f 'cloudflared tunnel .*127.0.0.1:18095' >/dev/null; then echo "STILL RUNNING: cloudflared"; status=1; fi
for port in 18095 18140; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then echo "STILL OPEN: port $port"; status=1; fi
done
[ $status -eq 0 ] && echo "HardRead public copy is offline"
exit $status
