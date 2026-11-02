# HardRead: was it luck, or was it you?

**Chess players get an engine review after every game. Competitive Pokémon players don't, because you can't see the opponent's moves and held items, and dice decide many turns. HardRead reviews a finished game anyway and estimates how much was luck.**

It's for competitive players who want to see which turns were dice and which decisions deserve a second look. It reviews Gen 9 OU, the most popular competitive format, from a [Pokémon Showdown](https://pokemonshowdown.com) replay.

**Try it:** [HardRead site](https://touchetpe.github.io/hardread-site/) (saved showcases, plus a link to live review when it's online) · Theme: *Build for Your World*

Built during the Meta Global AI Developer Hackathon with Meta's open **Muse Glimmer** model. Fan-made and not affiliated with Nintendo, The Pokémon Company or Game Freak. 18+.

## What it does

Paste a finished replay. In one to three minutes you get:

- **The whole game as one sum:** `53% start + 73 luck − 26 choices = 100% (win)`. You began with a 53% chance, luck added an estimated 73 points, and decisions took away 26. Luck means random events: critical hits, missed attacks, damage rolls and speed ties. Choices is everything else, for both players together.
- **Win chance turn by turn,** with the biggest luck moments priced against what could have happened instead.
- **Up to three moves the engine would have played differently,** with how uncertain that is, never added up into a score, and **the one decision to look at again.**
- **Muse Glimmer's explanation.** Ask what the turning point was, and it looks up the turn itself and explains what happened.
- **A test of your read.** Add your team and say what you believed about the opponent's set. HardRead checks your move against the alternatives under that belief.

It only reviews finished games. It never helps during a live battle.

## How it differs from a typical AI analyzer

Most analyzers paste a game into a language model and return whatever it says. On the review page you can open the comparison and see that for yourself.

| | One-prompt analyzer | HardRead |
|---|---|---|
| **Who does the analysis** | A language model reads the log. | [Foul Play](https://github.com/pmariglia/foul-play), an open-source search engine that won the 2025 PokéAgent Gen 9 OU track, samples the hidden sets and searches the continuations. |
| **Where numbers come from** | The model writes them. | The model **cannot write a number**. It writes sentences with empty slots that point at engine results, and code fills in the values. |
| **What stops an invented number** | Nothing. | A checker rejects any draft with an uncited or wrongly typed number, or a verdict the engine didn't give. The model gets one repair, then a labeled template takes over. |
| **What you can inspect** | The final text. | Every lookup Glimmer made, every rejected draft and the source of every number. |

## How Meta's models are used

**Muse Glimmer-30B runs the explanations as an agent.** It chooses which read-only engine tools to call, then submits an explanation that must pass the number checks. The same open weights run on a laptop or a rented GPU.

Results on a fixed set of 60 questions about 20 games (base model, no fine-tuning):

| Measure | Result |
|---|---|
| Passed the number checks on the first draft | 41/60 (68%) |
| Passed after at most one repair | 59/60 (98%) |
| Sentences flagged in a blind review | 3/60 (5%; 95% CI 1.7–13.7%) |

Passing means each number comes from an engine result and has the right type. It doesn't mean the explanation is true: a real number can still be the wrong one for its sentence, and plain-word claims aren't checked. That's why we also ran the blind review, which was done by an AI reviewer. The human label check is still pending, so we don't claim the prose is accurate. An earlier version of the same test passed 49/60 after repair, with 12/60 sentences flagged.

Those figures don't cover the newer turning-point question. On 14 game-sides it passed 9/14 on the first draft and 14/14 after repair, and an AI read judged 2 of the 14 misleading. There were no held-out games, and the chosen turn can change with the wording. One saved showcase uses the labeled template for this question because its answer was misleading. See [RESULTS.md](RESULTS.md).

**Test of the checker (not a model result):** In a scripted test on 83 real drafts, the checker rejected every invented or uncited number (745/745) and every citation of the wrong kind of value (285/285). It rejected only 8% of real numbers cited from the wrong record, and it does not check claims in plain words. The 745/745 holds by design, since no digit may appear outside a slot. [Method and full table](docs/checker-test.md).

For comparison, the same model with only the battle log and no engine put numbers in 27 of 30 answers, with nothing linking them to a source. Asked for the biggest piece of luck, it named the same turn as the engine in 3 of 10 games. That is a small sample, and the engine's estimate is not ground truth.

**We also fine-tuned Glimmer to play.** Trained on a search engine's decisions, it won **178/200 games (89%; 95% CI 84–93%)** against a heuristic bot called Abyssal, with 0 illegal moves in 5,202 decisions. Those teams were partly seen in development, so this doesn't measure general strength. The fine-tuned model is a separate experiment and doesn't write the explanations.

## What we measured, including what failed

| Question | Result |
|---|---|
| Does the luck meter behave? | In bot self-play over 500 games, the two players' luck mirrors at r = −0.81, and subtracting measured luck cut outcome variance by 23%. But when one bot had a real information edge, about a fifth of that edge showed up as luck (one test level). |
| How often does the read test wrongly flag a strong move? | In bot self-play, on 184 moves chosen by a deeper search, it called 18 clear misplays (9.8%; 95% CI 6.3–14.9%), all wrong by the test's design. Our first two settings failed, at 75% and 21%. This wasn't measured on human games. |
| How much does hidden information matter to the engine? | Between bots, giving the engine the opponent's full sets instead of 25% of them was worth 14.67 win-rate points (95% CI ±4.96) over 300 side-swapped pairings, 600 games. This measures forced revelation, not the value of a human's reads. |
| Did our engine changes beat Foul Play? | **No.** A pre-registered sealed test of 10,000 games gave 50.05% (95% CI 49.16–50.94%, p = .91). |
| Did a better calibration help? | **No.** It failed its gate and was not switched on. |

**Limits you should know:**
- "Choices" is what remains after estimated luck. It includes both players, later information and model error, so it isn't a skill score.
- Without your team export, your own sets are assumed, and that can change a verdict.
- Roaring Moon joined OU on 30 October, after the usage statistics we use were made. Reviews of games with it still work, but the engine knows less about what it may be holding.
- Luck is an estimate. In a test where one bot had a real information edge, about a fifth of that edge showed up as luck, so a large luck number doesn't rule out skill. One test level so far, cause unknown; see [RESULTS.md](RESULTS.md#luck-information-edge-20261102).
- We haven't yet run a study with human players.

Full lab notes: [RESULTS.md](RESULTS.md) (detailed, in working shorthand). Pre-registration: [PREREGISTRATION.md](PREREGISTRATION.md).

## What was built here

**Built during the hackathon:** replay reconstruction, the luck meter, the start/luck/choices bridge, verdicts, the Glimmer agent and checker, the web app and the fine-tuning pipeline.

**Before kickoff (disclosed):** about two hours of self-play harness work on kickoff morning: a match runner, cloud job scripts, team validation, an oracle hook (lets a bot see the opponent's sets) and decision logging. None of it is a demo feature, but two pieces carried over: the review engine reuses and extends the decision-log serializer, and the hidden-information experiment above uses the oracle hook. Another 44 commits fall between kickoff on 26 October and the guide's 27 October building start. [BUILT_DURING_HACKATHON.md](BUILT_DURING_HACKATHON.md) lists first commits with timestamps, and explains a mismatch in the earlier pre-kickoff count.

**Not ours:** the search engine and its tournament win belong to Foul Play by pmariglia (GPL-3.0). Our bot on the public ladder ran on it: the account's best photographed rank was #157 (Elo 1727, 30 October 2026; an earlier snapshot was #199), other agents also used that account, and it later left the top 500. See [THIRD_PARTY.md](THIRD_PARTY.md) and [DATA.md](DATA.md).

## Run it

```bash
python3 app/server.py   # http://127.0.0.1:18051
```

Showcases work with just `fastapi` and `uvicorn`. To review new games, clone [the engine fork](FORK_URL_PENDING) into `foul-play/` and install its `requirements.txt` in a Python 3.13 environment (it needs Rust 1.89), then set `HARDREAD_FP_PYTHON` to that Python. Live explanations need a `llama-server` running Muse Glimmer. More in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
