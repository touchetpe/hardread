# V2 SFT manifest consumer compatibility — source proposal on HOLD

This private candidate changes consumer-side code only. Its original source file pins are recorded in `baseline.private.json`. The active admission build, source data and model have not been accessed by these tests. A source reviewer must accept the patch and root must separately obtain the builder's output and independent admission review before any real derivation, proof approval, upload or smoke.

`run_sft.py` recognizes exactly the legacy schema, the full v2 schema and the distinct derived smoke schema. V2 requires a reviewed proof binding with the current/parent manifest and parent train hashes, source index/spec, tokenizer/template, serializer bytes and literal version, frozen val/eval, overlap receipt and independent admission review. The runtime checks the actual mounted data files and unique ordered training IDs and task counts. Default production frozen val/eval pins come from the source builder's pinned accepted inputs. Synthetic tests inject separate synthetic pins only through the pure function; no CLI override exists.

`derive_v2_smoke.py` checks the full parent and reads its first 2,000 rows in parent order, writes a distinct smoke manifest, its own exact counts and hashes, a full-parent lineage and ordered-ID digest, plus a `pending_review` receipt and proof candidate in a new private sibling directory. The source `train_games` and `train_rosters` counts are explicitly labeled as parent counts rather than attributed to the smoke subset. Root must freeze and independently review the 2,000 IDs/order digest and actual proof/data hashes before any model call. The proof template is intentionally invalid; for a full parent, `manifest_sha256` equals `parent_full_manifest_sha256`; a smoke proof additionally requires `smoke_ordered_ids_sha256` and is generated as a candidate by the derivation script.

`submit_sft.sh` retains legacy and DATA_URI behavior. With `V2_PROOF_FILE`, it validates the local full/smoke readback before staging, checks the same proof, code and data pins during staging, saves the readback summary, stages the proof into the code snapshot and puts its SHA into the generated command. The existing per-node four-file `verify_sft_data.sh` remains byte-for-byte unchanged. The reviewed proof and generated YAML/code-manifest must be pinned before submission; the proof's claimed review hashes do not themselves prove that a reviewer accepted them.

The following are **inert placeholders**, not executed commands or authorization:

```sh
# After independent source/admission review and root approval only:
python3 azure/sft/derive_v2_smoke.py --parent <accepted-full-data-dir> \
  --parent-proof <reviewed-parent-proof> --proof-sha256 <reviewed-parent-proof-sha256> \
  --serializer tools/sft/serialize.py --out <new-private-smoke-dir> --execute

# After the smoke bytes, proof and ordered-ID digest are independently reviewed:
V2_PROOF_FILE=<reviewed-smoke-proof> DATA_URI=<reviewed-read-only-folder-uri> \
  DRY_RUN=1 bash azure/sft/submit_sft.sh <unique-job-name> <reviewed-smoke-dir> \
  <reviewed-timeout> <reviewed-description> <reviewed-runner-arguments>
```

A dry-run staging is not a job and does not authorize one. Actual capacity, timing, scope, source pins, four mounted-file hashes, output, watcher and creation are root-owned gates.
