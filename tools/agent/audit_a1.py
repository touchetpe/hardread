#!/usr/bin/env python3
"""Misleading-sentence audit for A1 (EVAL_PROTOCOL_AGENT.md §4): blinded sheet, then scoring.

  python3 tools/agent/audit_a1.py sheet scratch_ds/evals/faithfulness_v1.items.jsonl --out scratch_ds/evals/audit_sheet.jsonl
  python3 tools/agent/audit_a1.py score scratch_ds/evals/audit_sheet.jsonl --labels agent_labels.jsonl \
      [--owner owner_labels.jsonl] --out scratch_ds/evals/audit_v1.json

Population: every rendered sentence of the answers that passed with Glimmer text (raw or repaired); template
answers are excluded (fixed code). Sample: 60 sentences with random.Random(20261027) (all if fewer); owner
subset: 15 of the sheet with random.Random(15). Each sheet row shows only the question, the sentence and the
JSON of the tool outputs it cites: no arm, no raw/repaired status, no verifier report (blinding).
Labels files: one {"audit_id": ..., "misleading": true|false, "category": "a".."f" or null, "note": ...} per line.
Categories (protocol): a wrong attribution, b wrong direction, c unsupported causal/decisive claim, d judgement
without a verdict, e certainty below one half, f wrong move/Pokémon/turn association.
"""
import argparse
import json
import math
import random
from pathlib import Path

SEED_SAMPLE, SEED_OWNER, N_SAMPLE, N_OWNER = 20261027, 15, 60, 15


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def sheet(items_path, out):
    pop = []
    for line in open(items_path):
        row = json.loads(line)
        res = row["result"]
        if res.get("final") not in ("glimmer_raw", "glimmer_repaired"):
            continue
        trace = res.get("trace") or []
        for snt in res["rendered"]["sentences"]:
            cited_ids = {c["ref"].split(".")[0] for c in snt["citations"] if c.get("ref")}
            outs = [t for t in trace if any(f'"id": "{rid}"' in json.dumps(t["output"]) for rid in cited_ids)]
            pop.append({"question": row["question"], "sentence": snt["text"],
                        "cited_tool_outputs": [{"tool": t["name"], "args": t["args"], "output": t["output"]}
                                               for t in outs],
                        "_src": {"item": row["i"], "sentence": snt["i"]}})
    rng = random.Random(SEED_SAMPLE)
    sample = pop if len(pop) <= N_SAMPLE else rng.sample(pop, N_SAMPLE)
    owner = set(random.Random(SEED_OWNER).sample(range(len(sample)), min(N_OWNER, len(sample))))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f, open(str(out) + ".key.jsonl", "w") as fk:  # the key (item, sentence) stays apart
        for i, r in enumerate(sample):
            src = r.pop("_src")
            f.write(json.dumps(dict(r, audit_id=f"s{i:02d}", owner_spot_check=i in owner), ensure_ascii=False) + "\n")
            fk.write(json.dumps({"audit_id": f"s{i:02d}", **src}) + "\n")
    print(json.dumps({"population": len(pop), "sample": len(sample), "owner_subset": len(owner), "out": out}))


def _labels(path):
    return {r["audit_id"]: r for r in (json.loads(x) for x in open(path) if x.strip())}


def score(sheet_path, labels_path, owner_path, out):
    rows = [json.loads(x) for x in open(sheet_path) if x.strip()]
    lab = _labels(labels_path)
    missing = [r["audit_id"] for r in rows if r["audit_id"] not in lab]
    k = sum(1 for r in rows if lab.get(r["audit_id"], {}).get("misleading"))
    n = len(rows) - len(missing)
    cats = {}
    for r in rows:
        x = lab.get(r["audit_id"], {})
        if x.get("misleading"):
            cats[x.get("category") or "?"] = cats.get(x.get("category") or "?", 0) + 1
    res = {"n_audited": n, "missing_labels": missing, "misleading": k, "rate": round(k / n, 4) if n else None,
           "ci95": wilson(k, n), "by_category": cats, "gate_misleading_le_5pct": (k / n <= 0.05) if n else None}
    if owner_path:
        own = _labels(owner_path)
        both = [r["audit_id"] for r in rows if r.get("owner_spot_check") and r["audit_id"] in own
                and r["audit_id"] in lab]
        res["owner_spot_check"] = {"n": len(both), "disagreements": sum(
            1 for a in both if bool(own[a].get("misleading")) != bool(lab[a].get("misleading")))}
    Path(out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sheet")
    a.add_argument("items")
    a.add_argument("--out", required=True)
    b = sub.add_parser("score")
    b.add_argument("sheet")
    b.add_argument("--labels", required=True)
    b.add_argument("--owner")
    b.add_argument("--out", required=True)
    x = ap.parse_args()
    if x.cmd == "sheet":
        sheet(x.items, x.out)
    else:
        score(x.sheet, x.labels, x.owner, x.out)


if __name__ == "__main__":
    main()
