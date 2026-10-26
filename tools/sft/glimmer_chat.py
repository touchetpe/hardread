"""Render Muse Glimmer's chat template and tokenize SFT examples (no transformers needed).

The template is rendered with the same Jinja settings as transformers' apply_chat_template
(ImmutableSandboxedEnvironment, trim_blocks, lstrip_blocks, loopcontrols, tojson with
ensure_ascii=False). Tokenization uses the pinned tokenizer.json via `tokenizers`.

Supervision (see encode): the prompt ends with the generation prompt "<|start|>assistant";
every token after it is trained, i.e. the channel header " to=submit_action<|message|>",
the ATEM tool-call body and the final <|eot|>. The canary supervised only the content
after the header; its 18-step profile later emitted a malformed channel prefix. Training
the header removes that gap.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PINNED_TOKENIZER_SHA256 = "c9dbee66967b58f31a7c27f723c3760da3526ccd0427578e8905b0abb0031c4d"
PINNED_TEMPLATE_SHA256 = "cfc67e5f349f37690dfd31ed1f18bc4442a9dd32fe39a648f993cb4eb3cae678"
GEN_PROMPT = "<|start|>assistant"
EOT = "<|eot|>"
EOM = "<|eom|>"
BOS = "<|begin_of_text|>"
BOS_ID, EOM_ID, EOS_IDS = 200000, 200007, (200001, 200008)
EOS = "<|end_of_text|>"
DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[3] / "models" / "muse-glimmer-metadata"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class GlimmerChat:
    def __init__(self, model_dir: str | Path | None = None, verify: bool = True):
        import jinja2
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        from tokenizers import Tokenizer

        d = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        tok_path, tpl_path = d / "tokenizer.json", d / "chat_template.jinja"
        self.tokenizer_sha256 = sha256_file(tok_path)
        self.template_sha256 = sha256_file(tpl_path)
        if verify and (self.tokenizer_sha256 != PINNED_TOKENIZER_SHA256 or self.template_sha256 != PINNED_TEMPLATE_SHA256):
            raise ValueError(f"tokenizer/template differ from the pinned Glimmer revision in {d}")
        self.tok = Tokenizer.from_file(str(tok_path))

        def tojson(x, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
            return json.dumps(x, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys)

        def raise_exception(msg):
            raise jinja2.exceptions.TemplateError(msg)

        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"])
        env.filters["tojson"] = tojson
        env.globals["raise_exception"] = raise_exception
        self.template = env.from_string(tpl_path.read_text())
        self.eot_id = self.tok.token_to_id(EOT)
        self.pad_id = self.tok.token_to_id("<|finetune_right_pad|>")

    def render(self, messages, tools=None, add_generation_prompt=False, **kw) -> str:
        return self.template.render(messages=messages, tools=tools, add_generation_prompt=add_generation_prompt,
                                    bos_token=BOS, eos_token=EOS, **kw)

    def ids(self, text: str) -> list[int]:
        return self.tok.encode(text, add_special_tokens=False).ids

    def encode(self, prompt_messages, tools, target_message) -> dict:
        """Token ids + labels (-100 on the prompt) for one example; asserts the boundary is clean."""
        prompt = self.render(prompt_messages, tools, add_generation_prompt=True)
        full = self.render(list(prompt_messages) + [target_message], tools, add_generation_prompt=False)
        if not prompt.endswith(GEN_PROMPT) or not full.startswith(prompt) or not full.endswith(EOT):
            raise ValueError("template boundary mismatch between prompt and full rendering")
        p_ids, f_ids = self.ids(prompt), self.ids(full)
        if f_ids[:len(p_ids)] != p_ids:
            raise ValueError("prompt tokens are not a prefix of the full sequence")
        if f_ids[-1] != self.eot_id:
            raise ValueError("sequence does not end with <|eot|>")
        if f_ids[0] != BOS_ID or f_ids.count(BOS_ID) != 1:
            raise ValueError("expected exactly one BOS, at position 0 (rendered by the template)")
        tgt = f_ids[len(p_ids):]
        if any(t in EOS_IDS for t in tgt[:-1]):
            raise ValueError("an EOS token appears before the end of the target")
        labels = [-100] * len(p_ids) + f_ids[len(p_ids):]
        return {"input_ids": f_ids, "labels": labels, "n_prompt": len(p_ids), "n_target": len(f_ids) - len(p_ids),
                "target_text": full[len(prompt):]}


# --------------------------------------------------------------------------- output parser
# Python port of llama.cpp f8def7fe common_chat_params_init_muse_glimmer() for
# tool_choice=required (common/chat.cpp):
#   zero_or_more(start + analysis) + start + tool_call
#   start    = "<|start|>assistant"
#   analysis = " to=self<|message|>" until("<|eom|>") "<|eom|>"
#   tool     = " to=" until("<|message|>") "<|message|><atem:function_calls>" space
#              '<atem:invoke name="NAME">' space (param space)* "</atem:invoke>" space "</atem:function_calls>"
#   param    = '<atem:parameter name="P">' value "</atem:parameter>"  (string: raw text; else JSON)
# Generation stops on an EOS (<|eot|> 200008 or <|end_of_text|> 200001); <|eom|> is not a stop.
_ANALYSIS = r"<\|start\|>assistant to=self<\|message\|>(?:(?!<\|eom\|>).)*<\|eom\|>"
_TOOL = (r"<\|start\|>assistant to=(?P<recipient>(?:(?!<\|message\|>).)*)<\|message\|><atem:function_calls>\s*"
         r'<atem:invoke name="(?P<name>[^"]*)">\s*(?P<params>(?:<atem:parameter name="[^"]*">.*?</atem:parameter>\s*)*)'
         r"</atem:invoke>\s*</atem:function_calls>")
_FULL_RE = re.compile(rf"(?P<analysis>(?:{_ANALYSIS})*){_TOOL}", re.DOTALL)
_PARAM_RE = re.compile(r'<atem:parameter name="([^"]*)">(.*?)</atem:parameter>', re.DOTALL)


def parse_tool_call(continuation: str, tools: list[dict], gen_prompt: str = GEN_PROMPT) -> dict:
    """Parse a raw model continuation (text generated after the prompt, special tokens kept).

    Returns {"ok", "error", "name", "arguments", "n_reasoning_frames", "reasoning_chars"}. Any deviation
    from the native layout (missing/garbled channel header, text after the call, missing EOS) is an error.
    """
    res = {"ok": False, "error": None, "name": None, "arguments": None, "n_reasoning_frames": 0, "reasoning_chars": 0}
    text = gen_prompt + continuation
    if text.endswith(EOT):
        text = text[: -len(EOT)]
    elif text.endswith("<|end_of_text|>"):
        text = text[: -len("<|end_of_text|>")]
    else:
        res["error"] = "no_eos_truncated"
        return res
    m = _FULL_RE.fullmatch(text)
    if not m:
        res["error"] = "malformed_channel_or_call"
        return res
    frames = re.findall(r"to=self<\|message\|>(.*?)<\|eom\|>", m.group("analysis"), re.DOTALL)
    res["n_reasoning_frames"], res["reasoning_chars"] = len(frames), sum(len(f) for f in frames)
    schemas = {(t.get("function") or t)["name"]: (t.get("function") or t).get("parameters") or {} for t in tools}
    name = m.group("name")
    if name not in schemas:
        res["error"] = "unknown_tool"
        return res
    props = schemas[name].get("properties") or {}
    args = {}
    for k, v in _PARAM_RE.findall(m.group("params")):
        if k not in props:
            res["error"] = f"unknown_parameter:{k}"
            return res
        if props[k].get("type") == "string":
            args[k] = v
        else:
            try:
                args[k] = json.loads(v)
            except json.JSONDecodeError:
                res["error"] = f"bad_json_parameter:{k}"
                return res
    res.update(ok=True, name=name, arguments=args)
    return res
