#!/bin/bash
# Start the isolated public copy, reachable only from this Mac on 127.0.0.1:18095. This script opens no tunnel.
#   deploy/public/up.sh <dir with a clean export: app/ tools/ foul-play/>
set -euo pipefail
CONTEXT=${1:?clean export directory}
HERE=$(cd "$(dirname "$0")" && pwd)
PROFILE=hardread-public
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
mkdir -p "$RUN_DIR/vm-empty"
colima status -p "$PROFILE" >/dev/null 2>&1 || colima start "$PROFILE" --cpu 6 --memory 8 --disk 20 --mount "$RUN_DIR/vm-empty" >/dev/null 2>&1
export DOCKER_HOST=unix://$HOME/.colima/$PROFILE/docker.sock

docker build -q -t hardread-public -f "$HERE/Dockerfile" "$CONTEXT" >/dev/null
docker network inspect hardread-public >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/24 hardread-public >/dev/null

# Firewall inside the VM. The container may reach: DNS, public HTTPS, and the model gate. Nothing else on this Mac or
# LAN, and nothing on the VM itself.
colima ssh -p "$PROFILE" -- sudo sh -s <<'RULES'
iptables -D INPUT -s 172.30.0.0/24 -j DROP 2>/dev/null || true
iptables -D INPUT -s 172.30.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || true
iptables -I INPUT -s 172.30.0.0/24 -j DROP
iptables -I INPUT -s 172.30.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -F DOCKER-USER
iptables -A DOCKER-USER -s 172.30.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A DOCKER-USER -d 172.30.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A DOCKER-USER -s 172.30.0.0/24 -d 192.168.5.2 -p tcp --dport 18140 -j ACCEPT
iptables -A DOCKER-USER -s 172.30.0.0/24 -p udp --dport 53 -d 192.168.5.3 -j ACCEPT
iptables -A DOCKER-USER -s 172.30.0.0/24 -p tcp --dport 53 -d 192.168.5.3 -j ACCEPT
iptables -A DOCKER-USER -s 172.30.0.0/24 -d 10.0.0.0/8 -j DROP
iptables -A DOCKER-USER -s 172.30.0.0/24 -d 172.16.0.0/12 -j DROP
iptables -A DOCKER-USER -s 172.30.0.0/24 -d 192.168.0.0/16 -j DROP
iptables -A DOCKER-USER -s 172.30.0.0/24 -d 169.254.0.0/16 -j DROP
iptables -A DOCKER-USER -s 172.30.0.0/24 -d 100.64.0.0/10 -j DROP
iptables -A DOCKER-USER -s 172.30.0.0/24 -p tcp --dport 443 -j ACCEPT
iptables -A DOCKER-USER -s 172.30.0.0/24 -j DROP
iptables -A DOCKER-USER -j RETURN
RULES

# The model gate on the Mac: loopback only, chat completions and health only.
if ! lsof -nP -iTCP:18140 -sTCP:LISTEN >/dev/null 2>&1; then
  nohup python3 "$HERE/glimmer_gate.py" >/dev/null 2>&1 &
  echo $! > "$RUN_DIR/gate.pid"
fi

docker rm -f hardread-public >/dev/null 2>&1 || true
docker run -d --name hardread-public --restart on-failure --init --log-opt max-size=10m --log-opt max-file=2 --network hardread-public \
  --read-only --tmpfs /tmp:size=256m --tmpfs /state:size=2g,uid=10001,gid=10001,mode=0700 \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 6g --cpus 5 \
  --add-host glimmer-gate:192.168.5.2 -e HARDREAD_LLM_URL=http://glimmer-gate:18140 \
  -e HARDREAD_PUBLIC_ORIGIN="${HARDREAD_PUBLIC_ORIGIN:-https://touchetpe.github.io}" \
  -p 127.0.0.1:18095:8080 hardread-public >/dev/null
echo "public copy on http://127.0.0.1:18095 (no tunnel)"
