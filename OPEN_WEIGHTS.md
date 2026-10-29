# Open-weight evidence (status as of 2026-10-29)

## Replay-review model

HardRead’s accepted source integration uses base Muse Glimmer-30B for read-only engine-evidence selection and stated-read parsing into editable fields. Application code fills displayed numeric slots from cited tool fields so the supporting receipts can be inspected. This supports numeric provenance, not correctness. Current explanations use a labeled template; the complete C10 stated-read/verdict flow and A1 live-prose ship checks remain pending. The A1 v2 recorder returned 59/60 answers after at most one repair. Separately, a blinded review flagged 3/60 sentences in a seeded sample of 60 from a 120-sentence pool. Fifteen owner labels and a bound-score review remain pending; neither check is a live-prose PASS. [D1 engineering-flow record](RESULTS.md#d1-template-ui-20261028) [A1 v2 record](RESULTS.md#src-a1-v2-owner-pending)

At the Oct 27 and Oct 28 capture times, local `llama.cpp` process and model/file provenance were recorded. Those snapshots bind identity at capture time; they do not show current serving, uptime, or resident-memory state.

## Published-file identity

The recorded local disk file matched Meta’s published `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` at pinned Hugging Face revision `b44f1072cf75e918a1b82a3a17b45c4389b076e7`. The file is 16,756,683,904 bytes with SHA-256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e`. The local license and usage-policy bytes also matched the official tree’s blob identities. [Pinned published file](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/blob/b44f1072cf75e918a1b82a3a17b45c4389b076e7/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf) [Captured local provenance record](ops/provenance/glimmer-official-gguf.json)

## Separate gameplay study

The QLoRA XL6 adapter is game-playing research; it does not power the replay explainer. After the original R1 selection cutoff was missed, a later committed F1 addendum selected XL6 under its engine-target validation rule: 1,379/2,428 action rows, or 56.7957% top-1 agreement. Training stopped at 5,668/5,959 planned steps, a budget-stopped partial epoch. A later first-epoch Glimmer v2 candidate scored 1,386/2,428 but missed the prespecified 1,416 threshold, so XL6 remains selected. The second-epoch candidate is still pending. These are correlated offline engine-target metrics, not battle win percentages. [Validation and selection record](RESULTS.md)

In separate arena play against the Abyssal heuristic bot on partly seen development teams, selected XL6 won 161/200 scheduled games (80.5%; 199 completed, with one timeout counted as a non-win; Wilson 95% CI 74.46–85.39%). Pooled G1 won 333/400 scheduled games (83.25%; 399 completed, with one timeout counted as a non-win; Wilson 95% CI 79.28–86.59%). These are distinct setups, not held-out or human-ladder strength, matched improvement, or replay-explainer quality. [Clean and pooled G1 results](RESULTS.md#xl6-clean-n1-pooled-g1-20261029)

This page documents recorded local model identity, accepted source integration, and a separate bounded gameplay study; infrastructure-partner verification, prize eligibility, a current live trace, and any public product link are not established.


**Fan-made, not affiliated with Nintendo, The Pokémon Company or Game Freak. 18+.**
