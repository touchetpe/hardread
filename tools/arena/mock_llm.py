#!/usr/bin/env python3
"""Mock OpenAI-compatible chat/completions endpoint for arena tests.

Answers every request with a tool call that picks a random legal choice id. The ids come
from the tool schema (the enum of the first required parameter) or, if the schema has no
enum, from the request's "x_hardread_choice_ids" (sent by the policy when
HARDREAD_LLM_DEBUG_IDS=1). Optional fault injection exercises the failure accounting.

  python tools/arena/mock_llm.py --port 18046 [--p-no-tool 0.05] [--p-illegal 0.05] [--delay 0.2]
"""
import argparse
import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_handler(args, rng):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {"status": "ok"})

        def do_POST(self):
            if not self.path.endswith("/chat/completions"):
                return self._send(404, {"error": "not found"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            fn = body["tools"][0]["function"]
            params = fn.get("parameters", {})
            key = (params.get("required") or list(params.get("properties", {})))[0]
            ids = params.get("properties", {}).get(key, {}).get("enum") or body.get("x_hardread_choice_ids") or []
            if args.delay:
                time.sleep(args.delay)
            u = rng.random()
            msg = {"role": "assistant", "content": None}
            if u < args.p_no_tool or not ids:
                msg["content"] = "I think I will attack."
            else:
                choice = "not-a-legal-id" if u < args.p_no_tool + args.p_illegal else rng.choice(ids)
                msg["tool_calls"] = [{"id": "call_0", "type": "function", "function": {
                    "name": fn["name"], "arguments": json.dumps({key: choice})}}]
            self._send(200, {"id": "mock", "object": "chat.completion", "model": body.get("model"),
                             "choices": [{"index": 0, "message": msg,
                                          "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 0}})
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=18046)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--p-no-tool", type=float, default=0.0)
    ap.add_argument("--p-illegal", type=float, default=0.0)
    ap.add_argument("--delay", type=float, default=0.0)
    args = ap.parse_args()
    if not 18040 <= args.port <= 18049:
        raise SystemExit("arena ports are 18040-18049")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args, random.Random(args.seed)))
    print(f"mock LLM on http://127.0.0.1:{args.port}/v1/chat/completions", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
