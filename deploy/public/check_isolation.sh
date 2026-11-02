#!/bin/bash
# Prove the public copy's isolation right now. Exit 1 on any leak: a reachable host port, a host mount, or a writable path.
PROFILE=hardread-public
export DOCKER_HOST=unix://$HOME/.colima/$PROFILE/docker.sock
RUN_DIR=${HARDREAD_PUBLIC_RUN:-$HOME/.hardread-public}
MAC_PORTS=$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1{n=split($9,a,":"); print a[n]}' | sort -un | tr '\n' ' ')
VM_IPS=$(colima ssh -p "$PROFILE" -- sh -c "ip -4 -o addr | awk '{print \$4}' | cut -d/ -f1" | tr '\n' ' ')
MOUNTS=$(colima ssh -p "$PROFILE" -- sh -c "mount | grep -E 'virtiofs|sshfs|9p|fuse\.' | grep -v fusectl | awk '{print \$3}'" | tr '\n' ' ')
RESTART=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' hardread-public)
RULES=$(colima ssh -p "$PROFILE" -- sudo iptables -S DOCKER-USER | grep -c '172.30.0.0/24 -j DROP')
docker exec -i -e MAC_PORTS="$MAC_PORTS" -e VM_IPS="$VM_IPS" -e EXPECT_MOUNT="$RUN_DIR/vm-empty" -e MOUNTS="$MOUNTS" -e RESTART="$RESTART" -e RULES="$RULES" hardread-public python - <<'P'
import os, socket, sys
from concurrent.futures import ThreadPoolExecutor
def can(target):
    host, port = target
    s = socket.socket(); s.settimeout(1.5)
    try:
        s.connect((host, port)); return True
    except OSError:
        return False
    finally:
        s.close()
bad = 0
mac = sorted({int(x) for x in os.environ["MAC_PORTS"].split()} | {22, 80, 5432, 6379, 9000, 27017, 18000, 18002, 18030, 18131, 18135})
vm = os.environ["VM_IPS"].split()
targets = [("192.168.5.2", p) for p in mac if p != 18140]
targets += [(h, p) for h in vm + ["172.30.0.1", "192.168.5.1", "192.168.5.15", "169.254.169.254", "10.0.0.1", "192.168.1.1", "192.168.1.254"]
            for p in (22, 53, 80, 2375, 2376, 5432, 6379, 9000, 18095, 18131, 18135, 18140)]
with ThreadPoolExecutor(32) as ex:
    for t, hit in zip(targets, ex.map(can, targets)):
        if hit:
            print("REACHABLE", *t); bad += 1
gate, https, http = can(("glimmer-gate", 18140)), can(("replay.pokemonshowdown.com", 443)), can(("replay.pokemonshowdown.com", 80))
print("checked", len(targets), "host:port pairs, including", len(mac), "ports on the Mac")
print("gate reachable:", gate, "| public https reachable:", https, "| public non-https blocked:", not http)
bad += (not gate) + http
if os.getuid() == 0:
    print("RUNNING AS ROOT"); bad += 1
for p in ("/srv/hardread/x", "/x", "/opt/fp/x"):
    try:
        open(p, "w"); print("WRITABLE", p); bad += 1
    except OSError:
        pass
mounts = os.environ["MOUNTS"].split()
if mounts != [os.environ["EXPECT_MOUNT"]]:
    print("UNEXPECTED VM MOUNTS", mounts); bad += 1
if os.environ["RESTART"] != "on-failure":
    print("UNSAFE RESTART POLICY", os.environ["RESTART"]); bad += 1
if int(os.environ["RULES"]) < 1:
    print("FIREWALL MISSING"); bad += 1
print("leaks:", bad)
sys.exit(1 if bad else 0)
P
