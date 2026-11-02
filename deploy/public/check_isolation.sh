#!/bin/bash
# From inside the public container: what can it reach? Everything but the gate and public HTTPS must fail. Exit 1 on a leak.
export DOCKER_HOST=unix://$HOME/.colima/hardread-public/docker.sock
docker exec -i hardread-public python - <<'P'
import os, socket, sys
def can(host, port):
    s = socket.socket(); s.settimeout(2)
    try:
        s.connect((host, port)); return True
    except OSError:
        return False
    finally:
        s.close()
bad = 0
for host in ("192.168.5.2", "host.lima.internal", "host.docker.internal", "172.30.0.1", "192.168.5.15", "169.254.169.254", "10.0.0.1", "192.168.1.1"):
    for port in (22, 53, 80, 5432, 6379, 9000, 27017, 18030, 18131, 18135, 18000, 18002):
        if can(host, port):
            print("REACHABLE", host, port); bad += 1
print("gate reachable:", can("glimmer-gate", 18140))
print("public https reachable:", can("replay.pokemonshowdown.com", 443))
print("public non-https blocked:", not can("replay.pokemonshowdown.com", 80))
print("uid:", os.getuid())
for p in ("/srv/hardread/x", "/x", "/opt/fp/x"):
    try:
        open(p, "w"); print("WRITABLE", p); bad += 1
    except OSError:
        pass
print("leaks:", bad)
sys.exit(1 if bad else 0)
P
