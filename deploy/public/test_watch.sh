#!/bin/bash
# Exercise the watcher with no real tunnel: gate restart, tunnel restart with address publishing, and shutdown when
# isolation fails. Needs the public copy running (up.sh); it ends with that copy stopped.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
T=$(mktemp -d)
git init -q --bare "$T/remote.git"
git clone -q "$T/remote.git" "$T/site" 2>/dev/null
echo '{"url": null}' > "$T/site/live.json"
git -C "$T/site" add live.json
git -C "$T/site" -c user.name=t -c user.email=t@t commit -qm init
git -C "$T/site" branch -M main
git -C "$T/site" push -q -u origin main
cat > "$T/tunnel_stub.sh" <<STUB
#!/bin/bash
sleep 1000 >/dev/null 2>&1 &
echo \$! > "$RUN_DIR/tunnel.pid"
echo "https://stub-\$RANDOM.example"
STUB
chmod +x "$T/tunnel_stub.sh"
live() { git -C "$T/remote.git" show main:live.json; }
wait_for() { for _ in $(seq 1 "$2"); do eval "$1" && return 0; sleep 2; done; return 1; }
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; }
export HARDREAD_OFFLINE_TUNNEL_MATCH="sleep 1000"
HARDREAD_WATCH_TUNNEL="$T/tunnel_stub.sh" HARDREAD_WATCH_INTERVAL=5 nohup "$HERE/watch.sh" "$T/site" > "$T/watch.log" 2>&1 &
wait_for 'live | grep -q stub' 60 && pass "tunnel started and address published: $(live)" || fail "no address published"
first=$(live)
gate=$(lsof -nP -tiTCP:18140 -sTCP:LISTEN | head -1)
kill "$gate"
wait_for 'lsof -nP -iTCP:18140 -sTCP:LISTEN >/dev/null 2>&1' 40 && pass "gate restarted" || fail "gate not restarted"
kill "$(cat "$RUN_DIR/tunnel.pid")"
wait_for '[ "$(live)" != "$first" ]' 40 && pass "tunnel restarted, new address published: $(live)" || fail "tunnel not restarted"
stub=$(cat "$RUN_DIR/tunnel.pid")
echo "--- one brief failure must not shut down"
colima ssh -p hardread-public -- sudo iptables -I DOCKER-USER -s 172.30.0.0/24 -d 192.168.5.2 -p tcp --dport 18131 -j ACCEPT
sleep 1
colima ssh -p hardread-public -- sudo iptables -D DOCKER-USER -s 172.30.0.0/24 -d 192.168.5.2 -p tcp --dport 18131 -j ACCEPT
sleep 3
kill -0 "$(cat "$RUN_DIR/watch.pid")" 2>/dev/null && pass "watcher still running after a brief change" || fail "watcher stopped early"
echo "--- isolation broken: open the Mac's llama port to the container and keep it open"
colima ssh -p hardread-public -- sudo iptables -I DOCKER-USER -s 172.30.0.0/24 -d 192.168.5.2 -p tcp --dport 18131 -j ACCEPT
wait_for '! kill -0 '"$stub"' 2>/dev/null' 90 && pass "tunnel closed after isolation failed" || fail "tunnel still open"
wait_for '[ ! -f "$RUN_DIR/watch.pid" ]' 40 && pass "watcher gone" || fail "watcher still there"
wait_for 'live | grep -q null' 40 && pass "site told offline: $(live)" || fail "site not told offline"
wait_for '! lsof -nP -iTCP:18095 -sTCP:LISTEN >/dev/null 2>&1' 60 && pass "app port closed" || fail "app port open"
wait_for '! lsof -nP -iTCP:18140 -sTCP:LISTEN >/dev/null 2>&1' 60 && pass "gate port closed" || fail "gate port open"
wait_for 'colima list 2>/dev/null | grep hardread-public | grep -q Stopped' 60 && pass "VM stopped" || fail "VM not stopped"
pgrep -f "$T/watch" >/dev/null && fail "watcher process left" || pass "no watcher process left"

echo "--- a hung push must not stop the checks"
"$HERE/up.sh" "${HARDREAD_TEST_CONTEXT:?}" >/dev/null 2>&1
printf '#!/bin/sh\nsleep 1000\n' > "$T/remote.git/hooks/pre-receive"; chmod +x "$T/remote.git/hooks/pre-receive"
HARDREAD_WATCH_TUNNEL="$T/tunnel_stub.sh" HARDREAD_WATCH_INTERVAL=5 nohup "$HERE/watch.sh" "$T/site" > "$T/watch.log" 2>&1 &
sleep 25
b1=$(cat "$RUN_DIR/watch.beat"); sleep 40; b2=$(cat "$RUN_DIR/watch.beat")
[ "$b2" -gt "$b1" ] && pass "checks kept running during a hung push" || fail "watcher froze on a hung push"
"$HERE/offline.sh" >/dev/null; rm -f "$T/remote.git/hooks/pre-receive"
wait_for '! lsof -nP -iTCP:18095 -sTCP:LISTEN >/dev/null 2>&1' 60

echo "--- a hung check counts as a failure and closes everything"
"$HERE/up.sh" "${HARDREAD_TEST_CONTEXT:?}" >/dev/null 2>&1
printf '#!/bin/sh\nsleep 1000\n' > "$T/hung_check.sh"; chmod +x "$T/hung_check.sh"
HARDREAD_WATCH_CHECK="$T/hung_check.sh" HARDREAD_WATCH_CHECK_LIMIT=5 HARDREAD_WATCH_INTERVAL=5 HARDREAD_WATCH_TUNNEL="$T/tunnel_stub.sh" nohup "$HERE/watch.sh" "$T/site" > "$T/watch.log" 2>&1 &
wait_for '[ ! -f "$RUN_DIR/watch.pid" ]' 90 && pass "watcher closed after hung checks" || fail "watcher hung"
wait_for '! lsof -nP -iTCP:18095 -sTCP:LISTEN >/dev/null 2>&1' 60 && pass "app port closed" || fail "app port open"
wait_for 'colima list 2>/dev/null | grep hardread-public | grep -q Stopped' 60 && pass "VM stopped" || fail "VM not stopped"

echo "--- a tunnel with no pid file still gets closed"
"$HERE/up.sh" "${HARDREAD_TEST_CONTEXT:?}" >/dev/null 2>&1
rm -f "$RUN_DIR/tunnel.pid"
(exec -a 'cloudflared tunnel --url http://127.0.0.1:18095' sleep 1000) &
stray=$!
sleep 1
pgrep -f '^([^ ]*/)?cloudflared tunnel .*127[.]0[.]0[.]1:18095' >/dev/null && pass "stand-in matches the kill switch's pattern" || fail "stand-in doesn't match"
"$HERE/offline.sh" >/dev/null; rc=$?
kill -0 "$stray" 2>/dev/null && fail "stand-in tunnel left running" || pass "stand-in tunnel closed without a pid file"
[ "$rc" -eq 0 ] && pass "kill switch reports offline" || fail "kill switch exit $rc"

echo "--- open.sh brings it back, through the watcher"
mkdir -p "$RUN_DIR"; [ -e "$RUN_DIR/context" ] || ln -s "${HARDREAD_TEST_CONTEXT:?}" "$RUN_DIR/context"
before=$(live)
HARDREAD_WATCH_TUNNEL="$T/tunnel_stub.sh" HARDREAD_WATCH_INTERVAL=5 "$HERE/open.sh" "$T/site" >/dev/null
wait_for 'curl -s -m 3 http://127.0.0.1:18095/api/health | grep -q ok' 60 && pass "app back up" || fail "app not up"
wait_for 'live | grep -q stub' 60 && pass "watcher opened the stand-in and published: $(live)" || fail "no address published"
"$HERE/offline.sh" >/dev/null && pass "closed again" || fail "kill switch failed"
wait_for '! lsof -nP -iTCP:18095 -sTCP:LISTEN >/dev/null 2>&1' 60
