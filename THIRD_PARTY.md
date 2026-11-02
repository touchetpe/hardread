> Status notes below are dated records. The README is current; where they differ, trust the README. Modal and Starlette hosting entries describe an unused option.

# Third-party software, models and data

HardRead builds on the open-source projects, open model weights and public data listed here. A SHA-256 shown for a locally available source-checkout `LICENSE` or policy file is the hash of that file at the pinned commit; a hash labelled as an embedded wheel `LICENSE`/notice file is the hash of that member in the named package-version wheel. Other rows cite package metadata or the upstream's declared licence, as identified in each row; *not verified locally* means no local copy of the licence file was available to inspect.

**Pokémon** is a trademark of Nintendo, Creatures Inc. and GAME FREAK inc. HardRead is a fan-made research project, not affiliated with or endorsed by Nintendo, The Pokémon Company, Creatures, GAME FREAK, Smogon or Pokémon Showdown. HardRead uses no official art, sprites, logos or music; Pokémon, move and item names appear only as game data.

## Code

| Project | Upstream | Pinned commit / version | Licence (file SHA-256) | How HardRead uses it | Shipped in our repos? |
|---|---|---|---|---|---|
| **Foul Play** (battle bot; PokéAgent Challenge 2025 Gen 9 OU winner's engine) | github.com/pmariglia/foul-play | Upstream base `8f541f80f91f960603673796559f796b10ccf0fb`; separate HardRead fork `f061e63d3d588f30e72af2f4a7d12d3101896d02` | **GPL-3.0** (`3972dc97…6986`) | The separate `hardread-foulplay` checkout adds default-off engine levers, decision logging and hooks used by local experiments. The top-level `LICENSE` is byte-identical to `foul-play/LICENSE`. | Separate dependency checkout; not bundled in this source tree. |
| **poke-engine** (Rust battle search used by Foul Play) | github.com/pmariglia/poke-engine | `bcf13823abc162a608e187b26bbf683f759f385e`; Python package 0.0.48 built with `terastallization` | MIT, © 2024 pmariglia (`38ce7f13…0b4d`) | Search and battle simulation, through Foul Play | No (installed dependency) |
| **Pokémon Showdown** (battle simulator) | github.com/smogon/pokemon-showdown | `c0845f33d381402dfcb18ce324c20a94f18af075` | MIT, © 2011–2026 Guangcong Luo and contributors (`b2002a9f…3ed0`) | A private local server for self-play; `TeamValidator` (`tools/validate_teams.js`); `tools/sft/dex.json` is generated from its data by `tools/sft/mkdex.js` | No (local checkout); `dex.json` is a generated subset of its data files |
| **PokéChamp** (`AbyssalPlayer` heuristic baseline) | github.com/sethkarten/pokechamp | `0f84c460319ebe733f8c3028e58a2a5452c60d85` | MIT, © 2025 Seth Karten (`8c7dde40…37fb`) | Opponent bot in the arena (`tools/arena/abyssal_bot.py` runs it unmodified from a local checkout) | No |
| **poke-env** | github.com/hsahovic/poke-env | `7ff6212e4e738efd46f1ea52035b37a8bba1e386` | MIT, © 2019 hsahovic (`77f2c617…4f3b`) | Through PokéChamp, which bundles a modified `poke_env` package | No |
| **llama.cpp** (inference server, LoRA → GGUF conversion) | github.com/ggml-org/llama.cpp | `f8def7fe168bab245fbf15d3f18b26dbb1ef73c8` | MIT, © 2023–2026 the ggml authors (`94f29bbe…010d`); bundles nlohmann/json (MIT, © 2013–2025 Niels Lohmann) | Optional local Glimmer inference and LoRA-to-GGUF conversion. The app defaults to labeled template explanations; Glimmer is not required for every review. | No (built from source) |
| **Axolotl** (fine-tuning framework) | github.com/axolotl-ai-cloud/axolotl | version installed in the training container image | Apache-2.0 (*not verified locally*) | QLoRA fine-tuning of Glimmer (`azure/sft/run_sft.py`) | No |
| PyTorch | pytorch.org | 2.8.0 | BSD-3-Clause (package metadata) | Student, belief model, training | No |
| NumPy | numpy.org | 2.0.2 / 2.4.2 | BSD-3-Clause (package metadata) | Everywhere | No |
| FastAPI, Uvicorn | fastapi.tiangolo.com, uvicorn.org | historical local Mac app: FastAPI 0.128.8, Uvicorn 0.39.0; prospective Linux hosting lock: FastAPI 0.143.0, Uvicorn 0.54.0 (`deploy/runtime.lock`) | MIT; BSD-3-Clause (package metadata) | Web app server | No |
| **Modal Python SDK** | `github.com/modal-labs/modal-client` (wheel Project-URL metadata) | 1.2.4; isolated local macOS CPython 3.13 deployment client (`client-macos-cp313.hashes.lock:22`); wheel SHA-256 `cf4f01081bd9e5e1ec844d87a2c6a5805fd7c8f4deff5671c20d3b1505899aa8` | Apache-2.0 (local wheel METADATA; embedded LICENSE SHA-256 `a6cba85bc92e0cff7a450b1d873c0eaa2e9fc96bf472df0247a26bec77bf3ff9`) | Deployment client and Volume lifecycle in `deploy/public_launcher.py`, `deploy/modal_app.py` and `deploy/volume_lifecycle.py` | Source/lock only; SDK wheel is a separately acquired dependency, not bundled in the repo. |
| **httpx** | `github.com/encode/httpx` (wheel Project-URL metadata) | 0.28.1; prospective Linux hosting runtime (`deploy/runtime.lock:12`); wheel SHA-256 `d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad` | BSD-3-Clause (local wheel METADATA; embedded LICENSE.md SHA-256 `4ec59d544f12b5f539a3a716fd321ac58ccd8030b465221f2c880200cdf28d8d`) | Local/hosted smoke HTTP client in `deploy/smoke_harness.py` | Source/lock only; package wheel is not bundled in the repo. |
| **Starlette** | `github.com/Kludex/starlette` (wheel Project-URL metadata) | 1.7.0; prospective Linux hosting runtime (`deploy/runtime.lock:23`); wheel SHA-256 `67f8e99895493dd2911a03f11314af6ceebeae4e704bb9f43dfc6a9db151c93e` | BSD-3-Clause (local wheel License-Expression; embedded LICENSE.md SHA-256 `dcb95677a02240243187e964f941847d19b17821cf99e5afae684fab328c19bf`) | FastAPI dependency and direct `run_in_threadpool` import in `deploy/cpu_template_service.py` | Source/lock only; package wheel is not bundled in the repo. |
| PyArrow, huggingface_hub | arrow.apache.org, huggingface.co | 21.0.0, 0.36.2 | Apache-2.0 (package metadata) | Data processing; model downloads | No |

The 0.128.8/0.39.0 local Mac app versions are historical. The isolated macOS CPython 3.13 deployment client has Modal 1.2.4, FastAPI 0.143.0 and Starlette 1.7.0 installed; it has no httpx or Uvicorn. The separate Linux hosting runtime versions are prospective lockfile pins, and `deploy/runtime.lock` does not pin Modal. These entries document source/dependency attribution, not a completed remote build, runtime smoke test, or deployment.


## Prospective Mac clean-CLI package inputs

The prospective Mac CPython 3.13 setup input set contains 9 wheels and 55 Rust crate archives, including target-specific crates. In the preserved one-shot setup attempt, all 64 package archives were downloaded and hash-verified before the data phase ended on HTTP 403; no package was installed and no build started. These separately acquired inputs are not bundled in this repository. The archive list is recorded in the [frozen package manifest](tools/setup/mac-cp313-arm64.artifacts.json) and [runtime](tools/setup/requirements-runtime-cp313-macos-arm64.candidate.lock) and [build-tool](tools/setup/requirements-build-tools-cp313-macos-arm64.candidate.lock) locks.

The [package inventory](THIRD_PARTY_SETUP.md) reports declared wheel `METADATA` or Cargo.toml licence strings and whether each archive contains a licence/notice filename. It records metadata only; it does not determine licence compatibility, redistribution rights, adoption, or a completed clean setup. The literal `Dual License` declaration is retained as-is. Licence file contents were not reviewed.
## Models

| Model | Source | Licence (file SHA-256) | How HardRead uses it | Shipped? |
|---|---|---|---|---|
| **Muse Glimmer-30B** (Meta open weights), Q4_K_M GGUF | Meta's official release (`meta-models/Muse-Glimmer-30B-GGUF`) | **Apache-2.0** (`cfc7749b…3d30`) + **Muse Glimmer Usage Policy** (`98a14dab…81d3`; among other terms, "not intended for individuals under the age of 18") | Explainer and note parser in the optional local Glimmer path; the app defaults to labeled template explanations. LoRA fine-tunes are used for science experiments. | No: download the weights from Meta's release. Our LoRA adapters are not published yet. |

Because of Meta's usage policy, HardRead is for users aged 18 and over.

## Data

| Data | Source | Licence / terms | How HardRead uses it | Redistributed? |
|---|---|---|---|---|
| Smogon usage statistics (Gen 9 OU, 2026-09 "chaos") and Foul Play's set caches | smogon.com/stats, data.foulplay.cc, play.pokemonshowdown.com/data/sets (fetched by Foul Play) | No licence stated (*not verified*) | Foul Play's set prediction; pinned by SHA-256 in `DATA_SNAPSHOT.json` | No: hashes only |
| Metamon team sets (`jakegrigsby/metamon-teams`, v5, Gen 9 OU) | Hugging Face | No licence recorded on the dataset (as of our download) | Game configurations for self-play (teams that bots play with) | No (see `DATA.md`) |
| Metamon raw replays (`jakegrigsby/metamon-raw-replays`) | Hugging Face | No licence on the dataset card; its parsed companion dataset is CC BY-NC 4.0 | Internal research only (human belief pre-training experiments). Not used by the product's verdict. | No |
| Public Pokémon Showdown replays | replay.pokemonshowdown.com | Public pages; no re-hosting | Calibration and evaluation of the review on human games, pseudonymised | No: aggregates only |

See `DATA.md` for what is included in the repositories, what is excluded, and why.

## Licence file hashes (SHA-256 of each `LICENSE` at the pinned commit)

```
3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986  foul-play/LICENSE
38ce7f13fdac0ed9fa428649747dacc7e1fc0f4e4842239e4e0c53cefc450b4d  poke-engine/LICENSE
b2002a9fd52ba8db3783a05fbdebfb09c34fd09513b90f6b390a2c0dfbc93ed0  pokemon-showdown/LICENSE
8c7dde403442360bd371d89a8ef7b1a5c9bb2ad08e6f48115c32fa61d0dc37fb  pokechamp/LICENSE
77f2c617cce3d49b9657859f38af0f8538f57ed1f0790094346b2534f6cb4f3b  poke-env/LICENSE
94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d  llama.cpp/LICENSE
cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30  Muse-Glimmer-30B-GGUF/LICENSE
98a14dab9fd97de1666dc8589d399efab1e7fc3ba4e6230d037d6637ba9481d3  Muse-Glimmer-30B-GGUF/USAGE_POLICY.md
```
