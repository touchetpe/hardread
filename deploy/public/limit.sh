#!/bin/bash
# limit.sh SECONDS command...: run a command, and kill it if it's still running after SECONDS. Exit is nonzero then.
# (macOS has no `timeout`.) The alarm survives exec, so the command itself receives it.
exec perl -e 'alarm shift @ARGV; exec { $ARGV[0] } @ARGV or exit 127' "$@"
