#!/usr/bin/env python3
"""Held-out evaluation of a Glimmer SFT adapter (HardRead).

  teacher  fresh-process HF reload (NF4 base + adapter, same quantization as training):
           - teacher-forced distribution over the legal choice ids at the id token
             (top-1 agreement with Foul Play's choice, top-k, policy-argmax agreement,
             policy mass of the model's pick, cross-entropy vs the search policy),
             with the adapter and with the adapter disabled (base under the same prefix);
           - greedy generation on a subset, parsed with the llama.cpp Muse parser port
             (malformed output = failure).
  serve    against a running llama-server (base GGUF [+ --lora adapter GGUF]):
           - production path: /v1/chat/completions with tools, tool_choice=required (llama.cpp's own
             PEG parser + grammar), temperature 0, top_k 40;
           - raw path: /completion with the exact prompt token ids and NO grammar, continuation
             parsed with the parser port (shows whether the model itself emits the native layout).
           Legal rate, malformed count, agreement, latency (TTFT, decode tok/s, wall).

Gate: legal-action rate on the production path >= 0.98 before anything else is looked at.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
for cand in (HERE.parents[1] / "tools" / "sft", HERE.parent / "tools" / "sft", HERE / "tools" / "sft"):
    if cand.is_dir():
        sys.path.insert(0, str(cand))
        break
import serialize as S  # noqa: E402
from glimmer_chat import GlimmerChat, parse_tool_call, EOS_IDS, EOT  # noqa: E402

GATE_LEGAL = 0.98


def load_rows(path, n, task="action"):
    op = gzip.open if str(path).endswith(".gz") else open
    rows = [json.loads(l) for l in op(path, "rt") if l.strip()]
    rows = [r for r in rows if r["task"] == task]
    rows.sort(key=lambda r: r["example_id"])
    if n and len(rows) > n:  # deterministic spread over games
        step = len(rows) / n
        rows = [rows[int(i * step)] for i in range(n)]
    return rows


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 3) if xs else None


def score_choice_dist(probs: dict, row) -> dict:
    """probs: {choice_id: p} over legal ids (renormalised)."""
    pol = row.get("policy") or {}
    ranked = sorted(probs, key=lambda c: -probs[c])
    pred = ranked[0]
    pol_arg = max(pol, key=pol.get) if pol else None
    ce = -sum(p * math.log(max(probs.get(c, 0.0), 1e-9)) for c, p in pol.items()) if pol else None
    return {"pred": pred, "agree": pred == row["choice_id"], "top2": row["choice_id"] in ranked[:2],
            "top3": row["choice_id"] in ranked[:3], "agree_policy_argmax": pred == pol_arg,
            "policy_mass_of_pred": pol.get(pred, 0.0), "p_choice": probs.get(row["choice_id"], 0.0), "ce_vs_policy": ce}


def summarize_scores(rs):
    if not rs:
        return {}
    m = lambda k: round(statistics.mean(float(r[k]) for r in rs if r.get(k) is not None), 4)  # noqa: E731
    return {"n": len(rs), "agree_top1": m("agree"), "top2": m("top2"), "top3": m("top3"),
            "agree_policy_argmax": m("agree_policy_argmax"), "policy_mass_of_pred": m("policy_mass_of_pred"),
            "p_choice": m("p_choice"), "ce_vs_policy": m("ce_vs_policy")}


def baselines(rows):
    """Trivial references on the same rows: first menu item, uniform, policy argmax."""
    first = statistics.mean(1.0 if r["choice_id"] == "act_01" else 0.0 for r in rows)
    unif = statistics.mean(1.0 / len(r["id_map"]) for r in rows)
    pa = statistics.mean(1.0 if r["policy"] and max(r["policy"], key=r["policy"].get) == r["choice_id"] else 0.0 for r in rows)
    return {"first_menu_item": round(first, 4), "uniform_expected": round(unif, 4), "search_policy_argmax": round(pa, 4)}


# ----------------------------------------------------------------------------- teacher (HF)

def last_logits(model, x):
    try:
        out = model(input_ids=x, logits_to_keep=1)
    except TypeError:
        out = model(input_ids=x)
    return out.logits[0, -1].float()


def teacher(args):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForImageTextToText, BitsAndBytesConfig

    gc = GlimmerChat(args.snapshot)
    rows = load_rows(args.eval, args.n)
    t0 = time.time()
    q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                           bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_quant_storage=torch.bfloat16)
    base = AutoModelForImageTextToText.from_pretrained(args.snapshot, local_files_only=True, quantization_config=q,
                                                       dtype=torch.bfloat16, attn_implementation="flash_attention_2",
                                                       device_map="auto")
    model = PeftModel.from_pretrained(base, args.adapter, is_trainable=False)
    model.eval()
    load_s = time.time() - t0
    dev = model.get_input_embeddings().weight.device
    digit_ids = {}
    for i in range(1, 100):
        tid = gc.tok.token_to_id(f"{i:02d}")
        if tid is None:
            raise SystemExit(f"choice id digits {i:02d} are not a single token")
        digit_ids[f"act_{i:02d}"] = tid
    act_us = gc.ids('">act_')[-2:]  # ['act', '_'] in context
    per_row, tuned, based = [], [], []
    t1 = time.time()
    for r in rows:
        tgt = S.target_message(r["choice"], r["id_map"])
        enc = gc.encode(r["messages"], r["tools"], tgt)
        ids = enc["input_ids"]
        # position of the 2-digit id token: right after the last 'act','_' pair
        pos = max(i for i in range(len(ids) - 2) if ids[i:i + 2] == act_us) + 2
        x = torch.tensor([ids[:pos]], device=dev)
        legal = list(r["id_map"])
        res = {"example_id": r["example_id"], "choice_id": r["choice_id"], "n_legal": len(legal)}
        for tag, ctx in (("tuned", None), ("base", "disable")):
            with torch.inference_mode():
                if ctx:
                    with model.disable_adapter():
                        logits = last_logits(model, x)
                else:
                    logits = last_logits(model, x)
            lp = torch.log_softmax(logits, -1)
            legal_lp = torch.stack([lp[digit_ids[c]] for c in legal])
            mass = float(legal_lp.exp().sum())
            probs = dict(zip(legal, torch.softmax(legal_lp, -1).tolist()))
            sc = score_choice_dist(probs, r)
            sc["legal_mass"] = mass
            res[tag] = sc
            (tuned if tag == "tuned" else based).append(sc)
        per_row.append(res)
    tf_s = time.time() - t1

    # greedy generation subset, parsed like llama.cpp would
    gens = []
    t2 = time.time()
    for r in rows[: args.gen_n]:
        prompt = gc.render(r["messages"], r["tools"], add_generation_prompt=True)
        ids = gc.ids(prompt)
        x = torch.tensor([ids], device=dev)
        ts = time.time()
        with torch.inference_mode():
            out = model.generate(input_ids=x, attention_mask=torch.ones_like(x), max_new_tokens=args.max_new_tokens,
                                 do_sample=False, eos_token_id=list(EOS_IDS), pad_token_id=200018)
        new = out[0, len(ids):].tolist()
        text = gc.tok.decode(new, skip_special_tokens=False)
        p = parse_tool_call(text, r["tools"])
        cid = (p.get("arguments") or {}).get("choice_id") if p["ok"] else None
        gens.append({"example_id": r["example_id"], "ok": p["ok"], "error": p["error"], "choice_id": cid,
                     "legal": cid in r["id_map"], "agree": cid == r["choice_id"], "n_new": len(new),
                     "reasoning_frames": p["n_reasoning_frames"], "s": round(time.time() - ts, 2),
                     "raw_head": text[:160] if not p["ok"] else None})
    gen_s = time.time() - t2
    summary = {
        "mode": "teacher", "adapter": str(args.adapter), "n": len(rows), "load_s": round(load_s, 1),
        "teacher_forced_s": round(tf_s, 1), "generation_s": round(gen_s, 1),
        "tuned": dict(summarize_scores(tuned), legal_mass=round(statistics.mean(s["legal_mass"] for s in tuned), 4)),
        "base_same_prefix": dict(summarize_scores(based), legal_mass=round(statistics.mean(s["legal_mass"] for s in based), 4)),
        "baselines": baselines(rows),
        "hf_generation": {"n": len(gens), "parsed_ok": sum(g["ok"] for g in gens), "legal": sum(g["legal"] for g in gens),
                          "malformed": sum(not g["ok"] for g in gens), "agree": sum(g["agree"] for g in gens),
                          "errors": sorted({g["error"] for g in gens if g["error"]}),
                          "median_new_tokens": pct([g["n_new"] for g in gens], .5), "median_s": pct([g["s"] for g in gens], .5)},
    }
    Path(args.out).write_text(json.dumps(summary, indent=1))
    with open(Path(args.out).with_suffix(".rows.jsonl"), "w") as f:
        for x in per_row + gens:
            f.write(json.dumps(x) + "\n")
    print(json.dumps(summary, indent=1))


# ----------------------------------------------------------------------------- serve (llama.cpp)

def post(url, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def serve(args):
    gc = GlimmerChat(args.tokenizer_dir)
    rows = load_rows(args.eval, args.n)
    base = args.url.rstrip("/")
    samp = {"temperature": 0, "top_k": 40, "top_p": 1, "seed": 42}
    post(base + "/v1/chat/completions", {"messages": rows[0]["messages"], "tools": rows[0]["tools"],
                                         "tool_choice": "required", "max_tokens": 16, **samp})  # warm-up
    chat, raw = [], []
    for r in rows:
        rec = {"example_id": r["example_id"], "choice_id": r["choice_id"], "n_legal": len(r["id_map"])}
        t = time.time()
        try:
            resp = post(base + "/v1/chat/completions", {"messages": r["messages"], "tools": r["tools"],
                                                        "tool_choice": "required", "max_tokens": args.max_new_tokens, **samp})
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            cid = None
            if len(calls) == 1 and calls[0]["function"]["name"] == "submit_action":
                cid = json.loads(calls[0]["function"]["arguments"]).get("choice_id")
            tm = resp.get("timings") or {}
            rec.update(status="ok", finish=resp["choices"][0].get("finish_reason"), n_calls=len(calls), choice=cid,
                       legal=cid in r["id_map"], agree=cid == r["choice_id"], reasoning_chars=len(msg.get("reasoning_content") or ""),
                       content_chars=len(msg.get("content") or ""), completion_tokens=(resp.get("usage") or {}).get("completion_tokens"),
                       prompt_tokens=(resp.get("usage") or {}).get("prompt_tokens"), ttft_s=(tm.get("prompt_ms") or 0) / 1000,
                       decode_tps=tm.get("predicted_per_second"), wall_s=round(time.time() - t, 3))
        except Exception as e:  # noqa: BLE001 - any failure counts as an illegal decision
            rec.update(status=f"error: {type(e).__name__}: {str(e)[:200]}", legal=False, agree=False, wall_s=round(time.time() - t, 3))
        chat.append(rec)
        if args.raw:
            ids = gc.ids(gc.render(r["messages"], r["tools"], add_generation_prompt=True))
            t = time.time()
            rr = {"example_id": r["example_id"]}
            try:
                resp = post(base + "/completion", {"prompt": ids, "n_predict": args.max_new_tokens, "return_tokens": True,
                                                    "cache_prompt": True, **samp})
                toks = resp.get("tokens") or []
                text = gc.tok.decode(toks, skip_special_tokens=False) if toks else resp.get("content", "")
                if (resp.get("stop_type") == "eos" or resp.get("stopped_eos")) and not text.endswith(EOT) \
                        and not text.endswith("<|end_of_text|>"):
                    text += EOT  # the stop token itself is not returned
                p = parse_tool_call(text, r["tools"])
                cid = (p.get("arguments") or {}).get("choice_id") if p["ok"] else None
                rr.update(ok=p["ok"], error=p["error"], choice=cid, legal=cid in r["id_map"], agree=cid == r["choice_id"],
                          reasoning_frames=p["n_reasoning_frames"], n_tokens=len(toks), stop_type=resp.get("stop_type"),
                          wall_s=round(time.time() - t, 3), raw_head=None if p["ok"] else text[:200])
            except Exception as e:  # noqa: BLE001
                rr.update(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}", legal=False, agree=False)
            raw.append(rr)
    n = len(chat)
    legal = sum(bool(c.get("legal")) for c in chat)
    summary = {
        "mode": "serve", "label": args.label, "url": base, "n": n,
        "chat": {"legal": legal, "legal_rate": round(legal / n, 4), "gate_legal_ge_0.98": legal / n >= GATE_LEGAL,
                 "agree": sum(bool(c.get("agree")) for c in chat), "agree_rate": round(sum(bool(c.get("agree")) for c in chat) / n, 4),
                 "errors": sum(not c["status"].startswith("ok") for c in chat),
                 "finish": dict((k, sum(1 for c in chat if c.get("finish") == k)) for k in {c.get("finish") for c in chat}),
                 "with_reasoning_content": sum(1 for c in chat if c.get("reasoning_chars")),
                 "wall_p50_s": pct([c.get("wall_s") for c in chat], .5), "wall_p95_s": pct([c.get("wall_s") for c in chat], .95),
                 "ttft_p50_s": pct([c.get("ttft_s") for c in chat], .5), "decode_tps_p50": pct([c.get("decode_tps") for c in chat], .5),
                 "completion_tokens_p50": pct([c.get("completion_tokens") for c in chat], .5),
                 "prompt_tokens_p50": pct([c.get("prompt_tokens") for c in chat], .5)},
        "baselines": baselines(rows),
    }
    if raw:
        summary["raw_no_grammar"] = {"parsed_ok": sum(bool(x.get("ok")) for x in raw), "malformed": sum(not x.get("ok") for x in raw),
                                     "legal": sum(bool(x.get("legal")) for x in raw), "agree": sum(bool(x.get("agree")) for x in raw),
                                     "errors": dict((e, sum(1 for x in raw if x.get("error") == e)) for e in {x.get("error") for x in raw} if e),
                                     "wall_p50_s": pct([x.get("wall_s") for x in raw], .5)}
    Path(args.out).write_text(json.dumps(summary, indent=1))
    with open(Path(args.out).with_suffix(".rows.jsonl"), "w") as f:
        for x in chat + raw:
            f.write(json.dumps(x) + "\n")
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    t = sub.add_parser("teacher")
    t.add_argument("--snapshot", required=True)
    t.add_argument("--adapter", required=True)
    t.add_argument("--eval", required=True)
    t.add_argument("--n", type=int, default=200)
    t.add_argument("--gen-n", type=int, default=24)
    t.add_argument("--max-new-tokens", type=int, default=96)
    t.add_argument("--out", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--url", required=True)
    s.add_argument("--tokenizer-dir", default=None)
    s.add_argument("--eval", required=True)
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--max-new-tokens", type=int, default=256)
    s.add_argument("--raw", action="store_true", help="also run the no-grammar /completion path")
    s.add_argument("--label", default="")
    s.add_argument("--out", required=True)
    args = ap.parse_args()
    teacher(args) if args.mode == "teacher" else serve(args)


if __name__ == "__main__":
    main()
