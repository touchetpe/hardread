#!/usr/bin/env python3
"""Check that the history the policy saw at play time equals the training-time history.

For an arena run made with --player llm --log-history, rebuilds every decision's history the
way the SFT data does (tools/dataset/extract.py history_cuts on the server's saved battle log,
then fp.hardread_history.canonical_history) and compares it with the history logged by
fp/hardread_policy.py. Also reports how the raw omniscient slice differs (exact opponent HP).

  python tools/arena/check_history_parity.py runs/<run_id>
"""
import json
import sys
from pathlib import Path

HR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HR / "tools" / "dataset"))
sys.path.insert(0, str(HR / "foul-play"))
from extract import history_cuts  # noqa: E402
from fp.hardread_history import canonical_history  # noqa: E402

try:  # the real SFT serializer, to compare rendered prompts too
    sys.path.insert(0, str(HR / "tools" / "sft"))
    import serialize as S  # noqa: E402
except Exception:  # noqa: BLE001
    S = None


def chunk_cut(log, cut, k):
    """Suggested extract.py fix for later decisions (k >= 1): cut at the start of the update
    chunk that follows the decision (the "|" + "|t:|" pair Showdown writes when choices are
    committed), not just before our switch line: lines in between (e.g. "-end ... [silent]",
    Natural Cure, Regenerator-style effects of our own switch-out) happen after the decision."""
    if cut is None or k == 0:
        return cut
    j = cut - 1
    while j > 0 and not log[j].startswith("|turn|"):
        if log[j].startswith("|t:|"):
            return j - 1 if log[j - 1] == "|" else j
        j -= 1
    return cut


def main():
    run = Path(sys.argv[1])
    games = [json.loads(l) for l in (run / "games.jsonl").read_text().splitlines() if l.strip()]
    total = same = raw_diff = same_fixed = 0
    rendered = rendered_same = rendered_same_fixed = 0
    for g in games:
        if not g.get("battle_log"):
            continue
        d = json.loads((run / "battlelogs" / g["battle_log"]).read_text())
        player = "p1" if d["p1"] == g["a"] else "p2"
        recs = [json.loads(l) for l in (run / f"g{g['game']:04d}_hr.policy.jsonl").read_text().splitlines()
                if l.strip()]
        recs = [r for r in recs if r.get("event") == "decision"]
        if recs and "history" not in recs[0]:
            raise SystemExit("run made without --log-history")
        cuts = history_cuts(d["log"], player, [{"turn": r["turn"], "team_preview": r["team_preview"],
                                                "choice": r["action"]} for r in recs])
        for r, cut in zip(recs, cuts):
            total += 1
            train = canonical_history(d["log"][:cut], player) if cut is not None else None
            if train == r["history"]:
                same += 1
            else:
                a, b = train or [], r["history"]
                i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
                print(f"MISMATCH g{g['game']:04d} turn {r['turn']} k {r['k']}: train {len(a)} lines, "
                      f"serve {len(b)}; first diff at {i}: train={a[i:i+2]} serve={b[i:i+2]}")
            c2 = chunk_cut(d["log"], cut, r["k"])
            if c2 is not None and canonical_history(d["log"][:c2], player) == r["history"]:
                same_fixed += 1
            if cut is not None and d["log"][:cut] != r["history"]:
                raw_diff += 1
            if S is not None and cut is not None and r.get("view") is not None:
                # Training renders the RAW omniscient slice (build_sft.py); serving renders our history.
                args = (r["legal"], player, r["turn"], r["k"], r["team_preview"])
                serve = S.build_example(r["view"], r["history"], *args)["messages"]
                rendered += 1
                if S.build_example(r["view"], d["log"][:cut], *args)["messages"] == serve:
                    rendered_same += 1
                elif S.build_example(r["view"], d["log"][:c2], *args)["messages"] == serve:
                    rendered_same_fixed += 1
    print(json.dumps({"decisions": total, "canonical_equal": same, "canonical_equal_with_chunk_cut": same_fixed,
                      "raw_omniscient_slice_differs": raw_diff,
                      "rendered_compared": rendered, "rendered_equal_raw_training_slice": rendered_same,
                      "rendered_equal_only_with_chunk_cut": rendered_same_fixed}))
    return 0 if total and same == total else 1


if __name__ == "__main__":
    sys.exit(main())
