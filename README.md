# HardRead: was it luck, or was it you?

**Chess players get an engine review after every game. Pokémon has luck calculators and win-chance tools, but none we found splits a game into start, luck and both players' choices using search over hidden sets. You can't see the opponent's moves and held items, and dice decide many turns. HardRead reviews a finished game anyway and estimates how much was luck. It isn't a chatbot reading a log: a search engine does the analysis, and the language model can only explain the engine's results.**

It's for competitive players who want to see which turns were dice and which decisions deserve a second look. I designed it for my own games. It reviews Gen 9 OU, a main competitive format, from a [Pokémon Showdown](https://pokemonshowdown.com) replay.

**Try it:** [HardRead site](https://touchetpe.github.io/hardread-site/) (saved showcases, plus a link to live review when it's online) · Theme: *Build for Your World*

The review, luck meter, read test, explanation agent and checker were built during the Meta Global AI Developer Hackathon. I designed and directed this project; AI coding agents (Claude Code and Codex) wrote much of the code. The search engine is the open-source Foul Play, and the language model is Meta's open **Muse Glimmer**. [What was built here](#what-was-built-here) has the details. Fan-made and not affiliated with Nintendo, The Pokémon Company or Game Freak. 18+.

## What it does

Paste a finished replay. It usually takes one to three minutes, and longer when the machine is busy. You get:

- **The whole game as one sum:** `53% start + 64 luck − 17 choices = 100% (win)`, from an unusually swingy game. You began with a 53% chance, luck added an estimated 64 points, and everything else, including both players' decisions and model error, took away 17. Luck means random events: critical hits, missed attacks, KO-deciding damage rolls and speed ties. Choices is everything else, for both players together.
- **Win chance turn by turn,** with the biggest luck moments priced against what could have happened instead.
- **Up to three moves the engine would have played differently,** with how uncertain that is, never added up into a score, and **the one decision to look at again.** A rerun with new random draws can pick a different decision, so treat this as one place to look, not the place.
- **Muse Glimmer's explanation.** Ask what the turning point was, and it looks up the turn itself and explains what happened.
- **A test of your read.** Add your team and say what you believed about the opponent's set. HardRead checks your move against the alternatives under that belief. It uses the items, abilities, Tera types and moves you state. It doesn't use spreads or replay the turn under the move you expected.

It only reviews finished games. It never helps during a live battle.

## How it differs from a one-prompt analyzer

A one-prompt analyzer pastes a game into a language model. We ran that setup with the same model, and the saved showcases let you compare the two.

| | One-prompt analyzer | HardRead |
|---|---|---|
| **Who does the analysis** | A language model reads the log. | [Foul Play](https://github.com/pmariglia/foul-play), an open-source search engine that won the 2025 PokéAgent Gen 9 OU track, samples the hidden sets and searches the continuations. |
| **Where numbers come from** | The model writes them. | The model's own numbers **can't reach you**. It's told to write sentences with empty slots that point at engine results, code fills in the values, and a draft with a number of its own is rejected. |
| **What stops an invented number** | Nothing. | A checker rejects uncited numbers and citations of the wrong kind of value. It can't tell whether a correctly typed number is the relevant one. The model gets one repair, then a labeled template takes over. |
| **What you can inspect** | The final text. | Model-written answers show every lookup and rejected draft, and the source of every number. Saved showcases also show the plain-prompt comparison. |

## How Meta's models are used

**Muse Glimmer-30B runs the explanations as an agent.** It chooses which read-only engine tools to call, then submits an explanation that must pass the checker. We run this agent on a 128 GB laptop. The weights also ran on a rented GPU for battle requests; the agent loop wasn't tested there.

Results on 28 October's versions of three questions, under the review flow from before the fix, 60 answers about 20 games (base model, no fine-tuning). The checker covers numbers and required wording:

| Measure | Result |
|---|---|
| Passed the checker on the first draft | 41/60 (68%) |
| Passed after at most one repair | 59/60 (98%) |
| Sentences flagged in a blind review | 3/60 (5%; 95% CI 1.7–13.7%) |

Passing means each number comes from an engine result and has the right type. It doesn't mean the explanation is true: a real number can still be the wrong one for its sentence, and plain-word claims aren't checked. That's why we also ran the blind review, which was done by an AI reviewer. No human has checked these answers or the turning-point answers, so we don't claim the prose is accurate. An earlier version of the same test passed 49/60 after repair, with 12/60 sentences flagged.

Those figures don't cover the newer turning-point question. An earlier version of it, before the fix and tuned on the same 14 game-sides it was tested on, passed 9/14 on the first draft and 14/14 after repair, and an AI read judged 2 of the 14 misleading. The chosen turn can change with the wording. One saved showcase uses the labeled template for this question because the model's tool call failed. See [RESULTS.md](RESULTS.md).

**Test of the checker (not a model result):** In a scripted test on 83 real drafts, the checker rejected every invented or uncited number (745/745) and every citation of the wrong kind of value (285/285). It rejected only 8% of real numbers cited from the wrong record, and it does not check claims in plain words. The 745/745 holds by design, since no digit may appear outside a slot. [Method and full table](docs/checker-test.md).

For comparison, the same model with only the battle log and no engine put numbers in 27 of 30 answers, with nothing linking them to a source.

**We also fine-tuned Glimmer to play.** Trained on a search engine's decisions, it won **178/200 games (89%; 95% CI 84–93%)** against a heuristic bot called Abyssal, with 0 illegal moves in 5,202 decisions. Those teams were partly seen in development, so this doesn't measure general strength. An earlier fine-tune won 8 of 40 against Foul Play at 100 ms. The fine-tuned model is a separate experiment and doesn't write the explanations.

## What we measured, including what failed

| Question | Result |
|---|---|
| Does the luck meter behave? | In bot self-play over 499 paired games, the two players' luck mirrors at r = −0.82 (−0.54 on 146 paired human games, where there's no known truth to compare with), and subtracting measured luck cut outcome variance by about 22% (19% on unseen games). Measured after the fix, with different search settings from the page. This is bot self-play. But in two tests where one bot had a real information edge, the estimate shifted part of that edge, in opposite directions, and both failed our [bias rule](RESULTS.md#luck-information-edge-20261102) (measured before the fix). |
| How often does the read test wrongly flag a strong move? | Measured before the fix to how reviews track hidden sets. In bot self-play, on 184 moves chosen by a deeper search, it called 18 clear misplays (9.8%; 95% CI 6.3–14.9%), all wrong by the test's design. Our first two settings failed, at 75% and 21%. This wasn't measured on human games. No read was stated in this test, so it doesn't measure how well real reads are judged. |
| How much does hidden information matter to the engine? | Between bots, giving the engine the opponent's full sets instead of 25% of them was worth 14.67 win-rate points (95% CI ±4.96) over 300 side-swapped pairings, 600 games. This measures forced revelation, not the value of a human's reads. |
| Did our engine change beat Foul Play? | **No.** A pre-registered sealed test of 10,000 games gave 50.05% (95% CI 49.16–50.94%, p = .91). |
| Did a better calibration help? | **No.** It failed its gate and was not switched on. |

**Limits you should know:**
- "Choices" is what remains after estimated luck. It includes both players, later information and model error, so it isn't a skill score.
- Without your team export, your own sets are assumed, and that can change a verdict.
- On some turns no sampled set reproduces what the log shows, and luck on those turns is rough. The review says when, with that game's own count.
- Roaring Moon joined OU on 30 October, after the usage statistics we use were made. Reviews of games with it still run, and the engine still guesses its hidden sets from a list of known sets. But once some of its moves are known, the engine may assume it has fewer than four, and we haven't measured how much this changes a review well enough to quote.
- A review can fail when an export says Keldeo and the log shows Keldeo-Resolute.
- **A flaw we fixed on 2 November:** reviews used hindsight. They replayed the whole game before searching, so early decisions sampled hidden sets from lists narrowed by later events. Each decision now uses the lists as they stood at that moment, and luck for a turn uses them as they stood right after that turn. Results marked "measured before the fix" come from the old version.
- Luck is an estimate. In two tests where one bot had a real information edge, the estimate shifted part of that edge, in opposite directions, and the cause isn't known. So a luck number doesn't rule out skill. See [RESULTS.md](RESULTS.md#luck-information-edge-20261102). Those tests were measured before the fix above; the cause isn't known and may relate to that flaw.
- Win chances use a formula fitted to the results of top-50 ladder games. It was fitted on a different search and a different guess at your own team than a review uses, and before the fix above, and it wasn't refitted. Its published test covers that fit, not the reviews as they run now, and no other ratings. Win chances can also move between runs, and the moves listed can change.
- We haven't yet run a study with human players.

Full lab notes: [RESULTS.md](RESULTS.md) (detailed, in working shorthand). Pre-registration: [PREREGISTRATION.md](PREREGISTRATION.md).

## What was built here

I designed and directed this project; AI coding agents (Claude Code and Codex) wrote much of the code.

**Built during the hackathon:** replay reconstruction, the luck meter, the start/luck/choices bridge, verdicts, the read test, the Glimmer agent and checker, the web app and the fine-tuning pipeline.

**Before kickoff (disclosed):** about two hours of self-play harness work on kickoff morning: a match runner, cloud job scripts, team validation, an oracle hook (lets a bot see the opponent's sets) and decision logging. None of it is a demo feature, but two pieces carried over: the review engine reuses and extends the decision-log serializer, and the hidden-information experiment above uses the oracle hook. Another 44 commits (40 in the public histories, since filtering removed private-only commits) fall between kickoff on 26 October and the guide's 27 October building start, under the official rules' 26 October window. They include the first versions of replay reconstruction, search evaluation, calibration, the luck meter, the engine levers and the fine-tuning pipeline. [BUILT_DURING_HACKATHON.md](BUILT_DURING_HACKATHON.md) lists first commits with timestamps, and explains a mismatch in the earlier pre-kickoff count.

**Not ours:** the search engine and its tournament win belong to Foul Play by pmariglia (GPL-3.0). Our fork adds logging, an oracle hook and experimental levers in `fp/hardread_*.py`, off by default; reviews don't set them. The ladder bot used the one tested in the sealed test. Our bot on the public ladder ran on Foul Play: the account's best photographed rank was #157 (Elo 1727, 30 October 2026; an earlier snapshot was #199), and other agents also used that account. Over 515 rated games the frozen engine performed at 1582 (95% CI 1550–1615). See [THIRD_PARTY.md](THIRD_PARTY.md) and [DATA.md](DATA.md).

## Run it

```bash
python3 app/server.py   # http://127.0.0.1:18051
```

Showcases work with just `fastapi` and `uvicorn`. To review new games, clone [the engine fork](https://github.com/touchetpe/foul-play-hardread) into `foul-play/` and install its `requirements.txt` in a Python 3.13 environment (it needs Rust 1.89), then set `HARDREAD_FP_PYTHON` to that Python. The first run downloads current set data, so numbers can differ from the saved reviews; on an Apple-silicon Mac with Python 3.13, `tools/setup/clean_cli.py` fetches the pinned data instead. Live explanations need a `llama-server` running Muse Glimmer. More in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

**Running the bot directly:** results were measured at the evaluated Foul Play commit f061e63 (public id 175e591). That commit crashes in games against a species missing from its usage table, such as Roaring Moon. In a local test of short games on Showdown 8f421176bf7c, it crashed in 9 of 12 games against a Roaring Moon team, and 0 of 12 with the guard. [The fork's `roaring-moon-guard` branch](https://github.com/touchetpe/foul-play-hardread) fixes the crash. In our seeded checks, sampling for species in the table didn't change. HardRead's review has its own guard.
