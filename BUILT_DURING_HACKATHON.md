# Built during the hackathon: what was made when

This provenance ledger is generated from pinned Git history. It describes the captured project snapshot below, not commits added later.

- Repositories: `hardread` (branch `main`, captured HEAD `9f69f1a30c6c75bd687e2a61974cbd309f8bb373`) and `hardread-foulplay` (branch `hardread`, captured HEAD `f061e63d3d588f30e72af2f4a7d12d3101896d02`); fork-only commits are those after upstream `8f541f80f91f960603673796559f796b10ccf0fb`.
- The source plan preserved these point-in-time refs but not their capture timestamp. Generated 2026-10-29 10:29:15 UTC from those fixed refs. The main history has advanced since this snapshot; refresh the ledger for the final release candidate.
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
| Replay reconstruction (what each player knew, turn by turn) | product | `tools/review/reconstruct.py` | bdf99231ea67 | 2026-10-26 18:46:46 | 2 |
| Search evaluation and win probability | product | `tools/review/evaluate.py`, `tools/review/value.py` | bdf99231ea67 | 2026-10-26 18:46:46 | 2 |
| Win-probability calibration | product | `tools/review/calibrate.py` | bdf99231ea67 | 2026-10-26 18:46:46 | 2 |
| Luck meter | product | `tools/review/luck.py` | 0143795e5415 | 2026-10-26 20:13:47 | 2 |
| Review JSON / CLI | product | `tools/review/review.py` | bdf99231ea67 | 2026-10-26 18:46:46 | 2 |
| Review on human games (Showdown replays) | product | `tools/review/human_eval.py` | e43fbf732477 | 2026-10-26 22:48:36 | 2 |
| Bridge: Start → Luck → Choices → Result | product | `tools/review/bridge.py` | 0ead27f0ea7f | 2026-10-27 04:44:48 | 3 |
| Bridge validation prototype | product | `tools/review/proposed/validate_bridge.py` | e1a17110d458 | 2026-10-27 04:11:32 | 3 |
| Bridge validation CLI | product | `tools/review/validate_bridge.py` | 0ead27f0ea7f | 2026-10-27 04:44:48 | 3 |
| Verdict (Misplay / Misread / Defensible / Within noise) | product | `tools/review/verdict.py` | 9705a6cb1fda | 2026-10-27 04:54:47 | 3 |
| Glimmer explainer with verifier | product | `tools/agent/explain.py`, `tools/agent/verifier.py` | e064e2756571 | 2026-10-27 05:10:05 | 3 |
| Note parser (what you were thinking → stated read) | product | `tools/agent/parse_note.py` | e064e2756571 | 2026-10-27 05:10:05 | 3 |
| Web app | product | `app/server.py` | 53ad910c9dcc | 2026-10-27 05:00:24 | 3 |
| Match runner (self-play harness) | science | `tools/run_match.py` | bc297c3eaef0 | 2026-10-26 12:35:18 | 1 |
| Oracle injection (value-of-information experiment) | science | `fp/hardread_oracle.py` | 43edaa3eb1e3 | 2026-10-26 12:40:41 | 1 |
| Decision log | science | `fp/hardread_decision_log.py` | 455f51545ba6 | 2026-10-26 14:30:26 | 1 |
| Pre-registration of the sealed test | science | `PREREGISTRATION.md` | ad60ab252fee | 2026-10-26 16:27:46 | 2 |
| Engine levers (value, double-half, student-blend, belief) | science | `fp/hardread_levers.py` | 4c77cd3f5558 | 2026-10-26 19:27:28 | 2 |
| Value-of-information analysis | science | `tools/analysis/voi.py` | ad60ab252fee | 2026-10-26 16:27:46 | 2 |
| 25M student policy/value model | science | `tools/student/model.py` | 69251f384038 | 2026-10-26 19:33:27 | 2 |
| Belief model | science | `tools/belief/model.py` | e6ff0880053c | 2026-10-26 19:15:04 | 2 |
| Glimmer fine-tuning pipeline | science | `tools/sft/serialize.py`, `azure/sft/run_sft.py` | 99b2bfcf09c7 | 2026-10-26 19:39:59 | 2 |
| Arena vs PokéChamp's abyssal bot | science | `tools/arena/run_arena.py` | 6be8560c796a | 2026-10-26 19:32:03 | 2 |

## Commit ledger

Each row is a commit reachable from the pinned refs. Subject text is intentionally omitted.

| UTC | Repo | Commit |
|---|---|---|
| 2026-10-26 12:21:44 | foul-play fork | `102886c13c63` |
| 2026-10-26 12:35:18 | hardread | `bc297c3eaef0` |
| 2026-10-26 12:35:41 | hardread | `4f66039d3e8d` |
| 2026-10-26 12:40:41 | foul-play fork | `43edaa3eb1e3` |
| 2026-10-26 12:42:56 | hardread | `5204d1e1f706` |
| 2026-10-26 13:49:53 | hardread | `b478fa367cf8` |
| 2026-10-26 13:51:51 | hardread | `7fc90b652cd6` |
| 2026-10-26 14:09:24 | hardread | `4153da39a570` |
| 2026-10-26 14:16:04 | hardread | `b4a23f4dd28e` |
| 2026-10-26 14:28:19 | hardread | `0b4603fd58bf` |
| 2026-10-26 14:28:52 | hardread | `d31c984fc494` |
| 2026-10-26 14:30:26 | foul-play fork | `455f51545ba6` |
| 2026-10-26 14:30:26 | hardread | `9f3b56cfa9b9` |
| 2026-10-26 14:30:26 | hardread | `c845541b3dce` |
| 2026-10-26 16:02:34 | hardread | `6f3af1944146` |
| 2026-10-26 16:11:20 | hardread | `4978e1bbecd7` |
| 2026-10-26 16:13:09 | foul-play fork | `6a896ef670d0` |
| 2026-10-26 16:22:09 | hardread | `9d81e34a589d` |
| 2026-10-26 16:27:46 | hardread | `ad60ab252fee` |
| 2026-10-26 16:48:33 | hardread | `f8d617a469cb` |
| 2026-10-26 16:53:59 | hardread | `194d0bb941bd` |
| 2026-10-26 18:46:46 | hardread | `bdf99231ea67` |
| 2026-10-26 18:47:18 | hardread | `1ea0b8189cfd` |
| 2026-10-26 19:15:04 | hardread | `e6ff0880053c` |
| 2026-10-26 19:27:28 | foul-play fork | `4c77cd3f5558` |
| 2026-10-26 19:27:28 | hardread | `dccb42559be5` |
| 2026-10-26 19:29:46 | hardread | `3b51b08fd4c3` |
| 2026-10-26 19:30:02 | hardread | `035f8fa85b37` |
| 2026-10-26 19:31:32 | hardread | `2ecc82686474` |
| 2026-10-26 19:32:03 | hardread | `6be8560c796a` |
| 2026-10-26 19:33:27 | hardread | `69251f384038` |
| 2026-10-26 19:34:48 | foul-play fork | `1a94ed80847c` |
| 2026-10-26 19:34:48 | hardread | `ba54307d243a` |
| 2026-10-26 19:39:59 | hardread | `99b2bfcf09c7` |
| 2026-10-26 19:40:11 | hardread | `6b52e302be7c` |
| 2026-10-26 20:08:21 | hardread | `bf0dcdfa595a` |
| 2026-10-26 20:13:47 | hardread | `0143795e5415` |
| 2026-10-26 20:13:47 | hardread | `79432323febf` |
| 2026-10-26 20:36:59 | hardread | `99659952b35a` |
| 2026-10-26 22:39:11 | hardread | `4dd0000913db` |
| 2026-10-26 22:43:58 | foul-play fork | `79a86c912e56` |
| 2026-10-26 22:43:58 | hardread | `394e58e7dcd4` |
| 2026-10-26 22:48:36 | hardread | `31cfebbfc777` |
| 2026-10-26 22:48:36 | hardread | `e43fbf732477` |
| 2026-10-26 22:48:54 | hardread | `7cfbd7e23718` |
| 2026-10-26 22:58:50 | hardread | `aaacf28c884c` |
| 2026-10-26 22:59:13 | hardread | `35bb7fd9313e` |
| 2026-10-26 23:07:24 | hardread | `7b49a708cce9` |
| 2026-10-26 23:07:24 | hardread | `f7e5c8897db9` |
| 2026-10-26 23:08:03 | hardread | `661b5e2d59d8` |
| 2026-10-26 23:53:26 | hardread | `925dd35de932` |
| 2026-10-26 23:53:26 | hardread | `f589f7ee1f38` |
| 2026-10-26 23:53:35 | hardread | `5ee53a03a260` |
| 2026-10-27 00:23:22 | foul-play fork | `f061e63d3d58` |
| 2026-10-27 00:23:22 | hardread | `4a60e53f3dcd` |
| 2026-10-27 00:24:58 | hardread | `6f78e1153041` |
| 2026-10-27 00:50:48 | hardread | `b9dab381996d` |
| 2026-10-27 02:52:49 | hardread | `d2a75e9b1152` |
| 2026-10-27 04:10:08 | hardread | `d24b6a2cc35f` |
| 2026-10-27 04:10:08 | hardread | `f4a26d7787b8` |
| 2026-10-27 04:10:20 | hardread | `f60562fed03c` |
| 2026-10-27 04:11:32 | hardread | `e1a17110d458` |
| 2026-10-27 04:37:04 | hardread | `7b3fae3e01eb` |
| 2026-10-27 04:37:10 | hardread | `169d9939dff1` |
| 2026-10-27 04:37:22 | hardread | `7aff697f44eb` |
| 2026-10-27 04:44:48 | hardread | `0ead27f0ea7f` |
| 2026-10-27 04:48:55 | hardread | `00e7bba09def` |
| 2026-10-27 04:54:47 | hardread | `9705a6cb1fda` |
| 2026-10-27 05:00:24 | hardread | `53ad910c9dcc` |
| 2026-10-27 05:08:37 | hardread | `99e619cb626c` |
| 2026-10-27 05:10:05 | hardread | `e064e2756571` |
| 2026-10-27 05:10:29 | hardread | `fe570ee29cc1` |
| 2026-10-27 05:17:54 | hardread | `710e931671fa` |
| 2026-10-27 05:46:20 | hardread | `37177f1be532` |
| 2026-10-27 05:47:40 | hardread | `4851397c7667` |
| 2026-10-27 05:50:04 | hardread | `6f90728400a3` |
| 2026-10-27 05:50:23 | hardread | `ed7d623b47c1` |
| 2026-10-27 05:51:37 | hardread | `1700f946e692` |
| 2026-10-27 05:51:52 | hardread | `2e21336d841e` |
| 2026-10-27 05:52:26 | hardread | `7f71a2419514` |
| 2026-10-27 05:54:30 | hardread | `7c676c0aa840` |
| 2026-10-27 05:56:59 | hardread | `e2738a940dfc` |
| 2026-10-27 06:19:56 | hardread | `158dd0acf36b` |
| 2026-10-27 08:26:42 | hardread | `ac2de6151a30` |
| 2026-10-27 08:48:31 | hardread | `c6aa21ec6b09` |
| 2026-10-27 08:48:38 | hardread | `343994525841` |
| 2026-10-27 09:12:34 | hardread | `deb98745e1fa` |
| 2026-10-27 09:59:50 | hardread | `151e99ea9ccf` |
| 2026-10-27 10:06:25 | hardread | `e9da68740e9e` |
| 2026-10-27 10:12:07 | hardread | `0c841659931a` |
| 2026-10-27 10:24:23 | hardread | `1ada5c01a18e` |
| 2026-10-27 10:31:03 | hardread | `14c2aa4078a3` |
| 2026-10-27 10:45:10 | hardread | `6188fcacb410` |
| 2026-10-27 10:52:02 | hardread | `dabcfab39766` |
| 2026-10-27 10:52:05 | hardread | `d6e0572453e5` |
| 2026-10-27 11:01:54 | hardread | `219b32a4e44f` |
| 2026-10-27 11:09:01 | hardread | `a3b1a7f78383` |
| 2026-10-27 11:45:05 | hardread | `6165cc653d1e` |
| 2026-10-27 11:46:09 | hardread | `f491c57669b1` |
| 2026-10-27 12:28:58 | hardread | `941e48da7435` |
| 2026-10-27 12:52:18 | hardread | `6b0e29f3eb9a` |
| 2026-10-27 13:15:17 | hardread | `d86d71037fd7` |
| 2026-10-27 13:47:24 | hardread | `484ab07db463` |
| 2026-10-27 13:59:51 | hardread | `9ed3d611c752` |
| 2026-10-27 14:24:39 | hardread | `564f5dabaf0f` |
| 2026-10-27 15:30:30 | hardread | `7513427e2468` |
| 2026-10-27 15:36:44 | hardread | `b2ad835f3309` |
| 2026-10-27 15:37:35 | hardread | `c5e9d500fb9d` |
| 2026-10-27 15:42:17 | hardread | `688914403328` |
| 2026-10-27 16:07:18 | hardread | `e68e73f76228` |
| 2026-10-27 16:42:02 | hardread | `6ad5670da8c7` |
| 2026-10-27 17:05:02 | hardread | `b545fc2c3133` |
| 2026-10-27 17:19:08 | hardread | `04297e00780d` |
| 2026-10-27 17:40:53 | hardread | `5d1c256783d1` |
| 2026-10-27 17:42:10 | hardread | `a58b0c0adc38` |
| 2026-10-27 17:49:38 | hardread | `319b20bc8ba2` |
| 2026-10-27 18:04:39 | hardread | `7252a9e9c050` |
| 2026-10-27 18:13:47 | hardread | `29e462e2853f` |
| 2026-10-27 18:20:09 | hardread | `2d5fa9a57053` |
| 2026-10-27 18:23:20 | hardread | `52a58246fa71` |
| 2026-10-27 18:52:41 | hardread | `e9414f36f8f2` |
| 2026-10-27 18:58:44 | hardread | `592f35f9512d` |
| 2026-10-27 19:35:03 | hardread | `31486004201c` |
| 2026-10-27 19:36:57 | hardread | `31fd7fdac396` |
| 2026-10-27 20:35:48 | hardread | `a2328c90d7f1` |
| 2026-10-27 20:38:35 | hardread | `4e5bc4d63e69` |
| 2026-10-27 20:52:28 | hardread | `a4d967106ad0` |
| 2026-10-27 20:56:32 | hardread | `8545a15cb525` |
| 2026-10-27 21:05:38 | hardread | `cf71837da176` |
| 2026-10-27 21:51:03 | hardread | `e36132278933` |
| 2026-10-27 22:11:55 | hardread | `06e35f62f737` |
| 2026-10-27 22:13:41 | hardread | `9a2881cb5db2` |
| 2026-10-27 22:30:37 | hardread | `7e429e0a11b7` |
| 2026-10-27 22:51:52 | hardread | `6fe5b801bd5d` |
| 2026-10-27 22:54:46 | hardread | `a848534fcc18` |
| 2026-10-27 23:16:41 | hardread | `aed7b9fa5d69` |
| 2026-10-27 23:19:26 | hardread | `295285694858` |
| 2026-10-27 23:25:24 | hardread | `dc7c0d6f803d` |
| 2026-10-28 00:55:52 | hardread | `d887982b9abd` |
| 2026-10-28 01:02:24 | hardread | `0fc2310644e6` |
| 2026-10-28 01:22:29 | hardread | `83e099cc5853` |
| 2026-10-28 01:53:16 | hardread | `0d9cc6daff80` |
| 2026-10-28 02:04:04 | hardread | `61e2f257d036` |
| 2026-10-28 02:06:16 | hardread | `fb1015301859` |
| 2026-10-28 02:24:11 | hardread | `5ccce925d013` |
| 2026-10-28 02:33:46 | hardread | `9b306bd8a68a` |
| 2026-10-28 02:52:38 | hardread | `122c005e01c7` |
| 2026-10-28 03:06:22 | hardread | `158135ec6983` |
| 2026-10-28 03:09:51 | hardread | `040b23469a5f` |
| 2026-10-28 03:12:19 | hardread | `07d544138eb6` |
| 2026-10-28 03:13:14 | hardread | `a6924beb328c` |
| 2026-10-28 03:17:05 | hardread | `fe860bc3efa6` |
| 2026-10-28 03:28:14 | hardread | `4cc5489acae5` |
| 2026-10-28 03:38:44 | hardread | `a8db207e4788` |
| 2026-10-28 04:04:13 | hardread | `f46cc82fa457` |
| 2026-10-28 04:07:12 | hardread | `f0b990f3b25e` |
| 2026-10-28 04:17:44 | hardread | `b6c12ef7abcf` |
| 2026-10-28 04:19:23 | hardread | `389ae1963fe6` |
| 2026-10-28 04:52:07 | hardread | `19dd5ec117bf` |
| 2026-10-28 05:22:09 | hardread | `06292ff886bd` |
| 2026-10-28 05:37:22 | hardread | `a0ca744c6de6` |
| 2026-10-28 06:28:43 | hardread | `25e88c92bfc2` |
| 2026-10-28 06:40:43 | hardread | `ef5a95a142e9` |
| 2026-10-28 07:41:20 | hardread | `53b4a61240ed` |
| 2026-10-28 07:43:17 | hardread | `5627c8c3cc52` |
| 2026-10-28 07:47:00 | hardread | `8d0f80265699` |
| 2026-10-28 07:55:53 | hardread | `a89186b53406` |
| 2026-10-28 08:03:57 | hardread | `3b16788ba236` |
| 2026-10-28 09:10:12 | hardread | `82798752eaf7` |
| 2026-10-28 11:00:12 | hardread | `088d04bf5068` |
| 2026-10-28 11:00:12 | hardread | `3ee88fde16d0` |
| 2026-10-28 11:01:27 | hardread | `4d4254e18b15` |
| 2026-10-28 11:03:13 | hardread | `0f8904cc2716` |
| 2026-10-28 11:05:40 | hardread | `796f754831bf` |
| 2026-10-28 11:11:21 | hardread | `7846756bb6a2` |
| 2026-10-28 11:26:35 | hardread | `7a4c18540c29` |
| 2026-10-28 11:30:01 | hardread | `ba2cb5d49ed1` |
| 2026-10-28 11:45:57 | hardread | `5bdb46816a55` |
| 2026-10-28 12:10:57 | hardread | `3be0b71f3a15` |
| 2026-10-28 12:36:53 | hardread | `ccc32b02a22a` |
| 2026-10-28 12:40:21 | hardread | `16792b92738b` |
| 2026-10-28 13:01:18 | hardread | `b07b0d5dcfbf` |
| 2026-10-28 13:07:33 | hardread | `b59f5336baca` |
| 2026-10-28 13:24:53 | hardread | `8ebdd9963948` |
| 2026-10-28 13:29:53 | hardread | `55c0d4ab902c` |
| 2026-10-28 13:32:00 | hardread | `822257bb4b69` |
| 2026-10-28 13:35:46 | hardread | `d093058c24be` |
| 2026-10-28 14:03:46 | hardread | `7dcfeaab6933` |
| 2026-10-28 14:09:50 | hardread | `b83b96bf2d71` |
| 2026-10-28 14:10:56 | hardread | `65cb57c902b7` |
| 2026-10-28 14:17:11 | hardread | `34fa6cb2feaf` |
| 2026-10-28 14:17:11 | hardread | `6dbea136bdae` |
| 2026-10-28 14:31:24 | hardread | `deb99d115927` |
| 2026-10-28 14:32:19 | hardread | `2212283687cb` |
| 2026-10-28 14:33:04 | hardread | `c3bb0ac24079` |
| 2026-10-28 14:35:56 | hardread | `70ab80157a60` |
| 2026-10-28 14:35:56 | hardread | `788c9e18cf7f` |
| 2026-10-28 15:18:03 | hardread | `1602863d6a00` |
| 2026-10-28 15:18:38 | hardread | `8f97354d95b0` |
| 2026-10-28 15:23:23 | hardread | `6dda3c1bb243` |
| 2026-10-28 15:41:29 | hardread | `8ff2ad9a9c56` |
| 2026-10-28 15:47:18 | hardread | `98dab1428152` |
| 2026-10-28 15:49:35 | hardread | `6fd9df7b1e9f` |
| 2026-10-28 15:52:13 | hardread | `210d09f8f34f` |
| 2026-10-28 15:52:40 | hardread | `180778ac210b` |
| 2026-10-28 16:15:03 | hardread | `00ebb42c05a2` |
| 2026-10-28 16:16:57 | hardread | `94ff9973390f` |
| 2026-10-28 16:19:20 | hardread | `4acc62b9797f` |
| 2026-10-28 16:35:05 | hardread | `430f941e8e92` |
| 2026-10-28 16:35:05 | hardread | `e24a95231fc9` |
| 2026-10-28 16:52:08 | hardread | `2d3ef52fded2` |
| 2026-10-28 16:53:27 | hardread | `50cbaf8134a8` |
| 2026-10-28 16:54:30 | hardread | `589850bce07e` |
| 2026-10-28 17:12:30 | hardread | `a4d924cedb79` |
| 2026-10-28 17:18:40 | hardread | `a3c03293dbd9` |
| 2026-10-28 17:23:32 | hardread | `060fc4bb596c` |
| 2026-10-28 17:23:33 | hardread | `c7624a252b1c` |
| 2026-10-28 17:25:57 | hardread | `2dae6b379001` |
| 2026-10-28 17:38:46 | hardread | `1a23fa0adecd` |
| 2026-10-28 17:40:59 | hardread | `b4b12224fe3b` |
| 2026-10-28 17:47:57 | hardread | `29fd87ef7f35` |
| 2026-10-28 17:58:55 | hardread | `75ac84536d60` |
| 2026-10-28 18:01:35 | hardread | `8696dac642f3` |
| 2026-10-28 18:03:13 | hardread | `7c5ccf9c6b5b` |
| 2026-10-28 18:10:17 | hardread | `6746f4be0f4d` |
| 2026-10-28 18:15:51 | hardread | `94957ef7da3b` |
| 2026-10-28 18:32:19 | hardread | `03168372d418` |
| 2026-10-28 18:37:09 | hardread | `3c30e1c5af95` |
| 2026-10-28 18:38:24 | hardread | `3155947e5232` |
| 2026-10-28 18:43:01 | hardread | `1b5a17a8fe80` |
| 2026-10-28 18:45:26 | hardread | `3b5fddeaba1c` |
| 2026-10-28 18:47:11 | hardread | `ff88b21121c6` |
| 2026-10-28 18:56:44 | hardread | `79cdeb023c15` |
| 2026-10-28 19:17:47 | hardread | `fa412376e160` |
| 2026-10-28 19:23:36 | hardread | `f352b7e41fa8` |
| 2026-10-28 19:41:48 | hardread | `43e3534cfd37` |
| 2026-10-28 19:43:48 | hardread | `bd8957563302` |
| 2026-10-28 19:55:10 | hardread | `83dab9a94a7a` |
| 2026-10-28 20:11:02 | hardread | `25260d3a880b` |
| 2026-10-28 20:26:00 | hardread | `102f24969373` |
| 2026-10-28 20:29:54 | hardread | `b9be2cd79836` |
| 2026-10-28 20:32:33 | hardread | `e2011e4dfc99` |
| 2026-10-28 20:36:54 | hardread | `c79a94e248fb` |
| 2026-10-28 21:07:13 | hardread | `f57d6016c514` |
| 2026-10-28 21:11:32 | hardread | `37620ef1c999` |
| 2026-10-28 21:17:56 | hardread | `7646136a33cb` |
| 2026-10-28 21:19:08 | hardread | `0c691884b9bf` |
| 2026-10-28 21:44:00 | hardread | `6329141989e3` |
| 2026-10-28 21:44:00 | hardread | `cff1ce759596` |
| 2026-10-28 21:49:16 | hardread | `498490711781` |
| 2026-10-28 21:54:54 | hardread | `2ba6d8f88e7c` |
| 2026-10-28 21:54:54 | hardread | `5abb6aa20883` |
| 2026-10-28 22:07:57 | hardread | `15eaeef336c4` |
| 2026-10-28 22:11:40 | hardread | `292d3e10fa84` |
| 2026-10-28 22:39:15 | hardread | `2dd5e48a16c8` |
| 2026-10-28 22:43:15 | hardread | `fc342008fc6a` |
| 2026-10-28 22:44:25 | hardread | `afb31e09d260` |
| 2026-10-28 22:46:17 | hardread | `73e3a107446f` |
| 2026-10-28 22:50:16 | hardread | `4879b0ab7f53` |
| 2026-10-28 22:58:13 | hardread | `534085463600` |
| 2026-10-28 22:59:53 | hardread | `29b04ee487e0` |
| 2026-10-28 23:00:58 | hardread | `f747cf00840a` |
| 2026-10-28 23:03:58 | hardread | `2acf07a638a2` |
| 2026-10-28 23:05:10 | hardread | `c820a757ee1e` |
| 2026-10-28 23:12:00 | hardread | `59981da65fa3` |
| 2026-10-28 23:15:23 | hardread | `285b47c3a82f` |
| 2026-10-28 23:30:43 | hardread | `d1c62fc67f89` |
| 2026-10-28 23:35:17 | hardread | `de298f377078` |
| 2026-10-28 23:36:19 | hardread | `da8b94036e7d` |
| 2026-10-28 23:41:21 | hardread | `38ec6c7ec482` |
| 2026-10-28 23:42:08 | hardread | `ef4110c6e859` |
| 2026-10-28 23:43:46 | hardread | `f436efeb76d4` |
| 2026-10-28 23:46:33 | hardread | `de4c0c3546e8` |
| 2026-10-28 23:48:55 | hardread | `7fe1e7ef65eb` |
| 2026-10-28 23:51:03 | hardread | `f6123ca7e80f` |
| 2026-10-28 23:51:38 | hardread | `51d732f1c47c` |
| 2026-10-28 23:54:05 | hardread | `b5cb24cd6717` |
| 2026-10-29 00:01:42 | hardread | `75af2ce9642d` |
| 2026-10-29 00:01:59 | hardread | `49a823202d5a` |
| 2026-10-29 00:04:10 | hardread | `d73c1ae115ed` |
| 2026-10-29 00:05:17 | hardread | `2d13cb8d51cb` |
| 2026-10-29 00:09:42 | hardread | `723e7dcb31f0` |
| 2026-10-29 00:18:16 | hardread | `66105b82ca22` |
| 2026-10-29 00:19:14 | hardread | `cb6d44aabe5d` |
| 2026-10-29 00:22:03 | hardread | `91878e09e5fb` |
| 2026-10-29 00:30:38 | hardread | `32bef4bde0a6` |
| 2026-10-29 00:30:38 | hardread | `96a4fb8806d1` |
| 2026-10-29 00:34:54 | hardread | `321f46b88fa2` |
| 2026-10-29 00:41:21 | hardread | `dc68af0d91cd` |
| 2026-10-29 00:43:22 | hardread | `c129563d1820` |
| 2026-10-29 00:55:41 | hardread | `0d5ca264934b` |
| 2026-10-29 00:59:50 | hardread | `b5b176ad8314` |
| 2026-10-29 01:05:08 | hardread | `e45d1f5947a8` |
| 2026-10-29 01:14:49 | hardread | `94337714267f` |
| 2026-10-29 01:23:27 | hardread | `b9126c6c51de` |
| 2026-10-29 01:23:57 | hardread | `11a60a776008` |
| 2026-10-29 01:29:11 | hardread | `a798722d3e90` |
| 2026-10-29 01:31:45 | hardread | `e7e2b331832c` |
| 2026-10-29 01:33:02 | hardread | `0dcda583fd29` |
| 2026-10-29 01:47:37 | hardread | `b9b985017f70` |
| 2026-10-29 01:55:25 | hardread | `d048bae0bf96` |
| 2026-10-29 01:56:15 | hardread | `5cea2387da89` |
| 2026-10-29 01:59:17 | hardread | `7b1e038aa3d1` |
| 2026-10-29 02:03:11 | hardread | `2d87640471ad` |
| 2026-10-29 02:34:49 | hardread | `76d40b29fdf9` |
| 2026-10-29 02:36:11 | hardread | `ea2853aae192` |
| 2026-10-29 02:36:33 | hardread | `0fc947bbbc27` |
| 2026-10-29 02:38:08 | hardread | `efc7d96e6020` |
| 2026-10-29 02:38:29 | hardread | `f7e97456049f` |
| 2026-10-29 02:40:55 | hardread | `e94c3db427bf` |
| 2026-10-29 02:42:41 | hardread | `f86b77749810` |
| 2026-10-29 02:50:28 | hardread | `0f759bfd0d09` |
| 2026-10-29 02:50:49 | hardread | `33824389514a` |
| 2026-10-29 02:57:00 | hardread | `94c1e03038a6` |
| 2026-10-29 03:04:34 | hardread | `b4a656a4cbb7` |
| 2026-10-29 03:09:17 | hardread | `f8deaf9e19b0` |
| 2026-10-29 03:09:44 | hardread | `fde39cef13c3` |
| 2026-10-29 03:25:34 | hardread | `e59e335ed8a2` |
| 2026-10-29 03:53:58 | hardread | `6a9b7d6fb0e1` |
| 2026-10-29 03:56:39 | hardread | `64edca86e00c` |
| 2026-10-29 04:09:10 | hardread | `ae34c99f591d` |
| 2026-10-29 04:19:15 | hardread | `b927d3b7ec20` |
| 2026-10-29 04:35:51 | hardread | `551a82eb4494` |
| 2026-10-29 04:46:21 | hardread | `2e0567483340` |
| 2026-10-29 04:47:28 | hardread | `92796a2a40d7` |
| 2026-10-29 04:55:17 | hardread | `6df93f6060ee` |
| 2026-10-29 04:57:23 | hardread | `88c157d3be46` |
| 2026-10-29 04:59:35 | hardread | `d0908a2a92d7` |
| 2026-10-29 05:01:25 | hardread | `c7b9fb42c627` |
| 2026-10-29 05:16:33 | hardread | `5edb48e49b55` |
| 2026-10-29 05:21:39 | hardread | `bb2ae04ae4c4` |
| 2026-10-29 05:29:53 | hardread | `fdc98a47562e` |
| 2026-10-29 05:43:20 | hardread | `92b71a9ad82c` |
| 2026-10-29 05:46:15 | hardread | `fac4253a3c8c` |
| 2026-10-29 06:39:18 | hardread | `5616ccecaf1b` |
| 2026-10-29 07:29:18 | hardread | `a026c2b4f4eb` |
| 2026-10-29 07:47:34 | hardread | `16099764666e` |
| 2026-10-29 07:49:40 | hardread | `ad7068084329` |
| 2026-10-29 08:06:34 | hardread | `0dfcd079ee97` |
| 2026-10-29 08:07:07 | hardread | `6c47d562ab04` |
| 2026-10-29 08:07:36 | hardread | `9cff7a88a054` |
| 2026-10-29 08:08:04 | hardread | `8c2ca8476737` |
| 2026-10-29 08:13:48 | hardread | `b1df670e0352` |
| 2026-10-29 08:17:56 | hardread | `b0e23dc3c7c5` |
| 2026-10-29 08:38:04 | hardread | `638a28f64ecb` |
| 2026-10-29 08:50:03 | hardread | `4bb53a2093f4` |
| 2026-10-29 08:50:32 | hardread | `cff4bfac7d57` |
| 2026-10-29 08:58:06 | hardread | `70d1359b235d` |
| 2026-10-29 09:12:29 | hardread | `93a8517bfe33` |
| 2026-10-29 09:17:22 | hardread | `68e3a39ff218` |
| 2026-10-29 09:28:11 | hardread | `bfa22f177a8f` |
| 2026-10-29 09:53:22 | hardread | `9f69f1a30c6c` |

## Verify

```sh
git -C hardread rev-list --count 9f69f1a30c6c75bd687e2a61974cbd309f8bb373
git -C hardread log --reverse --format='%h %cI %s' 9f69f1a30c6c75bd687e2a61974cbd309f8bb373 --before=2026-10-26T16:00:00Z
git -C hardread log --reverse --format='%h %cI %s' 9f69f1a30c6c75bd687e2a61974cbd309f8bb373 --since=2026-10-26T16:00:00Z --before=2026-10-27T04:00:00Z
git -C hardread log --reverse --format='%h %cI %s' 9f69f1a30c6c75bd687e2a61974cbd309f8bb373 --since=2026-10-27T04:00:00Z
git -C foul-play rev-list --count 8f541f80f91f960603673796559f796b10ccf0fb..f061e63d3d588f30e72af2f4a7d12d3101896d02
git -C foul-play log --reverse --format='%h %cI %s' 8f541f80f91f960603673796559f796b10ccf0fb..f061e63d3d588f30e72af2f4a7d12d3101896d02
```

Git timestamps come from commit metadata. They describe the recorded history; they are evidence of ordering, not proof of when work was performed.
