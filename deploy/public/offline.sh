#!/bin/bash
# Kill switch: stops the watcher, closes the tunnel, then stops the app, the model gate and the VM. Models and other projects are untouched.
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
for f in watch tunnel gate; do
  pidfile="${RUN_DIR:?}/${f:?}.pid"
  if [ -f "$pidfile" ]; then
    kill "$(cat "$pidfile")" 2>/dev/null
    rm -f "${pidfile:?}"
  fi
done
DOCKER_HOST=unix://$HOME/.colima/hardread-public/docker.sock docker rm -f hardread-public >/dev/null 2>&1
colima stop hardread-public >/dev/null 2>&1
echo "HardRead public copy is offline"
