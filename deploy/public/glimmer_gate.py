#!/usr/bin/env python3
"""The only route from the public container to the model: chat completions and health, one call at a time.

  python3 deploy/public/glimmer_gate.py [--port 18140] [--upstream http://127.0.0.1:18135]
"""
import argparse
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 512_000
CHAT = "/v1/chat/completions"
ALLOWED_FIELDS = {"messages", "tools", "tool_choice", "max_tokens", "temperature", "top_k", "top_p", "seed",
                  "chat_template_kwargs"}
MAX_TOKENS = 2048
TIMEOUT = 400
ONE_AT_A_TIME = threading.Lock()


def clean_request(raw):
    """-> upstream body, or None when the request is not a plain chat completion."""
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(body, dict) or set(body) - ALLOWED_FIELDS or not isinstance(body.get("messages"), list):
        return None
    if not isinstance(body.get("max_tokens", 0), int) or body.get("max_tokens", 0) > MAX_TOKENS:
        return None
    return json.dumps(body).encode()


def make_handler(upstream):
    class Gate(BaseHTTPRequestHandler):
        server_version = "gate"
        sys_version = ""

        def _send(self, status, payload, ctype="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):   # no visitor data in logs
            pass

        def do_GET(self):
            if self.path != "/health":
                return self._send(404, b'{"error":"not found"}')
            try:
                with urllib.request.urlopen(upstream + "/health", timeout=2) as r:
                    return self._send(r.status, b'{"status":"ok"}')
            except (urllib.error.URLError, OSError):
                return self._send(503, b'{"status":"down"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if self.path != CHAT or not 0 < length <= MAX_BODY:
                return self._send(404, b'{"error":"not found"}')
            body = clean_request(self.rfile.read(length))
            if body is None:
                return self._send(400, b'{"error":"bad request"}')
            req = urllib.request.Request(upstream + CHAT, data=body, headers={"Content-Type": "application/json"})
            with ONE_AT_A_TIME:
                try:
                    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                        return self._send(r.status, r.read())
                except urllib.error.HTTPError as e:
                    return self._send(502, json.dumps({"error": f"model answered {e.code}"}).encode())
                except (urllib.error.URLError, OSError):
                    return self._send(502, b'{"error":"model unavailable"}')
    return Gate


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=18140)
    ap.add_argument("--upstream", default="http://127.0.0.1:18135")
    a = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.upstream.rstrip("/"))).serve_forever()


if __name__ == "__main__":
    main()
