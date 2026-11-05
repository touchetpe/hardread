#!/bin/bash
# Bring the public copy back, for example after a reboot: rebuild from the saved clean context, recheck isolation, open
# the tunnel through the watcher. Only run this when the owner wants live review open. Stop it with offline.sh.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
SITE=${1:-$(cd "$HERE/../../../hardread-site" && pwd)}
"$HERE/up.sh" "$RUN_DIR/context"
nohup "$HERE/watch.sh" "$SITE" >"$RUN_DIR/watch.log" 2>&1 &
echo "watcher started; the address appears in $SITE/live.json. Stop with: $HERE/offline.sh"
