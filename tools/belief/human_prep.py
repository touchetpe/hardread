#!/usr/bin/env python3
"""Human partial-label examples (tools/metamon/partial_targets.py) -> capped train/dev/test files for belief v1.

- drops every row whose opponent roster is an hr_val roster or a reserved/sealed roster (targets.excluded_rosters):
  those rosters are never trained on (PREREGISTRATION A1) and the reserved ones never touched;
- keeps at most --max-per-mon decisions per (game, side, opponent Pokemon) (consecutive decisions repeat the same state);
- splits by the example's roster-hash split (train / dev / test, as in targets.py).

  python3 tools/belief/human_prep.py scratch_ds/metamon/beliefs/sample20k.examples.jsonl.gz \
      scratch_ds/belief/human/rest*.examples.jsonl.gz --out-dir scratch_ds/belief/human --max-per-mon 3
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from targets import capped, excluded_rosters, iter_jsonl, open_text  # noqa: E402
from roster_hash import roster_digest  # noqa: E402


def is_excluded_example(example, excluded_digests):
    """Hash an incoming roster before checking the hr_val/reserved exclusion union."""
    return roster_digest(example["roster"]) in excluded_digests


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-per-mon", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    excl = excluded_rosters()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    st = Counter()
    games = defaultdict(set)
    rosters = defaultdict(set)
    excl_games = set()
    outs = {s: open_text(out_dir / "human.{}.jsonl.gz".format(s), "wt") for s in ("train", "dev", "test")}

    def gen():
        for path in a.inputs:
            for e in iter_jsonl([path]):
                st["rows_in"] += 1
                if is_excluded_example(e, excl):
                    st["rows_excluded_roster"] += 1
                    excl_games.add(e["game"])
                    continue
                yield e

    for e in capped(gen(), a.max_per_mon, a.seed):
        outs[e["split"]].write(json.dumps(e, separators=(",", ":")) + "\n")
        st["rows_" + e["split"]] += 1
        games[e["split"]].add(e["game"])
        rosters[e["split"]].add(e["roster"])
        for k in ("item", "ability", "tera"):
            st["scored_{}_{}".format(e["split"], k)] += bool(e["scored"][k])
        st["moves_pos_" + e["split"]] += e["scored"]["moves"]
    for f in outs.values():
        f.close()
    rep = {"stats": dict(st), "games": {k: len(v) for k, v in games.items()},
           "rosters": {k: len(v) for k, v in rosters.items()}, "games_with_excluded_roster_rows": len(excl_games),
           "excluded_roster_keys": len(excl), "max_per_mon": a.max_per_mon}
    (out_dir / "human_prep.report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
