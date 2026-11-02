#!/usr/bin/env python3
"""Mutation test of the explanation checker (rules: docs/checker-test.md). No model calls."""
import copy, glob, json, os, random, re, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT); sys.path.insert(0, "tools/agent")
from verifier import verify
from tools import ToolSession, field_kind
from lexicon import NUMERIC_KINDS, PLACEHOLDER_RE
from names import find_entities, to_id
def strip(o):
    if isinstance(o, dict): return {k: strip(v) for k, v in o.items() if k != "_raw"}
    if isinstance(o, list): return [strip(v) for v in o]
    return o
def replay(review, trace):
    s = ToolSession(review)
    ok = True
    for c in trace:
        out = s.call(c["name"], c["args"])
        if json.loads(json.dumps(strip(out))) != json.loads(json.dumps(c["output"])): ok = False
    return s, ok
def from_trace(review, trace):
    """Session rebuilt from the stored tool outputs: the records the model saw, with no recomputation."""
    s = ToolSession(review)
    for idx, c in enumerate(trace):
        ents = set(); out = c["output"]
        for rec in ToolSession._iter_records(out):
            rid = rec["id"]; s.records[rid] = rec; s.record_call[rid] = idx
            for k, v in rec.items():
                if field_kind(k) == "name" and isinstance(v, str):
                    ents.update(to_id(n) for _, n in s._entities_of(k, v, rec))
        s.calls.append({"name": c["name"], "args": c["args"], "output": out, "entities": sorted(ents)})
    return s
def sources():
    D = "scratch_ds/evals/a1-v2-base-20261028T133353Z/record-base-v2-20261028T150852Z.items.jsonl"
    for l in open(D):
        r = json.loads(l); res = r["result"]
        yield {"src": "a1v2", "id": r["i"], "q": r["question"], "review": json.load(open(r["review"])), "trace": res["trace"], "attempts": res["attempts"]}
    for f in sorted(glob.glob("app/data/showcases/showcase-*.explain.json")):
        rev = json.load(open(f.replace(".explain.", ".review.")))
        for q, x in json.load(open(f)).items():
            yield {"src": "showcase", "id": Path(f).name[:10] + q, "q": x["question"], "review": rev, "trace": x["trace"], "payload": None, "x": x}
WORDS = {3: "three", 7: "seven", 12: "twelve", 20: "twenty", 30: "thirty", 50: "fifty"}
LABEL = [(r"\bluck points\b", "win chance"), (r"\bwin chance\b", "luck points"), (r"\bluck\b", "win chance")]
FLIP = [("toward you", "against you"), ("against you", "toward you"), ("in your favour", "against you"), ("helped", "hurt"), ("hurt", "helped")]
OUTSIDE = ["Ogerpon", "Sneasler", "Cinderace", "Volcarona", "Dragapult", "Slowking", "Hatterene"]
def num_slots(s, sess):
    out = []
    for nm in PLACEHOLDER_RE.findall(s["text"]):
        ref = (s.get("slots") or {}).get(nm)
        try: rec, fld, val = sess.resolve(ref)
        except KeyError: continue
        k = field_kind(fld)
        if k in NUMERIC_KINDS and isinstance(val, (int, float)): out.append((nm, ref, rec, fld, val, k))
    return out
def all_fields(sess):
    for rid, rec in sess.records.items():
        for f, v in rec.items():
            if f in ("id", "_raw") or isinstance(v, (dict, list)): continue
            yield rid, f, v, field_kind(f)
def mutants(p, sess):
    """Yield (mutation, site, payload)."""
    fields = list(all_fields(sess))
    for i, s in enumerate(p["sentences"]):
        def mk(text=None, slots=None):
            q = copy.deepcopy(p)
            if text is not None: q["sentences"][i]["text"] = text
            if slots is not None: q["sentences"][i]["slots"] = slots
            return q
        for nm, ref, rec, fld, val, k in num_slots(s, sess):
            ph = "{" + nm + "}"; site = (i, nm)
            yield "M1", site, mk(s["text"].replace(ph, str(int(abs(val) if abs(val) >= 1 else val * 100) + 7)))
            w = WORDS[30] if abs(val) != 30 else WORDS[20]
            yield "M3", site, mk(s["text"].replace(ph, w))
            rid = ref.split(".")[0]
            diff = [x for x in fields if x[3] != k and x[3] in NUMERIC_KINDS and x[2] != val and isinstance(x[2], (int, float))]
            if diff:
                x = diff[hash(site) % len(diff)] if False else diff[0]
                yield "M4", site, mk(slots={**s["slots"], nm: f"{x[0]}.{x[1]}"})
            same = [x for x in fields if x[1] == fld and x[0] != rid and x[2] != val]
            if same: yield "M6", site, mk(slots={**s["slots"], nm: f"{same[0][0]}.{fld}"})
            sk = [x for x in fields if x[3] == k and x[1] != fld and x[2] != val and isinstance(x[2], (int, float))]
            if sk: yield "M7", site, mk(slots={**s["slots"], nm: f"{sk[0][0]}.{sk[0][1]}"})
            yield "M10", site, mk(slots={**s["slots"], nm: f"#999.{fld}"})
        yield "M2", (i, "*"), mk(s["text"].rstrip() + " It was worth 23 luck points.")
        for rx, to in LABEL:
            if re.search(rx, s["text"], re.I) and PLACEHOLDER_RE.search(s["text"]):
                t = re.sub(rx, to, s["text"], count=1, flags=re.I)
                if t != s["text"]: yield "M5", (i, rx), mk(t); break
        for a, b in FLIP:
            if a in s["text"]: yield "M8", (i, a), mk(s["text"].replace(a, b, 1)); break
        yield "X1", (i, "*"), mk(s["text"].rstrip() + " A critical hit decided this.")
        yield "X2", (i, "*"), mk(s["text"].rstrip() + " Luck mattered far more than the choices of both players.")
        for rx, to in LABEL:
            mm = re.search(rx, s["text"], re.I)
            if mm and num_slots(s, sess):
                near = any(abs(mm.start() - s["text"].find("{" + n[0] + "}")) <= 25 for n in num_slots(s, sess))
                if near: yield "X3", (i, rx), mk(s["text"][:mm.start()] + to + s["text"][mm.end():])
                break
        ents = find_entities(PLACEHOLDER_RE.sub(lambda m: "X" * len(m.group(0)), s["text"]))
        for nm, kind, eid, st in ents:
            cited = {e for c in sess.calls for e in c["entities"]}
            sub = next((o for o in OUTSIDE if to_id(o) not in cited), None)
            if sub: yield "M9", (i, nm), mk(s["text"].replace(nm, sub, 1))
def boot(per, reps=2000, seed=0):
    rnd = random.Random(seed); keys = list(per); v = []
    for _ in range(reps):
        c = n = 0
        for _ in keys:
            a, b = per[keys[rnd.randrange(len(keys))]]; c += a; n += b
        v.append(c / n if n else 0)
    v.sort(); return round(v[50], 3), round(v[1949], 3)
def main():
    res = defaultdict(lambda: defaultdict(lambda: [0, 0])); rules = defaultdict(Counter); ex = defaultdict(list)
    bad = Counter(); clean = 0; raw_rows = []
    for it in sources():
        _, ok = replay(it["review"], it["trace"])
        if not ok: bad["replay_mismatch_info_" + it["src"]] += 1
        sess = from_trace(it["review"], it["trace"])
        if it["src"] == "a1v2":
            good = [a for a in it["attempts"] if a["report"]["pass"]]
            raw = it["attempts"][0]; raw_rows.append((it["id"], raw["report"]["pass"], raw, it["trace"]))
            if not good: bad["no_passing_draft"] += 1; continue
            p = good[0]["payload"]
        else:
            ss = it["x"]["rendered"]["sentences"] if it["x"].get("rendered") else []
            p = {"sentences": [{"text": s["template"], "slots": s["slots"]} for s in ss]}
            if not ss: bad["no_rendered"] += 1; continue
        if not verify(p, sess)["pass"]: bad["base_fails_on_replay_" + it["src"]] += 1; continue
        clean += 1; key = f'{it["src"]}/{it["id"]}'
        for m, site, q in mutants(p, sess):
            if q == p: bad["noop_" + m] += 1; continue
            rep = verify(q, sess)
            res[m][key][1] += 1
            if not rep["pass"]:
                res[m][key][0] += 1; rules[m].update({f["rule"] for f in rep["failures"]})
            elif len(ex[m]) < 3: ex[m].append(q["sentences"][site[0]]["text"][:110])
    print("base drafts", clean, dict(bad))
    for m in sorted(res, key=lambda x: (x[0], int(x[1:]))):
        c = sum(a for a, b in res[m].values()); n = sum(b for a, b in res[m].values())
        print(f"{m}: caught {c}/{n} = {c/n:.3f} CI {boot(res[m])} drafts {len(res[m])} rules {dict(rules[m])} missed e.g. {ex[m][:2]}")
    # values for the real rejections
    print("--- rejected raw drafts: cited values")
    for i, ok, raw, trace in raw_rows:
        if ok: continue
        print(i, {k: v for k, v in trace[0]["output"].items() if k in ("luck_pts", "choices_pts", "result", "start_wp_pct", "end")}, json.dumps([c["output"] for c in trace if c["name"] != "get_summary"])[:330])
    print("raw pass", sum(r[1] for r in raw_rows), "of", len(raw_rows))
main()
