"""Minimal client for a local llama.cpp `llama-server` (OpenAI-compatible /v1/chat/completions, --jinja).

Base Muse Glimmer-30B only (no API calls to Meta/Modal/Baseten). Sampling defaults match the runbook's local
serve flags (temperature 0, top_k 40, top_p 1, seed 42); reasoning strength goes to the chat template via
chat_template_kwargs. Tool calls come back parsed by llama.cpp's Muse/ATEM parser.
"""
import json
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:18131/v1/chat/completions"


class LLMError(RuntimeError):
    pass


class GlimmerClient:
    def __init__(self, url=DEFAULT_URL, max_tokens=2048, temperature=0.0, reasoning="low", timeout=900, seed=42):
        self.url = url
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.reasoning = reasoning
        self.timeout = timeout
        self.seed = seed

    def chat(self, messages, tools=None, tool_choice="required", max_tokens=None):
        body = {"messages": messages, "max_tokens": max_tokens or self.max_tokens, "temperature": self.temperature,
                "top_k": 40, "top_p": 1, "seed": self.seed,
                "chat_template_kwargs": {"reasoning_strength": self.reasoning}}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LLMError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        wall = time.time() - t
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        tm = d.get("timings") or {}
        us = d.get("usage") or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                err = None
            except json.JSONDecodeError as e:
                args, err = None, f"arguments are not valid JSON: {e}"
            calls.append({"id": tc.get("id"), "name": fn.get("name"), "raw_arguments": raw, "arguments": args,
                          "parse_error": err})
        return {"message": msg, "tool_calls": calls, "finish_reason": ch.get("finish_reason"),
                "content": msg.get("content") or "", "reasoning": msg.get("reasoning_content") or "",
                "wall_s": round(wall, 3), "prompt_tokens": us.get("prompt_tokens"),
                "completion_tokens": us.get("completion_tokens"), "cached_tokens": tm.get("cache_n"),
                "prompt_ms": tm.get("prompt_ms"), "decode_tps": tm.get("predicted_per_second")}

    def health(self):
        base = self.url.split("/v1/")[0]
        try:
            with urllib.request.urlopen(base + "/health", timeout=5) as r:
                return json.loads(r.read()).get("status") == "ok"
        except Exception:  # noqa: BLE001
            return False


def assistant_message(resp):
    """The assistant turn to append to the history (reasoning kept so llama.cpp's prefix cache stays valid)."""
    msg = resp["message"]
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("reasoning_content"):
        out["reasoning_content"] = msg["reasoning_content"]
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def tool_message(call_id, name, content):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
