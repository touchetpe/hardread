# Human Gen 9 OU replays of top ladder players

Public Pokemon Showdown replays of the top-50 users of the Gen 9 OU ladder (snapshot 2026-10-26 19:34 UTC), crawled from public JSON endpoints, plus a parser that turns them into HardRead tables. Raw data stays local.

| File | Role |
|---|---|
| `crawl_replays.py` | Polite, resumable crawler (standard library only) |
| `parse_replays.py` | Raw shards to games / per-turn actions / revealed sets / summary (standard library only) |
| `reconstruct_check.py` | Runs `tools/review/reconstruct.py` (read-only use) on sampled games from both seats and cross-checks it against the parser |
| `crawl_broad.py` | Broader crawl: ladder users 51-500 plus rating-stratified global recent replays (see "Broad crawl" below) |

## Rerun

From `hardread/`, with `PY=../environments/foulplay/bin/python` (plain `python3` also works for the first two):

```sh
$PY tools/human/crawl_replays.py all          # ladder snapshot, per-user search, fetch, manifest (about 40 min at 1 req/s)
$PY tools/human/parse_replays.py              # writes scratch_ds/human/ (a few seconds)
$PY tools/human/reconstruct_check.py --n 50 --workers 4     # about 2 min; --no-nicknames reproduces the nickname failure mode
```

`crawl_replays.py` also runs phase by phase (`ladder`, `search`, `fetch`, `manifest`). Re-running `fetch` skips every id in `fetched_ids.txt`, so an interrupted crawl resumes where it stopped. The ladder snapshot (`ladder_top50.json`) and the per-user search listings are reused if present; delete them to take a new snapshot.

**Crawl rules.** One request every 1.1 s overall; User-Agent `HardRead research crawler (hackathon project)`; exponential backoff (2 s doubling to 120 s, 5 retries) on errors, 429 and 5xx; abort after 3 consecutive 403/429 (none seen); private and password-protected replays skipped; stops if the raw directory passes 5 GB. In the real run every request returned 200.

## Endpoints

- Ladder: `https://pokemonshowdown.com/ladder/gen9ou.json` works as given. It returns `toplist` with 500 entries, ordered by `elo` (the ladder's own ranking). Fields: `userid, username, w, l, t, gxe, r, rd, elo, rpr, ...`. The top 50 are taken in list order.
- Replay search: `https://replay.pokemonshowdown.com/search.json?user=<userid>&format=gen9ou` works as given. It returns up to 51 rows (`id, format, players, rating, uploadtime, private, password`); a 51st row means another page. Pagination used: `before=<uploadtime of the last row> + 1`, duplicates dropped by id (the +1 avoids losing the 51st row). `page=` is not used.
- One replay: `https://replay.pokemonshowdown.com/<id>.json` works as given (`id, format, players, log, uploadtime, views, formatid, rating, private, password`). The log is the spectator view with percent HP for both sides.

## Raw data (outside the repo)

`<PROJECT_ROOT>/data/replays/gen9ou-top/raw/`

- `replays-0000.jsonl.gz` ... `replays-0004.jsonl.gz`: gzipped JSONL, one replay JSON per line, 500 per shard (each shard is a series of gzip members; `gzip -dc` and Python's `gzip` read it as one stream).
- `manifest.json`: snapshot time, top-50 list with Elo/GXE, query parameters, counts, SHA-256 per shard, date range, rating distribution, per-user listing counts.
- `ladder_top50.json` (raw snapshot), `search/<userid>.json` (listings), `fetched_ids.txt` (checkpoint), `missing_ids.txt` (none), `crawl.log`.

**Counts (this run).** 2,167 unique replays fetched, 3.7 MB of shards (4.8 MB with listings), upload dates 2026-07-01 to 2026-10-26. Up to 200 per user, newest first, only uploads since 2026-07-01 (00:00 UTC).
- 3,637 unique replays listed; 961 are older than the window (a lower bound: pagination stops at the first page that crosses the cutoff).
- 12 of the top-50 users have no replay in the window (6 of them have no public gen9ou replay at all), 10 more have 1 to 9, and 9 have 100 or more (largest: 464, 384, 228, 225 listed in-window; the cap of 200 per user cut the two largest). Most top accounts do not save replays, so the set is dominated by a handful of uploaders.
- 13 fetched replays have no rating (unrated); the 5 that parsed are kept with `rated` false.
- Rating field: min 1000, p10 1552, median 1682, p90 1790, max 1956.
- No private or password-protected replay was listed.

## Processed data: `hardread/scratch_ds/human/` (gitignored)

| File | Content |
|---|---|
| `games.jsonl.gz` | One game per line, extractor-compatible: `uid` (`human/<replay_id>`), `run_id`, `game`, `winner` (`A` = p1, `B` = p2), `winner_player`, `status`, `player_a` (always `p1`), `turns`, `log` (list of protocol lines). Plus `p1`, `p2` (pseudonyms), `ratings` (pre-game p1/p2, replay field, post-game), `rated`, `uploadtime`, `end_reason` (ko / forfeit / timeout), `top50` (`p1`, `p2`, `both`), `higher_rated`, `chance_counts`, `chance_net`, `flags`, `replay_id`, `uid_anon` |
| `actions.jsonl.gz` | One row per player per decision point |
| `sets.jsonl.gz` | One row per player per Pokemon at game end |
| `species_profiles.json` | Per species (80 most seen): item, ability, Tera and move frequencies among games where the Pokemon appeared |
| `summary.json`, `summary.md` | Summary statistics |
| `reconstruction_check/`, `reconstruction_check_no_nicknames/` | Reconstruction results (see below) |
| `PRIVATE/` | Salt and pseudonym-to-username map. Local only, never release |

Games with no log past team preview (forfeit at preview, 13) are skipped.

### `actions.jsonl.gz`

Keys: `uid, turn, k, player, player_uid, top50, rating, won, kind, ...`.
- `kind`: `lead` (turn 0), `turn` (the normal decision at `|turn|N`), `pivot` (second decision after U-turn, Volt Switch, Flip Turn, Parting Shot, Baton Pass, Teleport, Shed Tail, Eject Button/Pack, Emergency Exit, Wimp Out), `faint_replace` (forced switch after a faint; `forced_switch` true; `single_option` true when only one Pokemon was left).
- `(turn, k)`: k counts that player's decisions within the turn, the same convention as `reconstruct.py` (a faint replacement at the end of turn T has `turn = T`, k >= 1).
- `action_type`: `move`, `switch`, `tera_move`, `lead`, `hidden`, `tera_hidden`. `action`: move name or target species. `tera_type` set when the player terastallised this turn.
- `hidden`: the log does not show the action. `hidden_reason` is `no_action_shown` (fainted before moving), `par`, `slp`, `frz`, `flinch`, `move: Taunt` (the attempted move is then in `action`), etc. About 7.6% of turn decisions are hidden.
- `locked_move`: the move was a forced continuation (`[from]lockedmove`), not a free choice.
- State at decision time: `self_active` / `opp_active` (`species, hp` in percent, `status`, `tera`), `self_alive`, `opp_alive`, `opp_seen`, `weather`, `field`, `hazards_self`, `hazards_opp`, `first_mover` (`self`, `opp`, or null), `speed_tie_hint`.
- `chance`: events of that turn, each `{t, favoured: self|opp, move}`. Types: `crit`, `miss`, `full_para`, `flinch`, `sleep_turn`, `freeze_turn`, `confusion_self_hit`, `secondary`, `thaw`.
  - `secondary` is a heuristic: a status, stat drop or confusion on the target right after a damaging move, with no `[from]` tag. Status moves are excluded; guaranteed secondaries (for example Mortal Spin) are counted.
  - Always-crit moves (Wicked Blow, Surging Strikes, Frost Breath, Storm Throw, Flower Trick) are excluded from `crit`.
  - Damage rolls are not events.
- `speed_tie_hint`: only `mirror_match_same_species` (both actives are the same species and no known priority move). A real speed tie cannot be detected from a public log; `first_mover` is given so speed can be bounded.

### `sets.jsonl.gz`

`species_preview` (the preview forme; `-*` forms are replaced by the forme that appeared), `species` (last forme), `in_preview` (always true: Gen 9 OU brings all six), `seen` (appeared in battle), `switch_ins`, `turns_active`, `fainted`, `item` (first non-Trick item revealed) with `item_how`, `item_events` (all reveals), `ability` with `ability_how`, `tera_type`, `terastallized`, `moves`, `n_moves_seen`.

- `item_how`: `message` (`[from] item:` text such as Leftovers, Rocky Helmet, Life Orb), `item_message` (`-item` line), `consumed`, `knock_off`, `frisk`, `trick`, or another move/ability name.
- Called moves (Metronome, Copycat, Dancer, Magic Bounce) are not counted as the Pokemon's own moves; Sleep Talk and Instruct targets are.

### Summary (2,154 games)

Games parsed 2,154 (13 skipped), 943 unique players (38 are top-50 users; the rest are their opponents), 42 games with both players in the top 50, 2,112 with one. Turns per game: mean 32.5, median 26, p10 15, p90 55. End: 970 KO, 1,152 forfeit, 32 timeout (55% forfeit or timeout). Pre-game rating: median 1721, p10 1588, p90 1843, max 2074; top-50 side median 1735, opponents median 1707. Higher-rated player wins 57%.

Reveal by game end, among Pokemon that appeared (23,688): item 49.2%, ability 41.9%, Tera type 13.2% (72% of teams terastallise at least once), moves 2.0 of 4 on average (0 moves: 8.2%, 4 moves: 11.2%).

Chance events per game, both players: crit 1.22, miss 0.60, full paralysis 0.39, flinch 0.08, sleep turn 0.30, freeze turn 0.08, secondary effect 1.28. The net count of chance events for minus against (crit, miss, para, flinch, sleep, freeze, secondary) correlates with winning at r = 0.16 (the farm baseline in `tools/review/README.md` is r = 0.18 on 250 games).

Top-30 species by share of teams: Great Tusk 31.8%, Kingambit 28.6%, Gholdengo 28.3%, Zamazenta 21.5%, Raging Bolt 21.5%, Ogerpon-Wellspring 21.2%, Dragonite 18.0%, Iron Valiant 17.1%, Kyurem 14.9%, Gliscor 14.3%, Iron Treads 14.1%, Samurott-Hisui 12.9%, Slowking-Galar 12.6%, Landorus-Therian 12.3%, Cinderace 12.1%, Clefable 11.4%, Corviknight 10.8%, Ting-Lu 10.3%, Iron Moth 9.6%, Glimmora 9.5%, and so on (full list in `summary.json`).

## Reconstruction check (`reconstruct_check.py`)

50 random games (at least 6 turns), both seats: 100 game-sides. Nobody's real team is known, so each side's team is **drafted from the replay**, as in the `tools/review` README for its public replay: species and forme from team preview; revealed item, ability, Tera type and moves from `sets.jsonl.gz`; unrevealed fields filled by default (item Leftovers, first listed ability, first type as Tera type, moves padded from the species' common revealed moves, Serious nature, 85 EVs). The draft uses the whole game, so it checks state tracking and decision reading, not search quality. The log fed to `reconstruct.py` is the sanitised `games.jsonl.gz` log.

"Clean" means: the decision exists, its legal set is non-empty, when the log shows the action it is in the legal set and equals the action read by `parse_replays.py` for the same (turn, k), and (at turn-start decisions) the reconstructed active Pokemon, HP (within 1.6 points for the user's estimated HP, 1 for the opponent) and status equal the parser's.

Results (`reconstruction_check/summary.json`): 99 of 100 sides ran; 4,337 decisions reconstructed, 4,336 clean (99.98%); 320 (7.4%) have a hidden action (not errors); all 4,337 parser decision rows were matched. State check: 3,734 of 3,735 turn-start decisions clean.

Failure modes:
1. **Nicknames (the big one).** With a draft that does not carry the in-game nicknames, `reconstruct.py` treats the user's percent HP as raw HP (it scales by max HP only for nicknames it finds in the team export), so the user's HP is wrong at every decision of nicknamed Pokemon: 77.8% clean overall, 962 own-HP mismatches (`reconstruction_check_no_nicknames/`). Public replays with nicknames therefore need the nicknames in the team export (the check does that by default). Suggested fix in `reconstruct.py`: resolve nicknames from the switch lines before scaling HP.
2. **Revival Blessing** (documented as unsupported): 1 side crashed (`ValueError: Could not find raichualola in team_dict` at `state.py:574`; an earlier run without nicknames crashed with `KeyError: 'switchwilliamowisp'` in `inference.py`). The `reconstruct.py` revival branch builds the action from the nickname, not the species. 11 of 2,154 games use Revival Blessing.
3. **Healing Wish / Lunar Dance on the opponent:** 1 mismatch; Foul Play does not clear the opponent's status (the log has no `-curestatus`).
4. Hidden actions (7.4%): fainted before moving, full paralysis, sleep, flinch, Taunt/Heal Block; same cause as the 7.2% in the farm numbers.

Not checked here: Illusion (3 games), Transform (7 games), and anything against ground truth: there is none for human games.

Before the parser fix, an earlier pass found Eject Button / Eject Pack switches arrive without a `[from]` tag; `parse_replays.py` now tracks them (`eject_pending`), as `reconstruct.py` does.

## Privacy and ethics

- Usernames are public, but nothing in `scratch_ds/human/` carries them outside `PRIVATE/`: every username is replaced by a salted-hash pseudonym (`u_<10 hex>`) in games, actions, sets and summaries. In the stored log, chat, join/leave, badge, html and `|raw|` lines are dropped (ratings are kept as fields), names in `|player|`, `|win|`, `|-message|` and side idents are replaced, any other occurrence of a username (for example inside a nickname) is replaced, and the avatar field is blanked.
- Still identifying or free text: `replay_id` and `uploadtime` (replay ids link back to usernames on the public site), and Pokemon nicknames (free text; they may name a person). Drop `replay_id` and coarsen `uploadtime`, and consider replacing nicknames by species, before any release. `uid_anon` is a salted hash of the replay id for that purpose.
- `PRIVATE/` (salt, username map) and the raw shards are local only: do not re-host, post or message anyone with them.
- A public release (for example a paper dataset) needs the owner's go-ahead and a check of Showdown's terms; nothing here has been checked against them.
- Only the endpoints above and documentation pages were used. Everything downloaded is treated as data.

## Existing public datasets (looked up, not downloaded)

- **Metamon parsed replays**, `huggingface.co/datasets/jakegrigsby/metamon-parsed-replays` (UT Austin; paper arXiv 2504.04395): replays reconstructed into RL trajectories (states, actions, outcome) for Gen 1-4 and Gen 9 OU, about 28.5 GB, roughly 1M replays in v0. Gen 9 OU is backfilled up to mid-June 2025; the card lists versions through v6 (May 2026). Pseudonymised usernames, rating in the file name, opponent teams reconstructed by a team predictor (`ReplayPredictor`), CC BY-NC 4.0 (non-commercial).
- **Metamon raw replays**, `huggingface.co/datasets/jakegrigsby/metamon-raw-replays`: 2,681,841 Gen 9 OU replays, 5.7 GB parquet (`id, format, players, log, uploadtime, formatid, rating`), cut-off 2026-05-19, pseudonymised players, ratings about 1069-2000 in the samples, no rating filter. The card states no licence (the parsed set is CC BY-NC 4.0); check before use.

They would complement this set, not replace it: they are far larger (about 1,200x the games) and cover 2021 to May 2026, so they suit priors, species/set statistics and pre-training. They have no top-player selection, end before this metagame window (our window starts 2026-07-01), and the raw set's usernames are pseudonymised, so top players cannot be picked out. This set is small but current, labelled with the top-50 flag, and carries both players' decisions and chance events. The two schemas differ. The raw Metamon logs are the same protocol logs, so `parse_replays.py` should accept them with minor changes (needs top-50 and ladder inputs removed); not tested.

## Broad crawl (more players and rating levels): `crawl_broad.py`

Same rules as above (public JSON endpoints only, 1.1 s between requests, descriptive User-Agent, backoff, abort after 3 consecutive 403/429, private replays skipped, pseudonymisation, checkpoints, nothing committed). Additions: the request spacing only ever grows after a 429/5xx/timeout (x2, cap 10 s, never relaxed within a run), and the run stops after `--abort-after-failing-minutes` (default 30) without a single success. Window: uploads since 2026-07-01.

```sh
$PY tools/human/crawl_broad.py run --max-hours 8 [--interval 1.1]   # resumable; kill it any time (SIGTERM) and re-run
$PY tools/human/crawl_broad.py status                               # band counts, sources, disk
$PY tools/human/crawl_broad.py manifest
$PY tools/human/parse_replays.py --broad <PROJECT_ROOT>/data/replays/gen9ou-broad/raw --out scratch_ds/human_broad
```

**What it fetches.**
1. Ladder users 51-500 (fresh snapshot, `ladder_top500.json`, with Elo/GXE per user in `manifest.json`; the 5 users that were in the first top-50 snapshot are skipped, they are already in `gen9ou-top`): up to 100 in-window replays each, newest first, round-robin over users (source `top500`).
2. The global recent-replays search without a user filter, `search.json?format=gen9ou&before=<t>`: the window is cut into 2,400 equal time slots, visited in a fixed random order, one page (51 rows) per slot, so any prefix is uniform in time. Each fetch picks the rating band (replay `rating` field) with the fewest games so far among bands that have candidates and fewer than 6,000 games (source `global`). The top-50 replays already crawled count toward the bands and are never re-fetched. Unrated replays are never chosen from the global listing.
3. Fetch order alternates between the two queues; one listing request every 8 fetches after a 250-slot warm-up.

Checkpoints: `index.jsonl` (id, source, rating, band; written with each 20-replay flush), `global_pages.jsonl` (one line per listed page), `search_top500/<user>.json`, `status.json` (every 5 min), `manifest.json` (every 30 min and at exit), `missing_ids.txt`, `run.lock`.

**Raw data:** `<PROJECT_ROOT>/data/replays/gen9ou-broad/raw/` (32 shards of 500, 43 MB with listings). **Processed:** `hardread/scratch_ds/human_broad/` (58 MB; same tables as `human/`, parsed from the broad shards plus the top-50 shards, which are tagged `source = top50`). The top-50 outputs in `scratch_ds/human/` are unchanged (re-running the default parse reproduces them byte for byte). Pseudonyms use the same salt as `scratch_ds/human/PRIVATE/`, so a player has the same pseudonym in both datasets; `PRIVATE/player_map.json` stays local.

**Extra columns** (games, actions and sets rows): `source` (`top50`, `top500`, `global`), `rating_band` (from the replay `rating` field: the lower player's post-game rating; bands `lt1300`, `1300-1499`, `1500-1699`, `1700-1899`, `1900plus`, `unrated`), `rating_band_pre` (same bands from the lower player's pre-game rating), and in games `top500` (`p1`, `p2`: player is among the 500 ladder entries of the snapshot). Free text: a second pass replaces any other player's username that turns up inside a nickname.

### Result of the run (2026-10-26 22:19 to 2026-10-27 04:01 CET, stopped early)

- 18,483 requests over 5 h 42 min at about 0.9 per second, all HTTP 200, no 403 or 429. The crawl stopped at 18,483 requests (target was 25-30k) because the replay host failed (below); it is resumable.
- 15,612 new replays (7,806 `top500`, 7,806 `global`) plus the 2,167 top-50 replays reused = 17,779. 17,676 parsed; 103 skipped (forfeit before the first turn). 9,426 distinct players (38 top-50 users).
- Listing: 445 users (ladder 51-500 minus 5) gave 12,997 in-window replays (11,529 after the 100-per-user cap and dedup; 7,806 fetched, about 3,700 left in the queue). The 2,400 global pages listed 119,131 unique in-window replays (the pages cover about 57% of the window's time; public gen9ou uploads run at about 1,800 per day, so roughly 215,000 in the window).
- Rating mix of the global listing (the field is the lower player's rating): under 1300 54.6%, 1300-1499 21.9%, 1500-1699 14.6%, 1700-1899 4.7%, 1900+ 0.2%, unrated 4.1%. All 179 listed 1900+ replays were fetched (183 games with 1900+ in total, 21 of them from the top-500 route or top-50 set); 1700-1899 had about 3,100 listed candidates left; the other bands were limited by the crawl time.
- **Games per band (parsed):** under 1300: 3,738; 1300-1499: 3,775; 1500-1699: 6,074 (the 6,000 cap only stops global top-ups; the top-500 route adds regardless); 1700-1899: 3,766; 1900+: 183; unrated: 140.
- Source by band: under 1300: 3,536 global / 176 top500 / 26 top50; 1300-1499: 2,489 / 1,220 / 66; 1500-1699: 0 / 4,918 / 1,156 (the global top-up never needed this band: the lowest band is always picked first); 1700-1899: 1,564 / 1,314 / 888; 1900+: 162 / 15 / 6.
- Disk: raw 43 MB, processed 58 MB. 

**Endpoint behaviour.**
- The global search works without a user filter and with `before=<unix time>` for any time in the window: 51 rows per page, ratings included, about one page per 40 minutes of uploads (about 1,800 public gen9ou replays per day).
- At 04:01 the replay host (`replay.pokemonshowdown.com`, both search and replay JSON) began timing out (30 s) and then returning Cloudflare HTTP 522 for over an hour; at 05:10 `pokemonshowdown.com/ladder` answered in 16 s and the replay search still returned 522. The crawler slowed itself to 10 s per request (it never saw a 429), retried each replay 5 times, and was stopped by hand after about one hour without a success (the new `--abort-after-failing-minutes` guard does this automatically). The unflushed buffer (under 20 replays) was refetched on resume by design. To resume: re-run `crawl_broad.py run --interval 2` once the host answers.

### Summary per rating band (`scratch_ds/human_broad/summary_by_band.json` and `.md`)

Band = replay `rating` field (lower player). Games, turns, forfeit rate, species usage top 15, reveal rates, chance events per game, action mix and the higher-rated player's win rate are all in the files.

| band | games | players | turns (mean) | forfeit or timeout | move / switch / tera move (turn decisions, hidden 6-11%) | item / ability / Tera revealed (seen Pokemon) | crits / misses per game |
|---|---|---|---|---|---|---|---|
| under 1300 | 3,738 | 5,204 | 24.0 | 48.6% | 71.6 / 14.9 / 2.3 | 43.4 / 35.1 / 11.7 | 1.00 / 0.51 |
| 1300-1499 | 3,775 | 3,758 | 25.8 | 53.7% | 68.5 / 19.6 / 2.6 | 44.1 / 39.4 / 13.1 | 1.07 / 0.58 |
| 1500-1699 | 6,074 | 1,916 | 27.4 | 50.8% | 67.8 / 20.8 / 2.5 | 46.9 / 41.6 / 13.3 | 1.12 / 0.54 |
| 1700-1899 | 3,766 | 989 | 31.9 | 55.1% | 67.9 / 22.4 / 2.2 | 48.0 / 41.9 / 13.2 | 1.21 / 0.59 |
| 1900+ | 183 | 124 | 35.6 | 59.0% | 67.1 / 24.5 / 2.1 | 45.8 / 40.2 / 13.8 | 1.33 / 0.57 |

Reading: higher bands play longer games and switch more (14.9% to 24.5% of turn decisions) and reveal more; the lowest band forfeits less but has far more sleep turns (0.44 per game against 0.14) and more flinches. Species usage shifts too: Corviknight, Dragapult and Alomomola are more common in the lowest band, Zamazenta and Samurott-Hisui in the upper bands (full top 15 per band in the files).

**Caveats.** The games are not a uniform sample: the 1500-1899 bands are dominated by the listings of top-500 users and the global sample covers about 57% of the window's time. The band label is the lower player's post-game rating, so it depends on the result: the higher-rated player's win rate by that band is biased (43% in 1700-1899) and should not be used; by the lower player's pre-game rating it is 54.4%, 53.3%, 53.1%, 52.9% and 47.0% (n = 164) for the five bands (`_higher_rated_win_pct_by_pre_game_band`). The 6-11% hidden-action rate falls as rating rises. Reconstruction was only checked on the top-50 set.
