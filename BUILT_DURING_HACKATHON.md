Commit ids in this file are those of the public repositories: this one, and [the engine fork](https://github.com/touchetpe/foul-play-hardread) for fork rows. History was filtered for release; dates are unchanged. Commits that only touched private paths aren't in this repo and are marked. Counts in the Verify section describe the private history.

# Built during the hackathon: what was made when

This provenance ledger is generated from pinned Git history. It describes the captured project snapshot below, not commits added later.

- Repositories: `hardread` (branch `main`, captured HEAD `b03214fe71c0d75a5caafba95494783a2f42ac3a`) and `hardread-foulplay` (branch `hardread`, captured HEAD `175e5916880c7b42c53527b5f280bef8e383af2a`); fork-only commits are those after upstream `3264ac8bf0b42178b8c8561f1702fe74bc64a3d9`.
- The source plan preserved these point-in-time refs but not their capture timestamp. Generated 2026-10-29 10:29:15 UTC from those fixed refs. The main history has advanced since this snapshot; refresh the ledger for the final release candidate.
- Append-only main-history extension prepared 2026-10-29 16:27:14 UTC, covering commits after b03214fe71c0d75a5caafba95494783a2f42ac3a through exact `hardread` main commit `4aed64ffb566877be14d5ab173e08effe79a5935` (committer timestamp 2026-10-29 16:19:32 UTC). The original time-band counts, first-commit table, and pre-kickoff disclosure remain tied to the original snapshot above. This extension is not the final release-locked capture.
- Git timestamps below are committer timestamps in UTC. Commit subjects are omitted from the per-commit ledger so operational identifiers in commit messages are not copied.

## Time bands

| Band | From (UTC) | To (UTC) | Why this boundary | HardRead commits | Fork commits |
|---|---|---|---|---:|---:|
| 1. Before kickoff | — | 2026-10-26 16:00 | Kickoff was Mon Oct 26 at 12:00 PM ET (EDT, UTC−4) | 11 | 3 |
| 2. Kickoff → building period | 2026-10-26 16:00 | 2026-10-27 04:00 | The participant guide's building period starts Oct 27 (00:00 ET); the official rules' build window starts Oct 26 | 39 | 5 |
| 3. Building period | 2026-10-27 04:00 | 2026-11-05 02:00 | Submissions are due Wed Nov 4 at 9:00 PM ET (EST, UTC−5) | 294 | 0 |

**Before kickoff:** The earlier pre-rewrite ledger recorded 16 pre-kickoff harness commits (11 main and 5 fork); the pinned reachable snapshot above has 14 (11 main and 3 fork), between 2026-10-26 12:21:44 and 2026-10-26 14:30:26 UTC. The current ledger does not establish why two fork commits are no longer reachable. The 14 reachable commits establish the self-play harness around the open-source Foul Play engine: a match runner, cloud job scripts, team validation, an oracle hook for one science experiment, and decision logging. These commits do not contain the demo review features. The review engine later reused the decision-log public-view serializer, first written before kickoff and extended afterward.

## Demo and science features: first commit

| Feature | Kind | File(s) | First commit | UTC | Band |
|---|---|---|---|---|---:|
| Replay reconstruction (what each player knew, turn by turn) | product | `tools/review/reconstruct.py` | d339b3368202 | 2026-10-26 18:46:46 | 2 |
| Search evaluation and win probability | product | `tools/review/evaluate.py`, `tools/review/value.py` | d339b3368202 | 2026-10-26 18:46:46 | 2 |
| Win-probability calibration | product | `tools/review/calibrate.py` | d339b3368202 | 2026-10-26 18:46:46 | 2 |
| Luck meter | product | `tools/review/luck.py` | bdd25a3b1ecb | 2026-10-26 20:13:47 | 2 |
| Review JSON / CLI | product | `tools/review/review.py` | d339b3368202 | 2026-10-26 18:46:46 | 2 |
| Review on human games (Showdown replays) | product | `tools/review/human_eval.py` | 2b18bcb59576 | 2026-10-26 22:48:36 | 2 |
| Bridge: Start → Luck → Choices → Result | product | `tools/review/bridge.py` | a74b665abb53 | 2026-10-27 04:44:48 | 3 |
| Bridge validation prototype | product | `tools/review/proposed/validate_bridge.py` | e1a17110d458 (private paths only, not in this repo) | 2026-10-27 04:11:32 | 3 |
| Bridge validation CLI | product | `tools/review/validate_bridge.py` | a74b665abb53 | 2026-10-27 04:44:48 | 3 |
| Verdict (Misplay / Misread / Defensible / Within noise) | product | `tools/review/verdict.py` | 3cb0f251a98f | 2026-10-27 04:54:47 | 3 |
| Glimmer explainer with verifier | product | `tools/agent/explain.py`, `tools/agent/verifier.py` | e24b6eaba03e | 2026-10-27 05:10:05 | 3 |
| Note parser (what you were thinking → stated read) | product | `tools/agent/parse_note.py` | e24b6eaba03e | 2026-10-27 05:10:05 | 3 |
| Web app | product | `app/server.py` | c65c0b5d4553 | 2026-10-27 05:00:24 | 3 |
| Match runner (self-play harness) | science | `tools/run_match.py` | 92b7b47c74df | 2026-10-26 12:35:18 | 1 |
| Oracle injection (value-of-information experiment) | science | `fp/hardread_oracle.py` | aafe82129295 | 2026-10-26 12:40:41 | 1 |
| Decision log | science | `fp/hardread_decision_log.py` | 2058dee18007 | 2026-10-26 14:30:26 | 1 |
| Pre-registration of the sealed test | science | `PREREGISTRATION.md` | 0d83b4d585ef | 2026-10-26 16:27:46 | 2 |
| Engine levers (value, double-half, student-blend, belief) | science | `fp/hardread_levers.py` | 6aa0a0129541 | 2026-10-26 19:27:28 | 2 |
| Value-of-information analysis | science | `tools/analysis/voi.py` | 0d83b4d585ef | 2026-10-26 16:27:46 | 2 |
| 25M student policy/value model | science | `tools/student/model.py` | b8a8c78023ae | 2026-10-26 19:33:27 | 2 |
| Belief model | science | `tools/belief/model.py` | 092638e7220d | 2026-10-26 19:15:04 | 2 |
| Glimmer fine-tuning pipeline | science | `tools/sft/serialize.py`, `azure/sft/run_sft.py` | 83af55939863 | 2026-10-26 19:39:59 | 2 |
| Arena vs PokéChamp's abyssal bot | science | `tools/arena/run_arena.py` | eb8462fdbf5b | 2026-10-26 19:32:03 | 2 |

## Commit ledger

Rows through `b03214fe71c0` are reachable from the original pinned refs. The append-only extension that follows records `hardread` main commits after `b03214fe71c0` through `4aed64ffb566`; the fork reference is unchanged. Subject text is intentionally omitted from this public per-commit ledger.

| UTC | Repo | Commit |
|---|---|---|
| 2026-10-26 12:21:44 | foul-play fork | `7d5d6415f184` |
| 2026-10-26 12:35:18 | hardread | `92b7b47c74df` |
| 2026-10-26 12:35:41 | hardread | `683ab8972d95` |
| 2026-10-26 12:40:41 | foul-play fork | `aafe82129295` |
| 2026-10-26 12:42:56 | hardread | `7f2cde2aabfa` |
| 2026-10-26 13:49:53 | hardread | `1a59dcb1abee` |
| 2026-10-26 13:51:51 | hardread | `cb2d3999b11b` |
| 2026-10-26 14:09:24 | hardread | `bca14b41e73b` |
| 2026-10-26 14:16:04 | hardread | `7e307d9995df` |
| 2026-10-26 14:28:19 | hardread | `a2096eb70fdb` |
| 2026-10-26 14:28:52 | hardread | `da3165584044` |
| 2026-10-26 14:30:26 | foul-play fork | `2058dee18007` |
| 2026-10-26 14:30:26 | hardread | `bbfc57b7f27f` |
| 2026-10-26 14:30:26 | hardread | `a901266df5ce` |
| 2026-10-26 16:02:34 | hardread | `271bb28b4933` |
| 2026-10-26 16:11:20 | hardread | `e4f0ef49ed3e` |
| 2026-10-26 16:13:09 | foul-play fork | `e0d963394e9c` |
| 2026-10-26 16:22:09 | hardread | `4ab9a0352dc9` |
| 2026-10-26 16:27:46 | hardread | `0d83b4d585ef` |
| 2026-10-26 16:48:33 | hardread | `6152ca36ddb9` |
| 2026-10-26 16:53:59 | hardread | `f59d27b53941` |
| 2026-10-26 18:46:46 | hardread | `d339b3368202` |
| 2026-10-26 18:47:18 | hardread | `c19fbbe91a16` |
| 2026-10-26 19:15:04 | hardread | `092638e7220d` |
| 2026-10-26 19:27:28 | foul-play fork | `6aa0a0129541` |
| 2026-10-26 19:27:28 | hardread | `18149cab5024` |
| 2026-10-26 19:29:46 | hardread | `67ef897b9057` |
| 2026-10-26 19:30:02 | hardread | `b6e4df301f68` |
| 2026-10-26 19:31:32 | hardread | `2ecc82686474 (private paths only, not in this repo)` |
| 2026-10-26 19:32:03 | hardread | `eb8462fdbf5b` |
| 2026-10-26 19:33:27 | hardread | `b8a8c78023ae` |
| 2026-10-26 19:34:48 | foul-play fork | `6ce9acd2ebef` |
| 2026-10-26 19:34:48 | hardread | `ba54307d243a (private paths only, not in this repo)` |
| 2026-10-26 19:39:59 | hardread | `83af55939863` |
| 2026-10-26 19:40:11 | hardread | `a544ad99954d` |
| 2026-10-26 20:08:21 | hardread | `e7fb9a5d3e33` |
| 2026-10-26 20:13:47 | hardread | `bdd25a3b1ecb` |
| 2026-10-26 20:13:47 | hardread | `f18a312f21a5` |
| 2026-10-26 20:36:59 | hardread | `e6b77dd1b256` |
| 2026-10-26 22:39:11 | hardread | `f65b51811a6c` |
| 2026-10-26 22:43:58 | foul-play fork | `47ee61eee542` |
| 2026-10-26 22:43:58 | hardread | `480b8c578f91` |
| 2026-10-26 22:48:36 | hardread | `81089bed3d37` |
| 2026-10-26 22:48:36 | hardread | `2b18bcb59576` |
| 2026-10-26 22:48:54 | hardread | `6411e6b4c1d7` |
| 2026-10-26 22:58:50 | hardread | `aaacf28c884c (private paths only, not in this repo)` |
| 2026-10-26 22:59:13 | hardread | `6805c45a0c65` |
| 2026-10-26 23:07:24 | hardread | `4da7b3cefb23` |
| 2026-10-26 23:07:24 | hardread | `f7e5c8897db9 (private paths only, not in this repo)` |
| 2026-10-26 23:08:03 | hardread | `3bd8b336fb05` |
| 2026-10-26 23:53:26 | hardread | `53d29c2b14eb` |
| 2026-10-26 23:53:26 | hardread | `587546a56b6a` |
| 2026-10-26 23:53:35 | hardread | `5bf00263de5a` |
| 2026-10-27 00:23:22 | foul-play fork | `175e5916880c` |
| 2026-10-27 00:23:22 | hardread | `bf5f16697f2e` |
| 2026-10-27 00:24:58 | hardread | `8fcc7255709d` |
| 2026-10-27 00:50:48 | hardread | `22677d58dfa6` |
| 2026-10-27 02:52:49 | hardread | `ff737e1f39ca` |
| 2026-10-27 04:10:08 | hardread | `13fc24ad920f` |
| 2026-10-27 04:10:08 | hardread | `70a6bcd748a2` |
| 2026-10-27 04:10:20 | hardread | `9148d72c1062` |
| 2026-10-27 04:11:32 | hardread | `e1a17110d458 (private paths only, not in this repo)` |
| 2026-10-27 04:37:04 | hardread | `32599ea53047` |
| 2026-10-27 04:37:10 | hardread | `169d9939dff1 (private paths only, not in this repo)` |
| 2026-10-27 04:37:22 | hardread | `35ab14e60a1d` |
| 2026-10-27 04:44:48 | hardread | `a74b665abb53` |
| 2026-10-27 04:48:55 | hardread | `7e945ea2e7a2` |
| 2026-10-27 04:54:47 | hardread | `3cb0f251a98f` |
| 2026-10-27 05:00:24 | hardread | `c65c0b5d4553` |
| 2026-10-27 05:08:37 | hardread | `3645819b71d3` |
| 2026-10-27 05:10:05 | hardread | `e24b6eaba03e` |
| 2026-10-27 05:10:29 | hardread | `52009440ad1a` |
| 2026-10-27 05:17:54 | hardread | `cea2daaadd05` |
| 2026-10-27 05:46:20 | hardread | `d67849bef6f1` |
| 2026-10-27 05:47:40 | hardread | `c507e517ec96` |
| 2026-10-27 05:50:04 | hardread | `1082ab3efa56` |
| 2026-10-27 05:50:23 | hardread | `0ad443675622` |
| 2026-10-27 05:51:37 | hardread | `f9f2ea4ee55d` |
| 2026-10-27 05:51:52 | hardread | `6a06f3705712` |
| 2026-10-27 05:52:26 | hardread | `fda84683bea6` |
| 2026-10-27 05:54:30 | hardread | `6a4e2e0976c6` |
| 2026-10-27 05:56:59 | hardread | `f25f5cf37312` |
| 2026-10-27 06:19:56 | hardread | `c3e33494338f` |
| 2026-10-27 08:26:42 | hardread | `9a08deed09af` |
| 2026-10-27 08:48:31 | hardread | `bc234d730f47` |
| 2026-10-27 08:48:38 | hardread | `546ecfce1b32` |
| 2026-10-27 09:12:34 | hardread | `9bd6e1823b61` |
| 2026-10-27 09:59:50 | hardread | `c8354ae80a70` |
| 2026-10-27 10:06:25 | hardread | `588880b9002a` |
| 2026-10-27 10:12:07 | hardread | `15c3972288e4` |
| 2026-10-27 10:24:23 | hardread | `ba20678044dc` |
| 2026-10-27 10:31:03 | hardread | `14c2aa4078a3 (private paths only, not in this repo)` |
| 2026-10-27 10:45:10 | hardread | `6188fcacb410 (private paths only, not in this repo)` |
| 2026-10-27 10:52:02 | hardread | `c69e90b42591` |
| 2026-10-27 10:52:05 | hardread | `a9a458d1caa6` |
| 2026-10-27 11:01:54 | hardread | `219b32a4e44f (private paths only, not in this repo)` |
| 2026-10-27 11:09:01 | hardread | `a3b1a7f78383 (private paths only, not in this repo)` |
| 2026-10-27 11:45:05 | hardread | `b02f1db81ae9` |
| 2026-10-27 11:46:09 | hardread | `f491c57669b1 (private paths only, not in this repo)` |
| 2026-10-27 12:28:58 | hardread | `e0ae3b4d9af0` |
| 2026-10-27 12:52:18 | hardread | `6b0e29f3eb9a (private paths only, not in this repo)` |
| 2026-10-27 13:15:17 | hardread | `f249c803198b` |
| 2026-10-27 13:47:24 | hardread | `e694300173e8` |
| 2026-10-27 13:59:51 | hardread | `f6edd152a411` |
| 2026-10-27 14:24:39 | hardread | `96ebdcaa7f97` |
| 2026-10-27 15:30:30 | hardread | `382a50a3ed28` |
| 2026-10-27 15:36:44 | hardread | `2de4373883ca` |
| 2026-10-27 15:37:35 | hardread | `7eb432cd020c` |
| 2026-10-27 15:42:17 | hardread | `69b21fe2122e` |
| 2026-10-27 16:07:18 | hardread | `8f69e1e0ed7d` |
| 2026-10-27 16:42:02 | hardread | `1b8391fcfa4d` |
| 2026-10-27 17:05:02 | hardread | `7b8158e59245` |
| 2026-10-27 17:19:08 | hardread | `81edcbcd1f4b` |
| 2026-10-27 17:40:53 | hardread | `862075416c15` |
| 2026-10-27 17:42:10 | hardread | `4c00c3447036` |
| 2026-10-27 17:49:38 | hardread | `319b20bc8ba2 (private paths only, not in this repo)` |
| 2026-10-27 18:04:39 | hardread | `7252a9e9c050 (private paths only, not in this repo)` |
| 2026-10-27 18:13:47 | hardread | `a70022508fde` |
| 2026-10-27 18:20:09 | hardread | `2d5fa9a57053 (private paths only, not in this repo)` |
| 2026-10-27 18:23:20 | hardread | `52a58246fa71 (private paths only, not in this repo)` |
| 2026-10-27 18:52:41 | hardread | `e24d8c7c5e5f` |
| 2026-10-27 18:58:44 | hardread | `3e566f13c198` |
| 2026-10-27 19:35:03 | hardread | `344627855ca5` |
| 2026-10-27 19:36:57 | hardread | `d7a07a4a2bd1` |
| 2026-10-27 20:35:48 | hardread | `4bac65c8eb7c` |
| 2026-10-27 20:38:35 | hardread | `6e060002c36d` |
| 2026-10-27 20:52:28 | hardread | `be2747030fe3` |
| 2026-10-27 20:56:32 | hardread | `57415d65782f` |
| 2026-10-27 21:05:38 | hardread | `158b35192bc2` |
| 2026-10-27 21:51:03 | hardread | `d65d42420845` |
| 2026-10-27 22:11:55 | hardread | `a2d69c4ded4c` |
| 2026-10-27 22:13:41 | hardread | `2186adf33450` |
| 2026-10-27 22:30:37 | hardread | `90b35891f775` |
| 2026-10-27 22:51:52 | hardread | `427675b67e34` |
| 2026-10-27 22:54:46 | hardread | `465f24bf43f5` |
| 2026-10-27 23:16:41 | hardread | `04197615b7cd` |
| 2026-10-27 23:19:26 | hardread | `c53a9be7a19c` |
| 2026-10-27 23:25:24 | hardread | `b26c4bf1e786` |
| 2026-10-28 00:55:52 | hardread | `e8d31b57eb91` |
| 2026-10-28 01:02:24 | hardread | `f1731c615c32` |
| 2026-10-28 01:22:29 | hardread | `775ccc9f8900` |
| 2026-10-28 01:53:16 | hardread | `a16fe17acb7c` |
| 2026-10-28 02:04:04 | hardread | `d643f4d17ec9` |
| 2026-10-28 02:06:16 | hardread | `e8677666caa6` |
| 2026-10-28 02:24:11 | hardread | `5ccce925d013 (private paths only, not in this repo)` |
| 2026-10-28 02:33:46 | hardread | `9b306bd8a68a (private paths only, not in this repo)` |
| 2026-10-28 02:52:38 | hardread | `122c005e01c7 (private paths only, not in this repo)` |
| 2026-10-28 03:06:22 | hardread | `6be29f9acf01` |
| 2026-10-28 03:09:51 | hardread | `040b23469a5f (private paths only, not in this repo)` |
| 2026-10-28 03:12:19 | hardread | `27f91a1c78dd` |
| 2026-10-28 03:13:14 | hardread | `994f05adcc4e` |
| 2026-10-28 03:17:05 | hardread | `9976c43d3fa5` |
| 2026-10-28 03:28:14 | hardread | `9eaeb4d7e40f` |
| 2026-10-28 03:38:44 | hardread | `8ec4451b184f` |
| 2026-10-28 04:04:13 | hardread | `ba014223bf76` |
| 2026-10-28 04:07:12 | hardread | `546fdadb2ecb` |
| 2026-10-28 04:17:44 | hardread | `4463ba02dc0a` |
| 2026-10-28 04:19:23 | hardread | `c748565bca67` |
| 2026-10-28 04:52:07 | hardread | `caf6c2d798b3` |
| 2026-10-28 05:22:09 | hardread | `724dfccc833d` |
| 2026-10-28 05:37:22 | hardread | `8b3ce196c2df` |
| 2026-10-28 06:28:43 | hardread | `4b793a4ad0bb` |
| 2026-10-28 06:40:43 | hardread | `4f47b6f463ee` |
| 2026-10-28 07:41:20 | hardread | `06809547f09c` |
| 2026-10-28 07:43:17 | hardread | `15d1f21eba83` |
| 2026-10-28 07:47:00 | hardread | `1b296742bde5` |
| 2026-10-28 07:55:53 | hardread | `f62feddb7603` |
| 2026-10-28 08:03:57 | hardread | `4fcc91bb6036` |
| 2026-10-28 09:10:12 | hardread | `99e3702cc4ce` |
| 2026-10-28 11:00:12 | hardread | `088d04bf5068 (private paths only, not in this repo)` |
| 2026-10-28 11:00:12 | hardread | `d6712b9c8117` |
| 2026-10-28 11:01:27 | hardread | `8cebd1db7c13` |
| 2026-10-28 11:03:13 | hardread | `d60c08e8a288` |
| 2026-10-28 11:05:40 | hardread | `eab257324104` |
| 2026-10-28 11:11:21 | hardread | `7846756bb6a2 (private paths only, not in this repo)` |
| 2026-10-28 11:26:35 | hardread | `7a4c18540c29 (private paths only, not in this repo)` |
| 2026-10-28 11:30:01 | hardread | `987e139d5526` |
| 2026-10-28 11:45:57 | hardread | `5bdb46816a55 (private paths only, not in this repo)` |
| 2026-10-28 12:10:57 | hardread | `320e619c7223` |
| 2026-10-28 12:36:53 | hardread | `ba74e798e11a` |
| 2026-10-28 12:40:21 | hardread | `16792b92738b (private paths only, not in this repo)` |
| 2026-10-28 13:01:18 | hardread | `5cb2af264f1f` |
| 2026-10-28 13:07:33 | hardread | `ebbbf2157f93` |
| 2026-10-28 13:24:53 | hardread | `ef599d09ea79` |
| 2026-10-28 13:29:53 | hardread | `10899473ea6a` |
| 2026-10-28 13:32:00 | hardread | `c7db58f0de86` |
| 2026-10-28 13:35:46 | hardread | `09566d97fcd2` |
| 2026-10-28 14:03:46 | hardread | `b8ab6c739299` |
| 2026-10-28 14:09:50 | hardread | `554009d4a73c` |
| 2026-10-28 14:10:56 | hardread | `65cb57c902b7 (private paths only, not in this repo)` |
| 2026-10-28 14:17:11 | hardread | `b68ecdad03ab` |
| 2026-10-28 14:17:11 | hardread | `8bbd18bc8fdb` |
| 2026-10-28 14:31:24 | hardread | `0b9e32d19e30` |
| 2026-10-28 14:32:19 | hardread | `2212283687cb (private paths only, not in this repo)` |
| 2026-10-28 14:33:04 | hardread | `c3bb0ac24079 (private paths only, not in this repo)` |
| 2026-10-28 14:35:56 | hardread | `ae607ebe0180` |
| 2026-10-28 14:35:56 | hardread | `88364b30fff9` |
| 2026-10-28 15:18:03 | hardread | `863cd3fd5262` |
| 2026-10-28 15:18:38 | hardread | `8f97354d95b0 (private paths only, not in this repo)` |
| 2026-10-28 15:23:23 | hardread | `305f4b0a73ea` |
| 2026-10-28 15:41:29 | hardread | `8ff2ad9a9c56 (private paths only, not in this repo)` |
| 2026-10-28 15:47:18 | hardread | `ad86e24494ec` |
| 2026-10-28 15:49:35 | hardread | `9ef7a2566ed4` |
| 2026-10-28 15:52:13 | hardread | `f7c79fe4b30b` |
| 2026-10-28 15:52:40 | hardread | `180778ac210b (private paths only, not in this repo)` |
| 2026-10-28 16:15:03 | hardread | `ade8d7908cb5` |
| 2026-10-28 16:16:57 | hardread | `94ff9973390f (private paths only, not in this repo)` |
| 2026-10-28 16:19:20 | hardread | `35e975841ce0` |
| 2026-10-28 16:35:05 | hardread | `430f941e8e92 (private paths only, not in this repo)` |
| 2026-10-28 16:35:05 | hardread | `e24a95231fc9 (private paths only, not in this repo)` |
| 2026-10-28 16:52:08 | hardread | `5bbdb0cbea26` |
| 2026-10-28 16:53:27 | hardread | `5fb454f49260` |
| 2026-10-28 16:54:30 | hardread | `589850bce07e (private paths only, not in this repo)` |
| 2026-10-28 17:12:30 | hardread | `8c985d604d55` |
| 2026-10-28 17:18:40 | hardread | `a3c03293dbd9 (private paths only, not in this repo)` |
| 2026-10-28 17:23:32 | hardread | `060fc4bb596c (private paths only, not in this repo)` |
| 2026-10-28 17:23:33 | hardread | `c7624a252b1c (private paths only, not in this repo)` |
| 2026-10-28 17:25:57 | hardread | `2dae6b379001 (private paths only, not in this repo)` |
| 2026-10-28 17:38:46 | hardread | `c45af26f3123` |
| 2026-10-28 17:40:59 | hardread | `edc4245e3e56` |
| 2026-10-28 17:47:57 | hardread | `76fda1aed077` |
| 2026-10-28 17:58:55 | hardread | `75ac84536d60 (private paths only, not in this repo)` |
| 2026-10-28 18:01:35 | hardread | `1250ad94822c` |
| 2026-10-28 18:03:13 | hardread | `7c5ccf9c6b5b (private paths only, not in this repo)` |
| 2026-10-28 18:10:17 | hardread | `53258d353c08` |
| 2026-10-28 18:15:51 | hardread | `94957ef7da3b (private paths only, not in this repo)` |
| 2026-10-28 18:32:19 | hardread | `463c33c8b827` |
| 2026-10-28 18:37:09 | hardread | `3c30e1c5af95 (private paths only, not in this repo)` |
| 2026-10-28 18:38:24 | hardread | `3155947e5232 (private paths only, not in this repo)` |
| 2026-10-28 18:43:01 | hardread | `6fefaf326fdf` |
| 2026-10-28 18:45:26 | hardread | `3b5fddeaba1c (private paths only, not in this repo)` |
| 2026-10-28 18:47:11 | hardread | `c8b2edbf264f` |
| 2026-10-28 18:56:44 | hardread | `2f811ccbb76e` |
| 2026-10-28 19:17:47 | hardread | `fa412376e160 (private paths only, not in this repo)` |
| 2026-10-28 19:23:36 | hardread | `2384179f261e` |
| 2026-10-28 19:41:48 | hardread | `252b17da3e01` |
| 2026-10-28 19:43:48 | hardread | `bd8957563302 (private paths only, not in this repo)` |
| 2026-10-28 19:55:10 | hardread | `0c659d37f97a` |
| 2026-10-28 20:11:02 | hardread | `25260d3a880b (private paths only, not in this repo)` |
| 2026-10-28 20:26:00 | hardread | `102f24969373 (private paths only, not in this repo)` |
| 2026-10-28 20:29:54 | hardread | `24d617418287` |
| 2026-10-28 20:32:33 | hardread | `e2011e4dfc99 (private paths only, not in this repo)` |
| 2026-10-28 20:36:54 | hardread | `c79a94e248fb (private paths only, not in this repo)` |
| 2026-10-28 21:07:13 | hardread | `f57d6016c514 (private paths only, not in this repo)` |
| 2026-10-28 21:11:32 | hardread | `37620ef1c999 (private paths only, not in this repo)` |
| 2026-10-28 21:17:56 | hardread | `7646136a33cb (private paths only, not in this repo)` |
| 2026-10-28 21:19:08 | hardread | `0c691884b9bf (private paths only, not in this repo)` |
| 2026-10-28 21:44:00 | hardread | `6329141989e3 (private paths only, not in this repo)` |
| 2026-10-28 21:44:00 | hardread | `cff1ce759596 (private paths only, not in this repo)` |
| 2026-10-28 21:49:16 | hardread | `498490711781 (private paths only, not in this repo)` |
| 2026-10-28 21:54:54 | hardread | `2ba6d8f88e7c (private paths only, not in this repo)` |
| 2026-10-28 21:54:54 | hardread | `5abb6aa20883 (private paths only, not in this repo)` |
| 2026-10-28 22:07:57 | hardread | `15eaeef336c4 (private paths only, not in this repo)` |
| 2026-10-28 22:11:40 | hardread | `292d3e10fa84 (private paths only, not in this repo)` |
| 2026-10-28 22:39:15 | hardread | `2dd5e48a16c8 (private paths only, not in this repo)` |
| 2026-10-28 22:43:15 | hardread | `4a68ebcde22d` |
| 2026-10-28 22:44:25 | hardread | `afb31e09d260 (private paths only, not in this repo)` |
| 2026-10-28 22:46:17 | hardread | `73e3a107446f (private paths only, not in this repo)` |
| 2026-10-28 22:50:16 | hardread | `4879b0ab7f53 (private paths only, not in this repo)` |
| 2026-10-28 22:58:13 | hardread | `7e791a1febf0` |
| 2026-10-28 22:59:53 | hardread | `90e017f4f4ad` |
| 2026-10-28 23:00:58 | hardread | `f747cf00840a (private paths only, not in this repo)` |
| 2026-10-28 23:03:58 | hardread | `2acf07a638a2 (private paths only, not in this repo)` |
| 2026-10-28 23:05:10 | hardread | `c820a757ee1e (private paths only, not in this repo)` |
| 2026-10-28 23:12:00 | hardread | `59981da65fa3 (private paths only, not in this repo)` |
| 2026-10-28 23:15:23 | hardread | `15470bdec315` |
| 2026-10-28 23:30:43 | hardread | `d3f3f07555cc` |
| 2026-10-28 23:35:17 | hardread | `de298f377078 (private paths only, not in this repo)` |
| 2026-10-28 23:36:19 | hardread | `da8b94036e7d (private paths only, not in this repo)` |
| 2026-10-28 23:41:21 | hardread | `38ec6c7ec482 (private paths only, not in this repo)` |
| 2026-10-28 23:42:08 | hardread | `ef4110c6e859 (private paths only, not in this repo)` |
| 2026-10-28 23:43:46 | hardread | `92ed2ec28716` |
| 2026-10-28 23:46:33 | hardread | `de4c0c3546e8 (private paths only, not in this repo)` |
| 2026-10-28 23:48:55 | hardread | `7fe1e7ef65eb (private paths only, not in this repo)` |
| 2026-10-28 23:51:03 | hardread | `f6123ca7e80f (private paths only, not in this repo)` |
| 2026-10-28 23:51:38 | hardread | `51d732f1c47c (private paths only, not in this repo)` |
| 2026-10-28 23:54:05 | hardread | `b5cb24cd6717 (private paths only, not in this repo)` |
| 2026-10-29 00:01:42 | hardread | `2e780a9b2e7e` |
| 2026-10-29 00:01:59 | hardread | `49a823202d5a (private paths only, not in this repo)` |
| 2026-10-29 00:04:10 | hardread | `d73c1ae115ed (private paths only, not in this repo)` |
| 2026-10-29 00:05:17 | hardread | `2d13cb8d51cb (private paths only, not in this repo)` |
| 2026-10-29 00:09:42 | hardread | `723e7dcb31f0 (private paths only, not in this repo)` |
| 2026-10-29 00:18:16 | hardread | `66105b82ca22 (private paths only, not in this repo)` |
| 2026-10-29 00:19:14 | hardread | `b6b473aa2ba9` |
| 2026-10-29 00:22:03 | hardread | `91878e09e5fb (private paths only, not in this repo)` |
| 2026-10-29 00:30:38 | hardread | `8d45dbbfcb95` |
| 2026-10-29 00:30:38 | hardread | `d5aec0c7713b` |
| 2026-10-29 00:34:54 | hardread | `321f46b88fa2 (private paths only, not in this repo)` |
| 2026-10-29 00:41:21 | hardread | `2f1c13fc3eb8` |
| 2026-10-29 00:43:22 | hardread | `c129563d1820 (private paths only, not in this repo)` |
| 2026-10-29 00:55:41 | hardread | `0d5ca264934b (private paths only, not in this repo)` |
| 2026-10-29 00:59:50 | hardread | `b5b176ad8314 (private paths only, not in this repo)` |
| 2026-10-29 01:05:08 | hardread | `e45d1f5947a8 (private paths only, not in this repo)` |
| 2026-10-29 01:14:49 | hardread | `94337714267f (private paths only, not in this repo)` |
| 2026-10-29 01:23:27 | hardread | `85068c741880` |
| 2026-10-29 01:23:57 | hardread | `bba37d469bd2` |
| 2026-10-29 01:29:11 | hardread | `a798722d3e90 (private paths only, not in this repo)` |
| 2026-10-29 01:31:45 | hardread | `cbdaf5304498` |
| 2026-10-29 01:33:02 | hardread | `0dcda583fd29 (private paths only, not in this repo)` |
| 2026-10-29 01:47:37 | hardread | `b9b985017f70 (private paths only, not in this repo)` |
| 2026-10-29 01:55:25 | hardread | `d048bae0bf96 (private paths only, not in this repo)` |
| 2026-10-29 01:56:15 | hardread | `e242d05cfe78` |
| 2026-10-29 01:59:17 | hardread | `7b1e038aa3d1 (private paths only, not in this repo)` |
| 2026-10-29 02:03:11 | hardread | `2d87640471ad (private paths only, not in this repo)` |
| 2026-10-29 02:34:49 | hardread | `76d40b29fdf9 (private paths only, not in this repo)` |
| 2026-10-29 02:36:11 | hardread | `55624121ac09` |
| 2026-10-29 02:36:33 | hardread | `0fc947bbbc27 (private paths only, not in this repo)` |
| 2026-10-29 02:38:08 | hardread | `0bde895444d4` |
| 2026-10-29 02:38:29 | hardread | `f7e97456049f (private paths only, not in this repo)` |
| 2026-10-29 02:40:55 | hardread | `e94c3db427bf (private paths only, not in this repo)` |
| 2026-10-29 02:42:41 | hardread | `f86b77749810 (private paths only, not in this repo)` |
| 2026-10-29 02:50:28 | hardread | `0f759bfd0d09 (private paths only, not in this repo)` |
| 2026-10-29 02:50:49 | hardread | `33824389514a (private paths only, not in this repo)` |
| 2026-10-29 02:57:00 | hardread | `94c1e03038a6 (private paths only, not in this repo)` |
| 2026-10-29 03:04:34 | hardread | `b4a656a4cbb7 (private paths only, not in this repo)` |
| 2026-10-29 03:09:17 | hardread | `f8deaf9e19b0 (private paths only, not in this repo)` |
| 2026-10-29 03:09:44 | hardread | `fde39cef13c3 (private paths only, not in this repo)` |
| 2026-10-29 03:25:34 | hardread | `e59e335ed8a2 (private paths only, not in this repo)` |
| 2026-10-29 03:53:58 | hardread | `3910eb09b2ef` |
| 2026-10-29 03:56:39 | hardread | `e5032ee720e9` |
| 2026-10-29 04:09:10 | hardread | `ae34c99f591d (private paths only, not in this repo)` |
| 2026-10-29 04:19:15 | hardread | `b927d3b7ec20 (private paths only, not in this repo)` |
| 2026-10-29 04:35:51 | hardread | `551a82eb4494 (private paths only, not in this repo)` |
| 2026-10-29 04:46:21 | hardread | `f3f6732913a4` |
| 2026-10-29 04:47:28 | hardread | `adc3bb6c161d` |
| 2026-10-29 04:55:17 | hardread | `d0ff387f9b10` |
| 2026-10-29 04:57:23 | hardread | `88c157d3be46 (private paths only, not in this repo)` |
| 2026-10-29 04:59:35 | hardread | `d0908a2a92d7 (private paths only, not in this repo)` |
| 2026-10-29 05:01:25 | hardread | `c7b9fb42c627 (private paths only, not in this repo)` |
| 2026-10-29 05:16:33 | hardread | `d6c4bab38288` |
| 2026-10-29 05:21:39 | hardread | `bb2ae04ae4c4 (private paths only, not in this repo)` |
| 2026-10-29 05:29:53 | hardread | `fdc98a47562e (private paths only, not in this repo)` |
| 2026-10-29 05:43:20 | hardread | `92b71a9ad82c (private paths only, not in this repo)` |
| 2026-10-29 05:46:15 | hardread | `fac4253a3c8c (private paths only, not in this repo)` |
| 2026-10-29 06:39:18 | hardread | `5616ccecaf1b (private paths only, not in this repo)` |
| 2026-10-29 07:29:18 | hardread | `16df5e7abf38` |
| 2026-10-29 07:47:34 | hardread | `16099764666e (private paths only, not in this repo)` |
| 2026-10-29 07:49:40 | hardread | `ad7068084329 (private paths only, not in this repo)` |
| 2026-10-29 08:06:34 | hardread | `0dfcd079ee97 (private paths only, not in this repo)` |
| 2026-10-29 08:07:07 | hardread | `6c47d562ab04 (private paths only, not in this repo)` |
| 2026-10-29 08:07:36 | hardread | `9cff7a88a054 (private paths only, not in this repo)` |
| 2026-10-29 08:08:04 | hardread | `8c2ca8476737 (private paths only, not in this repo)` |
| 2026-10-29 08:13:48 | hardread | `baf3cbdb3308` |
| 2026-10-29 08:17:56 | hardread | `47f6277c0c8b` |
| 2026-10-29 08:38:04 | hardread | `638a28f64ecb (private paths only, not in this repo)` |
| 2026-10-29 08:50:03 | hardread | `4bb53a2093f4 (private paths only, not in this repo)` |
| 2026-10-29 08:50:32 | hardread | `cff4bfac7d57 (private paths only, not in this repo)` |
| 2026-10-29 08:58:06 | hardread | `115a2facda09` |
| 2026-10-29 09:12:29 | hardread | `93a8517bfe33 (private paths only, not in this repo)` |
| 2026-10-29 09:17:22 | hardread | `68e3a39ff218 (private paths only, not in this repo)` |
| 2026-10-29 09:28:11 | hardread | `6aedb7f2ecca` |
| 2026-10-29 09:53:22 | hardread | `b03214fe71c0` |

## Append-only main-history extension through `4aed64ffb566877be14d5ab173e08effe79a5935`

| UTC | Repo | Commit |
|---|---|---|
| 2026-10-29 10:21:16 | hardread | `cf7a2a77c0f8 (private paths only, not in this repo)` |
| 2026-10-29 10:35:53 | hardread | `e10b499dfc21` |
| 2026-10-29 11:15:41 | hardread | `0a4850928f27` |
| 2026-10-29 11:18:58 | hardread | `60cd8e7a2ad3 (private paths only, not in this repo)` |
| 2026-10-29 11:19:19 | hardread | `4f11090d8813` |
| 2026-10-29 11:30:51 | hardread | `d92b7dee2df0` |
| 2026-10-29 11:32:05 | hardread | `74215b97a443 (private paths only, not in this repo)` |
| 2026-10-29 11:35:36 | hardread | `a86a118f9dd4 (private paths only, not in this repo)` |
| 2026-10-29 11:58:41 | hardread | `097c9f60ddba (private paths only, not in this repo)` |
| 2026-10-29 12:08:42 | hardread | `3cc042e37883 (private paths only, not in this repo)` |
| 2026-10-29 12:24:18 | hardread | `5c156d5e8273` |
| 2026-10-29 12:34:09 | hardread | `f7d3da2985be (private paths only, not in this repo)` |
| 2026-10-29 12:39:57 | hardread | `187bb6d250d5` |
| 2026-10-29 13:00:18 | hardread | `612d8024b588 (private paths only, not in this repo)` |
| 2026-10-29 13:02:56 | hardread | `0a0b59507768 (private paths only, not in this repo)` |
| 2026-10-29 13:07:06 | hardread | `ccac1aa97753 (private paths only, not in this repo)` |
| 2026-10-29 13:24:29 | hardread | `072e9e0e5ff5 (private paths only, not in this repo)` |
| 2026-10-29 13:33:03 | hardread | `c47d19646bfb` |
| 2026-10-29 13:35:14 | hardread | `13851d2bcce8 (private paths only, not in this repo)` |
| 2026-10-29 13:38:18 | hardread | `2016e685f72c (private paths only, not in this repo)` |
| 2026-10-29 13:55:49 | hardread | `b485067a1558 (private paths only, not in this repo)` |
| 2026-10-29 14:35:44 | hardread | `b3ce6ad4cb3e` |
| 2026-10-29 14:56:01 | hardread | `e7610a03453f` |
| 2026-10-29 15:13:56 | hardread | `d79d6e7fef85` |
| 2026-10-29 15:42:19 | hardread | `c9729af1ee74` |
| 2026-10-29 15:55:31 | hardread | `99cfe71dce4a` |
| 2026-10-29 15:55:54 | hardread | `a526fc73291b (private paths only, not in this repo)` |
| 2026-10-29 16:14:00 | hardread | `cb43408a4be6` |
| 2026-10-29 16:19:32 | hardread | `4aed64ffb566` |

## Verify

The commands and counts below describe the private history this ledger was generated from. Filtering removed commits that touched only private paths, so counts here will be lower.

```sh
git -C hardread rev-list --count b03214fe71c0d75a5caafba95494783a2f42ac3a
git -C hardread log --reverse --format='%cI %H' b03214fe71c0d75a5caafba95494783a2f42ac3a..4aed64ffb566877be14d5ab173e08effe79a5935
git -C hardread log --reverse --format='%h %cI %s' b03214fe71c0d75a5caafba95494783a2f42ac3a --before=2026-10-26T16:00:00Z
git -C hardread log --reverse --format='%h %cI %s' b03214fe71c0d75a5caafba95494783a2f42ac3a --since=2026-10-26T16:00:00Z --before=2026-10-27T04:00:00Z
git -C hardread log --reverse --format='%h %cI %s' b03214fe71c0d75a5caafba95494783a2f42ac3a --since=2026-10-27T04:00:00Z
git -C foul-play rev-list --count 3264ac8bf0b42178b8c8561f1702fe74bc64a3d9..175e5916880c7b42c53527b5f280bef8e383af2a
git -C foul-play log --reverse --format='%h %cI %s' 3264ac8bf0b42178b8c8561f1702fe74bc64a3d9..175e5916880c7b42c53527b5f280bef8e383af2a
```

Git timestamps come from commit metadata. They describe the recorded history; they are evidence of ordering, not proof of when work was performed.

## W5 selected milestone supplement through exact main commit `404f02b935b463f27a0a85b7ed889a6083016ed8` (partial; not final or release-locked)

- This selected milestone supplement is bounded to first-party judged-artifact source and documentation changes after `4aed64ffb566877be14d5ab173e08effe79a5935` (committer time 2026-10-29T16:19:32Z) through `404f02b935b463f27a0a85b7ed889a6083016ed8` (committer time 2026-10-30T07:23:36Z). It is not a full history audit. The original time bands, earlier feature rows, existing commit rows, and prior cutoff disclosures remain unchanged.
- Git committer timestamps below record ordering, not the time when work was performed. A source or artifact entry does not establish a successful build, installation, rendering, runtime, or release readiness.
- The tournament result belongs to Foul Play, not HardRead. Third-party work is not claimed as ours. This supplement makes no licence-compatibility, adoption, or redistribution determination.

| Selected milestone | Kind | Commit timestamp(s), UTC | Source/artifact paths |
|---|---|---|---|
| Ladder headline metadata | artifact-metadata | `84bd80f21f71` 2026-10-30T04:45:14Z | `ops/video/ladder-headline.json` |
| Own-ladder review component | product | `57b426612b31` 2026-10-30T04:50:54Z | `app/own_ladder_review.py` |
| Own-ladder selector component | product | `77f44fdd5711` 2026-10-30T06:02:29Z | `app/own_ladder_selection.py`, `tools/agent/run_own_ladder_selector.py` |
| Own-ladder postgame pane | product | `13a57caf3a40` 2026-10-30T06:13:02Z, `823f857a9621` 2026-10-30T06:17:19Z, `277199093b64` 2026-10-30T06:32:55Z | `app/own_ladder_postgame.py`, `app/server.py`, `app/static/app.js`, `app/static/index.html`, `app/static/style.css` |
| Science-chart visualization | artifact | `937e51e4aa97` 2026-10-30T05:38:14Z | `ops/video/science-charts-v3/chart-data.json`, `ops/video/science-charts-v3/five-abyssal-studies.png`, `ops/video/science-charts-v3/five-abyssal-studies.svg`, `ops/video/science-charts-v3/render.py` |
| Manual setup documentation update | documentation | `46d8427b09e7` 2026-10-30T04:29:23Z | `README.md` |
| Selector diagnostics source refinement | product-maintenance | `404f02b935b4` 2026-10-30T07:23:36Z | `tools/agent/run_own_ladder_selector.py` |

- A separate actual-render evidence-record commit is `dcbf5fe09c93 (private paths only, not in this repo)` at 2026-10-30T07:19:45Z; its private filename is omitted here. This records an evidence milestone, not a new product component or a release claim.
- This supplement deliberately selects source milestones; it does not reproduce every commit in the interval or revise earlier counts.
