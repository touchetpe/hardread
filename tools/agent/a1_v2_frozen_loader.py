"""Exact frozen C9 compatibility for A1 v2; v1 validation is unchanged."""
import hashlib,json
from pathlib import Path
import build_eval_manifest as M
from build_eval_manifest import (ROOT,ManifestError,SourceUnresolvedError,FROZEN_C9_V1_MANIFEST_DIGEST,FROZEN_C9_V1_REVIEW_CODE_COMMIT,FROZEN_C9_V1_M_PATH,FROZEN_C9_V1_M_SHA256,REVIEW_SOURCE_PATHS,ROWS_PER_POOL,POOLS,APP_POLICY_VERSION,CRAWL_PINS,DRAFT_CACHE_PATH,DRAFT_CACHE_SHA,EXPECTED_CRAWL_SOURCES,calib_b,canonical_json,digest_json,digest_bytes,manifest_digest,rank_hash,keys_for_offered,git_repo_head,_read_json,_read_source_jsonl,_resolve_path,_verify_file,_require_farm_identity,_source_identity,_build_offered,_census_by_identity,_selected_attempt_digest,_validate_candidate_census,_validate_row_eligibility,_validate_selected_context)
OLD_PIPELINE='147b6d20ecc9e5f1ef157b6dba6fad22b675cee2bf23fc8d63bf9523b7d2c620'
CURRENT_PIPELINE='550f15f96211a808e0fc761b7c68e7240a168540d221c18ce1f2ae8d97ec1898'
ITEM_PATH='scratch_ds/evals/c9-freeze-fb10153-v1/a1_items.json'
ITEM_SHA='a021d472b42368a3286f4a1e60ae38b0c261d0644ea7c1918bb19459035f6a88'
MANIFEST_PATH='scratch_ds/evals/c9-freeze-fb10153-v1/selection_manifest.json'

def _verify_historical_binding_v2(manifest, source_hashes, *, root):
    if manifest_digest(manifest)!=FROZEN_C9_V1_MANIFEST_DIGEST or manifest.get('review_code_commit')!=FROZEN_C9_V1_REVIEW_CODE_COMMIT or source_hashes!=manifest.get('review_source_sha256') or source_hashes.get(FROZEN_C9_V1_M_PATH)!=FROZEN_C9_V1_M_SHA256 or source_hashes.get('app/pipeline.py')!=OLD_PIPELINE:
        raise ManifestError('exact frozen historical source binding required')
    M._require_ancestor_commit(FROZEN_C9_V1_REVIEW_CODE_COMMIT,root=root)
    if M._git_blob_sha256(FROZEN_C9_V1_REVIEW_CODE_COMMIT,'app/pipeline.py',root=root)!=OLD_PIPELINE:raise ManifestError('historical review pipeline differs')
    M._verify_historical_constructor_sources(source_hashes,root=root)
    M._verify_current_file_matches_head(FROZEN_C9_V1_M_PATH,root=root)
    for path in REVIEW_SOURCE_PATHS:
        if path not in (FROZEN_C9_V1_M_PATH,'app/pipeline.py'):_verify_file(path,source_hashes[path],root)
    if M.digest_file(Path(root)/'app/pipeline.py')!=CURRENT_PIPELINE:raise ManifestError('current accepted pipeline differs')

def _frozen_witness_v2(candidate,attempt,review_code_commit,source_hashes,*,root):
    path=_resolve_path(attempt.get('witness_path'),root)
    if not path.is_relative_to((Path(root)/'scratch_ds/evals').resolve()):raise ManifestError('turn witness must be private')
    _verify_file(attempt['witness_path'],attempt.get('witness_file_sha256'),root)
    witness=_read_json(attempt['witness_path'],root)
    _verify_file(candidate.get('source_path'),candidate.get('source_sha256'),root)
    matches=[(record,h) for i,record,h in _read_source_jsonl(candidate['source_path'],root=root) if i==candidate['source_record_index']]
    if len(matches)!=1:raise ManifestError('frozen witness source record missing')
    record,h=matches[0];turns=candidate['turns']
    if h!=candidate['source_record_sha256'] or M._raw_game_uid(record)!=candidate['uid'] or M._raw_turn_count(record)!=turns or type(turns) is not int or turns<=100:
        raise ManifestError('frozen witness source differs')
    expected={'schema':'c9-product-max-turns-witness-v1','exception_type':'ProductMaxTurns','pool':candidate['pool'],'rank_hash':rank_hash(candidate['uid'],candidate['side']),'candidate_sha256':digest_json(_source_identity(candidate)),'source_path':candidate['source_path'],'source_sha256':candidate['source_sha256'],'source_record_index':candidate['source_record_index'],'source_record_sha256':h,'raw_turn_count':turns,'parsed_turn_count':turns,'product_max_turns':100,'app_pipeline_sha256':OLD_PIPELINE,'review_code_commit':review_code_commit,'observed_error_class':'InputError'}
    if source_hashes.get('app/pipeline.py')!=OLD_PIPELINE or canonical_json(witness)!=canonical_json(expected):raise ManifestError('frozen witness differs')
    return witness

def _validate_attempt_evidence_v2(pool, evidence, census, rows, manifest, *, root):
    if not isinstance(evidence, dict) or not isinstance(evidence.get("attempts"), list):
        raise ManifestError(f"{pool} selection attempt log is missing")
    attempts = evidence["attempts"]
    if evidence.get("attempts_sha256") != digest_json(attempts):
        raise ManifestError(f"{pool} selection attempt log hash changed")
    if evidence.get("review_code_commit") != manifest.get("review_code_commit"):
        raise ManifestError(f"{pool} attempt log review commit differs from the manifest")
    source_hashes = evidence.get("review_source_sha256")
    if (not isinstance(source_hashes, dict) or not source_hashes
            or any(not isinstance(value, str) or len(value) != 64 for value in source_hashes.values())):
        raise ManifestError(f"{pool} attempt log lacks review source hashes")
    if set(source_hashes) != set(REVIEW_SOURCE_PATHS):
        raise ManifestError(f"{pool} attempt log review source list is incomplete")
    if source_hashes != manifest.get("review_source_sha256"):
        raise ManifestError(f"{pool} attempt log review source hashes differ from the manifest")
    if manifest_digest(manifest) == FROZEN_C9_V1_MANIFEST_DIGEST:
        _verify_historical_binding_v2(manifest, source_hashes, root=root)
    else:
        for path, expected_hash in source_hashes.items():
            _verify_file(path, expected_hash, root)
    eligible = [entry for entry in census if entry.get("exclusion_reason") is None]
    eligible.sort(key=lambda row: (row["rank_hash"], row["uid"], row["side"]))
    rows_by_identity = {(row["pool"], row["uid"], row["side"]): row for row in rows}
    selected_uids, selected_identities = set(), []
    verified_review_exceptions = 0
    cursor = 0
    for attempt in attempts:
        if len(selected_identities) >= ROWS_PER_POOL:
            raise ManifestError(f"{pool} attempt log continues after its tenth successful unique game")
        if cursor >= len(eligible):
            raise ManifestError(f"{pool} attempt log exceeds the eligible source census")
        candidate = eligible[cursor]
        cursor += 1
        expected = {"pool": pool, "uid": candidate["uid"], "side": candidate["side"],
                    "rank_hash": candidate["rank_hash"],
                    "candidate_sha256": digest_json(_source_identity(candidate)),
                    "review_code_commit": evidence.get("review_code_commit"),
                    "review_source_sha256": source_hashes}
        if any(attempt.get(key) != value for key, value in expected.items()):
            raise ManifestError(f"{pool} attempt order or candidate source binding changed")
        identity = (pool, candidate["uid"], candidate["side"])
        outcome = attempt.get("outcome")
        if candidate["uid"] in selected_uids:
            if outcome != "duplicate_game" or "selected_row_sha256" in attempt or "exception_type" in attempt:
                raise ManifestError(f"{pool} duplicate game attempt was not skipped exactly")
            continue
        if outcome == "review_exception":
            if identity in rows_by_identity or "selected_row_sha256" in attempt:
                raise ManifestError(f"{pool} review exception attempt is malformed or selected")
            if attempt.get("exception_type") != "ProductMaxTurns":
                raise ManifestError(f"{pool} candidate exception provenance is pending; recorded selection is not approved")
            witness = _frozen_witness_v2(candidate, attempt, evidence.get("review_code_commit"), source_hashes, root=root)
            expected_fields = {key: witness[key] for key in ("raw_turn_count", "parsed_turn_count", "product_max_turns",
                                                               "source_record_index", "source_record_sha256")}
            expected_fields["witness_sha256"] = digest_json(witness)
            if any(canonical_json(attempt.get(key)) != canonical_json(value)
                   for key, value in expected_fields.items()):
                raise ManifestError(f"{pool} turn-limit exception evidence differs from source proof")
            path = _resolve_path(attempt.get("witness_path"), root)
            if not path.is_relative_to((Path(root) / "scratch_ds" / "evals").resolve()):
                raise ManifestError("turn-limit witness must be private")
            _verify_file(attempt["witness_path"], attempt.get("witness_file_sha256"), root)
            if canonical_json(_read_json(attempt["witness_path"], root)) != canonical_json(witness):
                raise ManifestError(f"{pool} turn-limit witness differs from source proof")
            verified_review_exceptions += 1
            continue
        if outcome != "selected":
            raise ManifestError(f"{pool} attempt outcome is not selected, exception, or duplicate")
        row = rows_by_identity.get(identity)
        if (row is None or attempt.get("selected_row_sha256") != row.get("selection_sha256")
                or not isinstance(row.get("selection_sha256"), str)
                or row.get("source_record_index") != candidate.get("source_record_index")
                or row.get("source_record_sha256") != candidate.get("source_record_sha256")
                or row.get("eligibility") != {"decided": True, "result": candidate["result"],
                                               "turns": candidate["turns"],
                                               "half": calib_b.half(candidate["uid"]) if pool == "crawl" else None,
                                               "both_seats_eligible": pool == "hr_val"}):
            raise ManifestError(f"{pool} selected attempt does not bind to the derived census row")
        selected_uids.add(candidate["uid"])
        selected_identities.append(identity)
    if len(selected_identities) != ROWS_PER_POOL or cursor != len(attempts):
        raise ManifestError(f"{pool} attempt log does not prove the first ten successful unique games")
    if selected_identities != [(row["pool"], row["uid"], row["side"]) for row in rows]:
        raise ManifestError(f"{pool} selected rows differ from the attempt log's first ten successes")
    if type(evidence.get("review_exceptions")) is not int or evidence["review_exceptions"] != verified_review_exceptions:
        raise ManifestError(f"{pool} exception count differs from attempt evidence")

def validate_manifest_v2(manifest, *, root=ROOT, app_offered_fn=None, require_evals_paths=False):
    """Recheck hashes, deterministic ordering, fixed sample and shipped app offered parity."""
    if not isinstance(manifest, dict) or manifest.get("schema") != "c9-selection-manifest-v1":
        raise ManifestError("selection manifest schema is missing or unsupported")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ManifestError("manifest source hash list is missing")
    source_by_path = {}
    for src in sources:
        if not isinstance(src, dict) or src.get("path") in source_by_path:
            raise ManifestError("manifest source list is malformed or duplicated")
        _verify_file(src.get("path"), src.get("sha256"), root)
        source_by_path[src["path"]] = src
    crawl_roles = {s.get("role") for s in sources if s.get("pool") == "crawl"}
    if crawl_roles != set(EXPECTED_CRAWL_SOURCES):
        raise ManifestError("manifest does not bind all three Draft-B source files")
    for role, expected_path in EXPECTED_CRAWL_SOURCES.items():
        if not any(s.get("pool") == "crawl" and s.get("role") == role and s.get("path") == expected_path and s.get("sha256") == CRAWL_PINS[role]
                   for s in sources):
            raise ManifestError("manifest Draft-B source paths are incomplete")
    farm_sources = [s for s in sources if s.get("pool") == "hr_val"]
    if len(farm_sources) < 2 or {s.get("role") for s in farm_sources} < {"games", "decisions"}:
        raise ManifestError("manifest lacks verified hr_val game and decision sources")
    farm_identity = manifest.get("farm_source_identity")
    _require_farm_identity(farm_identity, root=root)
    for role in ("games", "decisions"):
        ref = farm_identity[role]
        if not any(src.get("pool") == "hr_val" and src.get("role") == role
                   and src.get("path") == ref["path"] and src.get("sha256") == ref["sha256"]
                   for src in sources):
            raise SourceUnresolvedError("derived source reference differs from manifest")
    from c9_farm_source import checked
    if manifest.get("fork_commit") != git_repo_head(Path(root) / "foul-play"):
        raise ManifestError("Foul Play source commit changed")
    calibration_hash = "d4a8b2580ca101bc6f55a8900cb716db59d04d78e0636c6843ed135d6b5642a9"
    calibration_path = "tools/review/calibrator.json"
    checked(root, calibration_path, calibration_hash)
    if not isinstance(_read_json(calibration_path, root).get("absolute_human"), dict):
        raise ManifestError("active app calibrator is absent")
    if manifest.get("active_calibrator") != f"{calibration_path}#absolute_human@{calibration_hash}":
        raise ManifestError("active app calibrator identity changed")
    census_by_pool = _validate_candidate_census(manifest, sources, root=root)
    rows = manifest.get("rows")
    if not isinstance(rows, list) or len(rows) != 20:
        raise ManifestError("manifest must contain exactly 20 reviews")
    if not all(isinstance(r, dict) for r in rows):
        raise ManifestError("manifest selection rows are malformed")
    if [r.get("pool") for r in rows] != ["crawl"] * 10 + ["hr_val"] * 10:
        raise ManifestError("manifest order must be ten crawl reviews followed by ten hr_val reviews")
    per_pool = {p: [] for p in POOLS}
    _verify_file(DRAFT_CACHE_PATH, DRAFT_CACHE_SHA, root)
    from collections import defaultdict
    draft_rows = defaultdict(list)
    for _, record, _ in _read_source_jsonl(EXPECTED_CRAWL_SOURCES["sets"], root=root):
        draft_rows[(record.get("uid"), record.get("player"))].append(record)
    draft_profiles = _read_json(EXPECTED_CRAWL_SOURCES["species_profiles"], root)
    source_records = {}
    for pool in POOLS:
        source = next(src for src in sources if src.get("pool") == pool and src.get("role") == "games")
        source_records[pool] = {i: r for i, r, _ in _read_source_jsonl(source["path"], root=root)}
    seen = set()
    app_offered_fn = app_offered_fn or _build_offered
    for row in rows:
        if not isinstance(row, dict) or row.get("pool") not in POOLS:
            raise ManifestError("manifest selection row has an unknown pool")
        pool = row["pool"]
        uid, side = row.get("uid"), row.get("side")
        if not isinstance(uid, str) or not uid or side not in ("p1", "p2"):
            raise ManifestError("manifest row identity is malformed")
        pair = (pool, uid)
        if pair in seen:
            raise ManifestError("manifest selects more than one side of a game")
        seen.add(pair)
        if row.get("rank_hash") != rank_hash(uid, side):
            raise ManifestError("manifest rank hash does not match uid/side")
        if row.get("source_path") not in source_by_path:
            raise ManifestError("row source is not in the complete source hash list")
        src = source_by_path[row["source_path"]]
        if src.get("pool") != pool or src.get("sha256") != row.get("source_sha256"):
            raise ManifestError("row source identity/hash does not match manifest source list")
        _validate_row_eligibility(row)
        derived = _census_by_identity(census_by_pool[pool]).get((pool, uid, side))
        if (derived is None or derived.get("exclusion_reason") is not None
                or row.get("source_record_index") != derived.get("source_record_index")
                or row.get("source_record_sha256") != derived.get("source_record_sha256")
                or row.get("eligibility") != {"decided": True, "result": derived["result"],
                                               "turns": derived["turns"],
                                               "half": calib_b.half(uid) if pool == "crawl" else None,
                                               "both_seats_eligible": pool == "hr_val"}):
            raise ManifestError("selected row eligibility/source pointer differs from the raw candidate census")
        if row.get("review_code_commit") != manifest.get("review_code_commit"):
            raise ManifestError("review code commit differs from the frozen manifest")
        if row.get("app_policy_version") != APP_POLICY_VERSION:
            raise ManifestError("app explanation policy is not offered-scope-v2")
        if row.get("active_calibrator") != manifest.get("active_calibrator"):
            raise ManifestError("active calibrator differs from the frozen manifest")
        for key in ("replay", "review", "ctx"):
            _verify_file(row.get(f"{key}_path"), row.get(f"{key}_sha256"), root)
        review = _read_json(row["review_path"], root)
        ctx = _read_json(row["ctx_path"], root)
        _validate_selected_context(row, derived, source_records[pool][row["source_record_index"]],
                                   pool, uid, side, ctx, root, draft_rows, draft_profiles)
        offered = _read_json(row["offered_path"], root)
        if not isinstance(offered, list):
            raise ManifestError("frozen offered value must be a list, including []")
        if digest_bytes(canonical_json(offered).encode("utf-8")) != row.get("offered_sha256"):
            raise ManifestError("frozen offered hash changed")
        if row.get("selection_sha256") != _selected_attempt_digest(derived, review, ctx, offered):
            raise ManifestError("selected review/context/offered bytes differ from the attempt hash")
        keys = keys_for_offered(offered)
        if keys != row.get("ordered_offered_keys"):
            raise ManifestError("ordered offered keys changed")
        decision_keys = {(d.get("turn"), d.get("k", 0)) for d in review.get("decisions", [])
                         if isinstance(d, dict)}
        if any((k["turn"], k["k"]) not in decision_keys for k in keys):
            raise ManifestError("offered key is not present in the review")
        recomputed = app_offered_fn(review, ctx)
        if canonical_json(recomputed) != canonical_json(offered):
            raise ManifestError("frozen offered list differs from shipped app context")
        if require_evals_paths:
            for key in ("review_path", "ctx_path", "offered_path", "replay_path", "team_path"):
                try:
                    _resolve_path(row[key], root).relative_to((Path(root) / "scratch_ds" / "evals").resolve())
                except ValueError as exc:
                    raise ManifestError("frozen review/context/offered files must be private eval artifacts") from exc
        per_pool[pool].append(row)
    for pool, pool_rows in per_pool.items():
        if len(pool_rows) != ROWS_PER_POOL:
            raise ManifestError(f"manifest must contain ten {pool} reviews")
        if [r["rank_hash"] for r in pool_rows] != sorted(r["rank_hash"] for r in pool_rows):
            raise ManifestError(f"{pool} reviews are not in deterministic rank order")
        selection_evidence = manifest.get("selection_evidence")
        if not isinstance(selection_evidence, dict):
            raise ManifestError("manifest selection attempt evidence is missing")
        _validate_attempt_evidence_v2(pool, selection_evidence.get(pool), census_by_pool[pool], pool_rows, manifest,
                                   root=root)
    return per_pool


def _check_offered_pins(rows,pins,*,root):
    if not isinstance(rows,list) or len(rows)!=20 or not isinstance(pins,dict) or len(pins)!=20 or set(pins)!={r.get('offered_path') for r in rows}:
        raise ManifestError('exact offered byte pins required')
    for path,sha in pins.items():_verify_file(path,sha,root)

def load_registered_a1_v2(config, *, root=ROOT):
    """Validate only the previously frozen A1 items and materialize scoped inputs."""
    root=Path(root).resolve()
    for rel in (ITEM_PATH,MANIFEST_PATH):
        if any((root/Path(*Path(rel).parts[:i])).is_symlink() for i in range(1,len(Path(rel).parts)+1)):raise ManifestError('frozen path contains symlink')
    _verify_file(ITEM_PATH,ITEM_SHA,root)
    document=_read_json(ITEM_PATH,root)
    if document.get('manifest_path')!=MANIFEST_PATH or document.get('manifest_sha256')!=FROZEN_C9_V1_MANIFEST_DIGEST:raise ManifestError('frozen manifest identity differs')
    manifest=_read_json(MANIFEST_PATH,root)
    if manifest_digest(manifest)!=FROZEN_C9_V1_MANIFEST_DIGEST:raise ManifestError('frozen manifest digest differs')
    expected,_=M._manifest_items(MANIFEST_PATH,manifest)
    if canonical_json(document)!=canonical_json(expected) or len(document.get('items',[]))!=60:raise ManifestError('frozen A1 item document differs')
    if (config.get('manifest_sha256')!=FROZEN_C9_V1_MANIFEST_DIGEST or config.get('items_sha256')!=ITEM_SHA or (config.get('source_sha256') or {}).get('app/pipeline.py')!=CURRENT_PIPELINE):raise ManifestError('current config source tuple differs')
    from app import pipeline,server
    if Path(pipeline.__file__).resolve()!=(root/'app/pipeline.py').resolve() or M.digest_file(pipeline.__file__)!=CURRENT_PIPELINE or pipeline.MAX_TURNS!=100:raise ManifestError('actual current app pipeline differs')
    rows=manifest.get('rows') or []
    rawpins=config.get('offered_file_sha256')
    _check_offered_pins(rows,rawpins,root=root)
    validate_manifest_v2(manifest,root=root,app_offered_fn=lambda review,ctx:pipeline.build_view_with_offered(review,ctx)[1],require_evals_paths=True)
    prepared=[]
    for item in document['items']:
        row=rows[item['row_index']];review=_read_json(item['review'],root);ctx=_read_json(item['ctx'],root);offered=_read_json(item['offered'],root)
        verdicts={}
        if row.get('verdicts_path'):
            _verify_file(row['verdicts_path'],row.get('verdicts_sha256'),root);verdicts=_read_json(row['verdicts_path'],root)
            if not isinstance(verdicts,dict):raise ManifestError('cached verdicts malformed')
        visible=server._visible_verdicts(offered,verdicts)
        allowed={str(k['turn'])+':'+str(k['k']) for k in keys_for_offered(offered)}
        if not isinstance(visible,dict) or not set(visible).issubset(allowed):raise ManifestError('cached verdict escaped offered scope')
        prepared.append({**item,'review_data':review,'ctx_data':ctx,'offered_data':offered,'visible_verdicts':visible})
    return manifest,FROZEN_C9_V1_MANIFEST_DIGEST,prepared
