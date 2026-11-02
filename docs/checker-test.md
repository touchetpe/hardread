# Test of the explanation checker

This is a test of the checker in `tools/agent/verifier.py` (`agent-verifier-v6`). It isn't a model result, and it made no model calls. Rules were written before the run; the script is [`tools/sci/mutate_checker.py`](../tools/sci/mutate_checker.py).

**Result:** In a scripted test on 83 real drafts, the checker rejected every invented or uncited number (745/745) and every citation of the wrong kind of value (285/285). It rejected only 8% of real numbers cited from the wrong record, and it does not check claims in plain words.

The 745/745 holds by design, since no digit or number word may appear outside a slot.

**Method:** we took 83 drafts that had passed the checker (59 from a fixed 60-question evaluation, 24 saved showcase answers), injected one error at a time, and counted how many the checker rejected. Sessions were rebuilt from the stored tool outputs. On the 25 sources that also replayed exactly, the results matched. The 59 evaluation drafts are in private records, not in this repository, so the script needs those files to run in full.

| Injected error | Rejected | Share (95% CI) |
|---|---:|---:|
| Wrong number written as a literal | 285/285 | 100%, by design |
| Added uncited number | 175/175 | 100%, by design |
| Number written as a word | 285/285 | 100%, by design |
| Citation to a field of another kind | 285/285 | 100% |
| Dangling citation | 285/285 | 100% |
| Flipped direction | 57/57 | 100% |
| Species or move from outside the cited results | 4/4 | too few to say |
| Value labeled as the wrong quantity | 73/104 | 70% (64–77%) |
| Same kind, wrong field (e.g. win chance before vs after) | 106/209 | 51% (46–55%) |
| **Same field, wrong record (e.g. another turn's luck)** | **15/194** | **8% (5–11%)** |

Two more checks were added after seeing those results, so they're exploratory: a false claim in plain words with no number ("A critical hit decided this.") was rejected 20/175 times, and a false comparison ("Luck mattered far more...") 20/175 times, each 11% (8–16%).

**What this means:** passing means every number comes from a tool output and has the right type. It doesn't mean the explanation is true.
