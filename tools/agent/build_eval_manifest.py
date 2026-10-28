#!/usr/bin/env python3
"""Pure helpers for freezing the fixed E11 A1/A0 selection before a recorded call.

This module deliberately does not discover or bind private datasets.  Its selection
functions operate on caller-supplied rows; farm rows require a separately verified
``hr_val`` identity object.  Dataset parsing and review generation remain gated on C8.
"""
import hashlib
import json
import gzip
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT / "tools" / "review") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools" / "review"))
import calib_b  # noqa: E402

QUESTIONS = {
    1: "Was this game decided by luck or by my choices?",
    2: "What was the biggest piece of luck in this game, and how much did it matter?",
    3: "Where did the engine disagree most with my choices, and by how much?",
}
POOLS = ("crawl", "hr_val")
ROWS_PER_POOL = 10
A0_ROWS_PER_POOL = 5
APP_POLICY_VERSION = "offered-scope-v2"
EXPECTED_CRAWL_SOURCES = {
    "games": "scratch_ds/human_broad/games.jsonl.gz",
    "sets": "scratch_ds/human_broad/sets.jsonl.gz",
    "species_profiles": "scratch_ds/human_broad/species_profiles.json",
}
CRAWL_PINS = {"games": "338d1255fd4055fff86cf7ef6275a0feba856b7113d28f117f3953ce893a8750",
              "sets": "e2ab9a263c38cdf88dddaebde98ac60a278012895a467bd9c115c2d2a047fa1e",
              "species_profiles": "0584a121307abe129dc72b803f0d7771366414b21cf56f6aa7ca98f7dd55a878"}
DRAFT_CACHE_PATH = "foul-play/fp/data/pkmn_sets_cache/gen9ou/pokemon_full_sets.json"
DRAFT_CACHE_SHA = "e6129b38f2a605a5464d014a817ca3083986eae87617033a4f82266565b51772"
REVIEW_SOURCE_PATHS = ("tools/review/review.py", "tools/review/reconstruct.py",
                       "tools/review/calib_b.py", "tools/review/human_eval.py", "tools/review/luck.py",
                       "tools/review/calibrate.py", "tools/review/select_offered.py", "tools/review/bridge.py",
                       "tools/human/reconstruct_check.py", "tools/human/parse_replays.py",
                       "tools/dataset/extract.py", "tools/agent/build_eval_manifest.py", "tools/agent/c9_farm_source.py",
                       "tools/agent/construct_eval_c9.py", "app/pipeline.py", "app/bridge_adapter.py")


class ManifestError(ValueError):
    """A frozen manifest or its referenced input is incomplete or changed."""


class SourceUnresolvedError(ManifestError):
    """The exact registered source pool cannot be proven from the supplied identity."""


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest_json(value):
    return digest_bytes(canonical_json(value).encode("utf-8"))


def digest_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rank_hash(uid, side):
    if not isinstance(uid, str) or not uid or side not in ("p1", "p2"):
        raise ManifestError("candidate needs a nonempty uid and side p1/p2")
    return digest_bytes(f"a1/{uid}/{side}".encode("utf-8"))


def keys_for_offered(offered):
    if not isinstance(offered, list):
        raise ManifestError("offered decisions must be a list, including an explicit []")
    keys = []
    seen = set()
    for decision in offered:
        if not isinstance(decision, dict) or type(decision.get("turn")) is not int:
            raise ManifestError("offered decision has no integer turn")
        k = decision.get("k", 0)
        if type(k) is not int:
            raise ManifestError("offered decision k must be an integer")
        key = (decision["turn"], k)
        if key in seen:
            raise ManifestError("offered decision keys must be unique")
        seen.add(key)
        keys.append({"turn": key[0], "k": key[1]})
    return keys


def _require_farm_identity(identity, *, root=ROOT):
    """Rerun the pinned raw-source adapter and compare every derived byte."""
    if not isinstance(identity, dict) or identity.get("pool") != "hr_val":
        raise SourceUnresolvedError("source unresolved: exact hr_val identity is not proven")
    import c9_farm_source
    try:
        data = c9_farm_source.output_bytes(root)
        for key, expected in zip(("games", "decisions", "source_map"), data):
            ref = identity.get(key)
            if not isinstance(ref, dict) or not ref.get("path"):
                raise SourceUnresolvedError("derived source reference missing")
            source_path = _resolve_path(ref["path"], root)
            if not source_path.is_relative_to((Path(root) / "scratch_ds" / "evals").resolve()):
                raise SourceUnresolvedError("derived source must stay in private eval storage")
            actual = source_path.read_bytes()
            if actual != expected or ref.get("sha256") != digest_bytes(expected):
                raise SourceUnresolvedError("derived source differs from pinned raw source")
    except (OSError, ValueError, KeyError) as exc:
        raise SourceUnresolvedError("source unresolved: raw farm proof failed") from exc


def _read_source_jsonl(path, *, root=ROOT):
    """Parse JSONL source bytes while retaining stable decompressed record pointers and hashes."""
    raw = _resolve_path(path, root).read_bytes()
    try:
        decoded = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    except OSError as exc:
        raise ManifestError(f"cannot decompress source JSONL: {Path(path).name}") from exc
    records = []
    for line_index, line in enumerate(decoded.splitlines(keepends=True)):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ManifestError(f"malformed JSONL source record at line {line_index + 1}") from exc
        if not isinstance(record, dict):
            raise ManifestError(f"source record at line {line_index + 1} is not an object")
        records.append((line_index, record, digest_bytes(line)))
    return records


def _raw_game_uid(record):
    uid = record.get("uid")
    if isinstance(uid, str) and uid.strip():
        return uid
    run_id, game = record.get("run_id"), record.get("game")
    if isinstance(run_id, str) and run_id.strip() and type(game) is int and game >= 0:
        return f"{run_id}/g{game:04d}"
    return None


def _raw_winner_side(record):
    player = record.get("winner_player")
    raw_winner = {"A": "p1", "B": "p2", "p1": "p1", "p2": "p2"}.get(record.get("winner"))
    if player in ("p1", "p2"):
        if raw_winner and raw_winner != player:
            raise ManifestError("source winner and winner_player disagree")
        return player
    return raw_winner


def _raw_turn_count(record):
    log = record.get("log")
    if isinstance(log, list):
        lines = log
    elif isinstance(log, str):
        lines = log.splitlines()
    else:
        lines = []
    turns = [int(match.group(1)) for line in lines if isinstance(line, str)
             for match in [re.match(r"\|turn\|(\d+)\s*$", line)] if match]
    return max(turns) if turns else None


def _source_identity(candidate):
    return {key: candidate.get(key) for key in
            ("pool", "uid", "side", "source_path", "source_sha256", "source_record_index",
             "source_record_sha256", "result", "decided", "turns", "half", "both_seats_eligible",
             "team_exports", "decision_coverage")}


def _selected_attempt_digest(candidate, review, ctx, offered):
    return digest_json({"candidate": _source_identity(candidate),
                        "rank_hash": candidate.get("rank_hash"),
                        "review": review, "ctx": ctx, "offered": offered})


def _farm_team_exports(record, *, root, source_by_path):
    # Members are authenticated by the raw-source adapter rerun in validation.
    refs = record.get("team_exports")
    if not isinstance(refs, dict) or set(refs) != {"p1", "p2"}:
        return None
    for ref in refs.values():
        if (not isinstance(ref, dict) or set(ref) != {"member", "sha256"}
                or not isinstance(ref["member"], str)
                or not re.fullmatch(r"gen9/hr_val/[^/]+", ref["member"])
                or not isinstance(ref["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])):
            return None
    return refs


def enumerate_source_candidates(pool, sources, *, root=ROOT):
    """Re-enumerate both sides from exact hashed raw game bytes for manifest validation."""
    if pool not in POOLS:
        raise ManifestError(f"unknown candidate pool: {pool}")
    source_by_path = {source.get("path"): source for source in sources if isinstance(source, dict)}
    game_sources = [source for source in sources if source.get("pool") == pool and source.get("role") == "games"]
    if len(game_sources) != 1:
        if pool == "hr_val":
            raise SourceUnresolvedError("source unresolved: exact hr_val games source is not unique")
        raise ManifestError("candidate census requires one exact games source per pool")
    game_source = game_sources[0]
    _verify_file(game_source.get("path"), game_source.get("sha256"), root)
    decision_sides = {}
    if pool == "hr_val":
        decision_sources = [source for source in sources if source.get("pool") == pool
                            and source.get("role") == "decisions"]
        if len(decision_sources) != 1:
            raise SourceUnresolvedError("source unresolved: exact hr_val decisions source is not unique")
        decision_source = decision_sources[0]
        _verify_file(decision_source.get("path"), decision_source.get("sha256"), root)
        for _idx, decision, _record_hash in _read_source_jsonl(decision_source["path"], root=root):
            uid = _raw_game_uid(decision)
            sides = decision.get("sides")
            if not uid or not isinstance(sides, dict) or any(side not in ("p1", "p2") or type(count) is not int or count <= 0 for side, count in sides.items()):
                raise ManifestError("farm decision coverage malformed")
            if uid in decision_sides:
                raise ManifestError("duplicate farm decision coverage")
            decision_sides[uid] = set(sides)
    census = []
    for line_index, record, record_hash in _read_source_jsonl(game_source["path"], root=root):
        uid = _raw_game_uid(record)
        winner_side = _raw_winner_side(record)
        turns = _raw_turn_count(record)
        team_exports = _farm_team_exports(record, root=root, source_by_path=source_by_path) if pool == "hr_val" else None
        both_eligible = (pool == "hr_val" and team_exports is not None
                         and isinstance(record.get("log"), list) and bool(record["log"]))
        for side in ("p1", "p2"):
            decided = (winner_side in ("p1", "p2") or (pool == "hr_val" and record.get("log") is None and record.get("winner_recorded") in ("A", "B")))
            result = None if winner_side not in ("p1", "p2") else ("win" if winner_side == side else "loss")
            candidate = {"pool": pool, "uid": uid, "side": side, "result": result,
                         "decided": decided, "turns": turns,
                         "source_path": game_source["path"], "source_sha256": game_source["sha256"],
                         "source_record_index": line_index, "source_record_sha256": record_hash}
            if pool == "crawl":
                candidate["half"] = calib_b.half(uid) if uid else None
            else:
                candidate["both_seats_eligible"] = bool(both_eligible)
                candidate["team_exports"] = team_exports
                candidate["decision_coverage"] = sorted(decision_sides.get(uid, set()))
            candidate["rank_hash"] = rank_hash(uid, side) if uid else None
            candidate["exclusion_reason"] = _candidate_exclusion_reason(candidate, pool)
            census.append(candidate)
    return sorted(census, key=lambda row: (row["source_record_index"], row["side"]))


def _candidate_exclusion_reason(candidate, pool):
    if not isinstance(candidate.get("uid"), str) or not candidate["uid"].strip():
        return "invalid_uid"
    if candidate.get("decided") is True and type(candidate.get("turns")) is not int:
        return "turn_count_unavailable"
    if candidate.get("decided") is not True or candidate.get("result") not in ("win", "loss"):
        return "undecided"
    if type(candidate.get("turns")) is not int:
        return "turn_count_unavailable"
    if candidate["turns"] < 10:
        return "short"
    if pool == "crawl" and calib_b.half(candidate["uid"]) != 1:
        return "wrong_half"
    if pool == "hr_val" and candidate.get("both_seats_eligible") is not True:
        return "farm_seats_ineligible"
    return None


def eligibility_counts(candidates, pool):
    """Return deterministic exclusion counts without changing the registered filters."""
    counts = {"seen": 0, "eligible": 0, "wrong_half": 0, "undecided": 0,
              "short": 0, "farm_seats_ineligible": 0, "invalid": 0}
    for c in candidates:
        counts["seen"] += 1
        if not isinstance(c, dict) or c.get("pool") != pool or not c.get("uid") or c.get("side") not in ("p1", "p2"):
            counts["invalid"] += 1
            continue
        reason = _candidate_exclusion_reason(c, pool)
        if reason in ("undecided", "invalid_uid"):
            counts["undecided"] += 1
        elif reason in ("short", "turn_count_unavailable"):
            counts["short"] += 1
        elif reason == "wrong_half":
            counts["wrong_half"] += 1
        elif reason == "farm_seats_ineligible":
            counts["farm_seats_ineligible"] += 1
        elif reason is None:
            counts["eligible"] += 1
        else:
            counts["invalid"] += 1
    return counts


def _candidate_eligibility(candidate, pool):
    if not isinstance(candidate, dict) or candidate.get("pool") != pool:
        return None
    uid, side = candidate.get("uid"), candidate.get("side")
    if not isinstance(uid, str) or not uid or side not in ("p1", "p2"):
        return None
    if _candidate_exclusion_reason(candidate, pool) is not None:
        return None
    half = calib_b.half(uid) if pool == "crawl" else None
    return {"decided": True, "result": candidate["result"], "turns": candidate["turns"],
            "half": half, "both_seats_eligible": candidate.get("both_seats_eligible") is True}


def _product_max_turn_witness(candidate, review_code_commit, source_hashes, *, root=ROOT):
    """Reproduce only the shipped product's specific source-bound turn-limit error."""
    if not isinstance(source_hashes, dict):
        raise ManifestError("turn-limit proof lacks reviewed source hashes")
    pipeline_hash = source_hashes.get("app/pipeline.py")
    if pipeline_hash != "147b6d20ecc9e5f1ef157b6dba6fad22b675cee2bf23fc8d63bf9523b7d2c620":
        raise ManifestError("turn-limit proof requires the reviewed app pipeline")
    _verify_file("app/pipeline.py", pipeline_hash, root)
    if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
    from app import pipeline
    if digest_file(Path(pipeline.__file__)) != pipeline_hash or pipeline.MAX_TURNS != 100:
        raise ManifestError("shipped app turn-limit source changed")
    _verify_file(candidate.get("source_path"), candidate.get("source_sha256"), root)
    matches = [(record, record_hash) for index, record, record_hash in
               _read_source_jsonl(candidate["source_path"], root=root)
               if index == candidate.get("source_record_index")]
    if len(matches) != 1:
        raise ManifestError("turn-limit source record missing")
    record, record_hash = matches[0]
    if (record_hash != candidate.get("source_record_sha256")
            or _raw_game_uid(record) != candidate.get("uid")
            or _raw_turn_count(record) != candidate.get("turns")):
        raise ManifestError("turn-limit source record differs from candidate")
    raw_log = record.get("log")
    raw_log = "\n".join(raw_log) if isinstance(raw_log, list) else raw_log
    if not isinstance(raw_log, str):
        raise ManifestError("turn-limit replay missing")
    parsed = None
    try:
        parsed = pipeline.PR.GameParser({"log": raw_log}, None, set()).run().turn
        pipeline.inspect_game({"log": raw_log, "formatid": "gen9ou", "private": True})
    except pipeline.InputError as exc:
        if (type(exc) is not pipeline.InputError or type(parsed) is not int
                or parsed != candidate["turns"] or parsed <= pipeline.MAX_TURNS
                or str(exc) != f"This game lasted {parsed} turns; HardRead reviews games up to {pipeline.MAX_TURNS} turns."):
            raise ManifestError("app rejected source for a different reason") from None
    except Exception as exc:
        raise ManifestError("cannot reproduce turn-limit source proof") from exc
    else:
        raise ManifestError("app did not reject the overlong source")
    return {"schema": "c9-product-max-turns-witness-v1", "exception_type": "ProductMaxTurns",
            "pool": candidate["pool"], "rank_hash": rank_hash(candidate["uid"], candidate["side"]),
            "candidate_sha256": digest_json(_source_identity(candidate)),
            "source_path": candidate["source_path"], "source_sha256": candidate["source_sha256"],
            "source_record_index": candidate["source_record_index"],
            "source_record_sha256": record_hash, "raw_turn_count": candidate["turns"],
            "parsed_turn_count": parsed, "product_max_turns": pipeline.MAX_TURNS,
            "app_pipeline_sha256": pipeline_hash, "review_code_commit": review_code_commit,
            "observed_error_class": "InputError"}


def select_candidates(candidates, pool, review_fn, context_fn, *, limit=ROWS_PER_POOL,
                      farm_source_identity=None, offered_fn=None, review_code_commit=None,
                      review_source_sha256=None, root=ROOT, exception_witness_fn=None):
    """Rank eligible sides, count review exceptions, and keep the first unique-game successes.

    ``review_fn(candidate)`` returns a review mapping. A successful review whose context
    cannot be derived is a hard blocker; it is never converted into another exclusion.
    """
    if pool not in POOLS:
        raise ManifestError(f"unknown pool: {pool}")
    if pool == "hr_val":
        _require_farm_identity(farm_source_identity, root=root)
    candidates = list(candidates)
    if type(limit) is not int or limit < 1:
        raise ManifestError("limit must be a positive integer")
    offered_fn = offered_fn or _build_offered
    ranked = []
    for candidate in candidates:
        eligibility = _candidate_eligibility(candidate, pool)
        if eligibility is None:
            continue
        row = dict(candidate)
        row["eligibility"] = eligibility
        row["rank_hash"] = rank_hash(row["uid"], row["side"])
        ranked.append(row)
    ranked.sort(key=lambda r: (r["rank_hash"], r["uid"], r["side"]))
    selected, selected_uids, exceptions, duplicate_sides = [], set(), 0, 0
    failed, attempts = [], []
    for candidate in ranked:
        attempt = {"pool": pool, "uid": candidate["uid"], "side": candidate["side"],
                   "rank_hash": candidate["rank_hash"], "candidate_sha256": digest_json(_source_identity(candidate)),
                   "review_code_commit": review_code_commit,
                   "review_source_sha256": review_source_sha256 or {}}
        if candidate["uid"] in selected_uids:
            duplicate_sides += 1
            attempts.append({**attempt, "outcome": "duplicate_game"})
            continue
        if type(candidate.get("turns")) is int and candidate["turns"] > 100:
            witness = _product_max_turn_witness(candidate, review_code_commit, review_source_sha256, root=root)
            if exception_witness_fn is None:
                raise ManifestError("turn-limit exception witness writer is required")
            ref = exception_witness_fn(witness)
            if not isinstance(ref, dict) or not ref.get("path") or not ref.get("sha256"):
                raise ManifestError("turn-limit exception witness was not saved")
            witness_path = _resolve_path(ref["path"], root)
            if not witness_path.is_relative_to((Path(root) / "scratch_ds" / "evals").resolve()):
                raise ManifestError("turn-limit witness must be private")
            saved = _read_json(ref["path"], root)
            _verify_file(ref["path"], ref["sha256"], root)
            if canonical_json(saved) != canonical_json(witness):
                raise ManifestError("saved turn-limit witness differs from source proof")
            exceptions += 1
            failed.append({"uid": candidate["uid"], "side": candidate["side"], "exception_type": "ProductMaxTurns"})
            attempts.append({**attempt, "outcome": "review_exception", "exception_type": "ProductMaxTurns",
                             "raw_turn_count": witness["raw_turn_count"], "parsed_turn_count": witness["parsed_turn_count"],
                             "product_max_turns": witness["product_max_turns"], "source_record_index": witness["source_record_index"],
                             "source_record_sha256": witness["source_record_sha256"],
                             "witness_path": ref["path"], "witness_file_sha256": ref["sha256"],
                             "witness_sha256": digest_json(witness)})
            continue
        try:
            review = review_fn(candidate)
        except ManifestError:
            # App preparation failures and explicit integrity stops are never review exceptions.
            raise
        except Exception as exc:
            # No separately accepted exception receipt exists: do not proceed to another rank.
            raise ManifestError("candidate exception provenance is pending; selection stopped") from exc
        try:
            ctx = context_fn(candidate, review)
            if not isinstance(ctx, dict):
                raise ManifestError("app context builder returned no context")
            offered = offered_fn(review, ctx)
            keys = keys_for_offered(offered)
            decision_keys = {(d.get("turn"), d.get("k", 0)) for d in review.get("decisions", [])
                             if isinstance(d, dict)}
            if any((x["turn"], x["k"]) not in decision_keys for x in keys):
                raise ManifestError("app offered key is absent from review decisions")
        except Exception as exc:
            raise ManifestError("app context/offered binding failed after successful review") from exc
        selection_sha = _selected_attempt_digest(candidate, review, ctx, offered)
        selected.append({**candidate, "review": review, "ctx": ctx, "offered": offered,
                         "ordered_offered_keys": keys, "selection_sha256": selection_sha})
        attempts.append({**attempt, "outcome": "selected", "selected_row_sha256": selection_sha})
        selected_uids.add(candidate["uid"])
        if len(selected) == limit:
            break
    census = []
    for candidate in candidates:
        row = _source_identity(candidate)
        row["rank_hash"] = (rank_hash(candidate["uid"], candidate["side"])
                             if candidate.get("uid") and candidate.get("side") in ("p1", "p2") else None)
        row["exclusion_reason"] = _candidate_exclusion_reason(candidate, pool)
        census.append(row)
    census.sort(key=lambda row: (row.get("source_record_index") if type(row.get("source_record_index")) is int
                                 else -1, row.get("side") or ""))
    return {"selected": selected, "review_exceptions": exceptions,
            "duplicate_sides_skipped": duplicate_sides, "review_failures": failed,
            "eligibility": eligibility_counts(candidates, pool), "candidate_census": census,
            "attempt_evidence": {"attempts": attempts, "attempts_sha256": digest_json(attempts),
                                 "review_code_commit": review_code_commit,
                                 "review_exceptions": exceptions,
                                 "review_source_sha256": review_source_sha256 or {}}}


def _build_offered(review, ctx):
    # Import the shipped app implementation, so the frozen list uses its exact policy.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app import pipeline
    return pipeline.build_view_with_offered(review, ctx)[1]


def build_artifact_row(candidate, *, source_path, source_sha256, replay_path, replay_sha256,
                       review_path, ctx_path, offered_path, review_code_commit,
                       active_calibrator, root=ROOT, offered_fn=None):
    """Write one prepared review/context/offered triple under scratch_ds/evals and bind hashes."""
    offered_fn = offered_fn or _build_offered
    review, ctx = candidate.get("review"), candidate.get("ctx")
    if not isinstance(review, dict) or not isinstance(ctx, dict):
        raise ManifestError("selected candidate lacks a review or app context")
    offered = offered_fn(review, ctx)
    keys = keys_for_offered(offered)
    if keys != candidate.get("ordered_offered_keys"):
        raise ManifestError("offered list changed between selection and freeze")
    selection_sha = _selected_attempt_digest(candidate, review, ctx, offered)
    if candidate.get("selection_sha256") != selection_sha:
        raise ManifestError("selected attempt hash changed before freeze")
    paths_and_data = ((review_path, review), (ctx_path, ctx), (offered_path, offered))
    hashes = {}
    eval_dir = (Path(root) / "scratch_ds" / "evals").resolve()
    for rel, data in paths_and_data:
        target = (Path(root) / rel).resolve()
        if not target.is_relative_to(eval_dir):
            raise ManifestError("frozen review/context/offered artifacts must stay under scratch_ds/evals")
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical_json(data).encode("utf-8") + b"\n"
        try:
            with open(target, "xb") as f:
                f.write(raw)
                f.flush()
        except FileExistsError as exc:
            raise ManifestError(f"refusing to overwrite frozen artifact: {rel}") from exc
        hashes[rel] = digest_bytes(raw)
    raw_offered = canonical_json(offered).encode("utf-8")
    if not isinstance(active_calibrator, str) or not active_calibrator:
        raise ManifestError("active calibrator must be recorded")
    if not isinstance(review_code_commit, str) or not review_code_commit:
        raise ManifestError("review source commit must be recorded")
    return {"pool": candidate["pool"], "uid": candidate["uid"], "side": candidate["side"],
            "rank_hash": candidate["rank_hash"], "source_path": source_path,
            "source_sha256": source_sha256, "replay_path": replay_path,
            "replay_sha256": replay_sha256, "review_path": review_path,
            "review_sha256": hashes[review_path], "ctx_path": ctx_path,
            "ctx_sha256": hashes[ctx_path], "offered_path": offered_path,
            "offered_sha256": digest_bytes(raw_offered), "ordered_offered_keys": keys,
            "eligibility": candidate["eligibility"], "selection_sha256": selection_sha,
            "source_record_index": candidate.get("source_record_index"),
            "source_record_sha256": candidate.get("source_record_sha256"),
            "team_exports": candidate.get("team_exports"), "review_code_commit": review_code_commit,
            "app_policy_version": APP_POLICY_VERSION, "active_calibrator": active_calibrator}


def manifest_digest(manifest):
    return digest_json(manifest)


def _resolve_path(path, root=ROOT):
    if not isinstance(path, str) or not path:
        raise ManifestError("manifest path is missing")
    p = Path(path)
    p = p if p.is_absolute() else Path(root) / p
    try:
        p.resolve().relative_to(Path(root).resolve())
    except ValueError as exc:
        raise ManifestError("manifest path escapes the workspace") from exc
    return p.resolve()


def _read_json(path, root=ROOT):
    p = _resolve_path(path, root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot read frozen JSON input: {path}") from exc


def _verify_file(path, expected, root=ROOT):
    if not isinstance(expected, str) or len(expected) != 64:
        raise ManifestError(f"missing SHA-256 for {path}")
    actual = digest_file(_resolve_path(path, root))
    if actual != expected:
        raise ManifestError(f"frozen input hash changed: {path}")
    return actual


def _validate_row_eligibility(row):
    e = row.get("eligibility")
    if not isinstance(e, dict) or e.get("decided") is not True or e.get("result") not in ("win", "loss"):
        raise ManifestError("manifest row is not a decided win/loss")
    if type(e.get("turns")) is not int or e["turns"] < 10:
        raise ManifestError("manifest row has fewer than ten turns")
    if row["pool"] == "crawl":
        if e.get("half") != 1 or calib_b.half(row["uid"]) != 1:
            raise ManifestError("crawl row is not in the registered held-out half")
    elif e.get("both_seats_eligible") is not True:
        raise ManifestError("farm row does not record both eligible seats")


def _census_by_identity(census):
    return {(row.get("pool"), row.get("uid"), row.get("side")): row for row in census}


def _validate_attempt_evidence(pool, evidence, census, rows, manifest, *, root):
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
    for path, expected_hash in source_hashes.items():
        _verify_file(path, expected_hash, root)
    if source_hashes != manifest.get("review_source_sha256"):
        raise ManifestError(f"{pool} attempt log review source hashes differ from the manifest")
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
            witness = _product_max_turn_witness(candidate, evidence.get("review_code_commit"), source_hashes, root=root)
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


def _validate_candidate_census(manifest, sources, *, root):
    declared = manifest.get("candidate_census")
    if not isinstance(declared, dict) or set(declared) != set(POOLS):
        raise ManifestError("complete per-pool source candidate census is missing")
    expected = {pool: enumerate_source_candidates(pool, sources, root=root) for pool in POOLS}
    for pool, entries in expected.items():
        record_by_uid = {}
        for entry in entries:
            uid = entry.get("uid")
            if not isinstance(uid, str):
                continue
            prior = record_by_uid.setdefault(uid, entry.get("source_record_index"))
            if prior != entry.get("source_record_index"):
                raise ManifestError(f"{pool} raw source contains duplicate game UIDs")
    if canonical_json(declared) != canonical_json(expected):
        raise ManifestError("candidate census differs from facts re-derived from the hashed raw sources")
    return expected


def _validate_selected_context(row, derived, source_record, pool, uid, side, ctx, root, draft_rows, draft_profiles):
    _verify_file(row.get("team_path"), row.get("team_sha256"), root)
    raw_log = source_record.get("log")
    raw_log = "\n".join(raw_log) if isinstance(raw_log, list) else raw_log
    if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
    from app import pipeline
    rep = {"log": raw_log, "formatid": "gen9ou", "private": True}
    team_data = _resolve_path(row["team_path"], root).read_bytes()
    if pool == "hr_val":
        from c9_farm_source import export_bytes
        if row.get("team_exports") != derived.get("team_exports"):
            raise ManifestError("selected team provenance differs from source")
        if team_data != export_bytes(root, derived["team_exports"][side]):
            raise ManifestError("selected team export differs from pinned pool")
        lines, expected_ctx, _ = pipeline.prepare(rep, side, team_text=team_data.decode("utf-8"))
    else:
        lines, expected_ctx, job = pipeline.prepare(rep, side)
        import human_eval
        expected_team = human_eval.draft_team_smogon(draft_rows[(uid, side)], draft_profiles, job["genders"])
        if team_data != expected_team.encode("utf-8"):
            raise ManifestError("draft team differs from pinned Draft-B inputs")
    replay = _read_json(row["replay_path"], root)
    if replay != {"log": "\n".join(lines)} or canonical_json(ctx) != canonical_json(expected_ctx):
        raise ManifestError("replay or app context differs from source")


def validate_manifest(manifest, *, root=ROOT, app_offered_fn=None, require_evals_paths=False):
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
        _validate_attempt_evidence(pool, selection_evidence.get(pool), census_by_pool[pool], pool_rows, manifest,
                                   root=root)
    return per_pool


def _manifest_items(manifest_path, manifest):
    digest = manifest_digest(manifest)
    rows = manifest["rows"]
    by_pool = {p: [i for i, row in enumerate(rows) if row["pool"] == p] for p in POOLS}
    a1 = []
    for row_index, row in enumerate(rows):
        for q in (1, 2, 3):
            a1.append({"row_index": row_index, "pool": row["pool"], "uid": row["uid"], "side": row["side"],
                       "rank_hash": row["rank_hash"], "review": row["review_path"],
                       "review_sha256": row["review_sha256"], "ctx": row["ctx_path"],
                       "ctx_sha256": row["ctx_sha256"], "offered": row["offered_path"],
                       "offered_sha256": row["offered_sha256"],
                       "ordered_offered_keys": row["ordered_offered_keys"],
                       "source_path": row["source_path"], "source_sha256": row["source_sha256"],
                       "replay": row["replay_path"], "replay_sha256": row["replay_sha256"],
                       "q": q, "question": QUESTIONS[q]})
    a0 = []
    for pool in POOLS:
        for row_index in by_pool[pool][:A0_ROWS_PER_POOL]:
            row = rows[row_index]
            for q in (1, 2, 3):
                a0.append({"row_index": row_index, "pool": pool, "uid": row["uid"], "side": row["side"],
                           "rank_hash": row["rank_hash"], "log": row["replay_path"],
                           "log_sha256": row["replay_sha256"], "review": row["review_path"],
                           "review_sha256": row["review_sha256"], "source_path": row["source_path"],
                           "source_sha256": row["source_sha256"], "q": q, "question": QUESTIONS[q]})
    return ({"manifest_path": manifest_path, "manifest_sha256": digest, "items": a1},
            {"manifest_path": manifest_path, "manifest_sha256": digest, "items": a0})


def validate_items_document(document, kind, *, root=ROOT, app_offered_fn=None):
    """Load and independently validate a recorded runner's frozen item document."""
    expected_count = 60 if kind == "a1" else 30 if kind == "a0" else None
    if expected_count is None or not isinstance(document, dict):
        raise ManifestError("recorded item document has an unsupported shape")
    manifest_path = document.get("manifest_path")
    manifest = _read_json(manifest_path, root)
    per_pool = validate_manifest(manifest, root=root, app_offered_fn=app_offered_fn, require_evals_paths=True)
    digest = manifest_digest(manifest)
    if document.get("manifest_sha256") != digest:
        raise ManifestError("selection manifest digest changed")
    expected_a1, expected_a0 = _manifest_items(manifest_path, manifest)
    expected = expected_a1 if kind == "a1" else expected_a0
    if canonical_json(document) != canonical_json(expected):
        raise ManifestError("item list differs from the fixed manifest/question order")
    if len(document.get("items", [])) != expected_count:
        raise ManifestError(f"recorded {kind.upper()} item count must be exactly {expected_count}")
    return manifest, per_pool, digest


def load_validated_a1(document, *, root=ROOT, app_offered_fn=None, visible_verdicts_fn=None):
    manifest, per_pool, digest = validate_items_document(document, "a1", root=root,
                                                         app_offered_fn=app_offered_fn)
    if visible_verdicts_fn is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from app import server as app_server
        visible_verdicts_fn = app_server._visible_verdicts
    items = []
    for it in document["items"]:
        row = manifest["rows"][it["row_index"]]
        review = _read_json(it["review"], root)
        ctx = _read_json(it["ctx"], root)
        offered = _read_json(it["offered"], root)
        verdicts = {}
        if row.get("verdicts_path"):
            _verify_file(row["verdicts_path"], row.get("verdicts_sha256"), root)
            verdicts = _read_json(row["verdicts_path"], root)
            if not isinstance(verdicts, dict):
                raise ManifestError("cached verdict file must contain a mapping")
        visible = visible_verdicts_fn(offered, verdicts)
        items.append({**it, "review_data": review, "ctx_data": ctx, "offered_data": offered,
                      "visible_verdicts": visible})
    return manifest, digest, items


def load_validated_a0(document, *, root=ROOT):
    manifest, _per_pool, digest = validate_items_document(document, "a0", root=root)
    items = []
    for it in document["items"]:
        row = manifest["rows"][it["row_index"]]
        _verify_file(it["log"], it["log_sha256"], root)
        _verify_file(it["review"], it["review_sha256"], root)
        items.append(dict(it))
    return manifest, digest, items


def git_head_for(path, root=ROOT):
    import subprocess
    result = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%H", "--", path],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode or not result.stdout.strip():
        raise ManifestError(f"cannot resolve current source commit for {path}")
    return result.stdout.strip()


def git_repo_head(path):
    import subprocess
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=10, check=False)
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", result.stdout.strip()):
        raise ManifestError("cannot resolve Foul Play repository commit")
    return result.stdout.strip()


def _mac_process_snapshot(pid):
    """Read argv, executable path and start time for a live macOS PID from OS process APIs."""
    import ctypes
    import ctypes.util
    import datetime
    import os
    import struct
    import subprocess
    import time

    if sys.platform != "darwin":
        raise ManifestError("actual structured server-process evidence requires macOS")
    libproc_path = ctypes.util.find_library("proc")
    if not libproc_path:
        raise ManifestError("macOS libproc is unavailable for server-process verification")
    libproc = ctypes.CDLL(libproc_path, use_errno=True)
    proc_pidpath = libproc.proc_pidpath
    proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
    proc_pidpath.restype = ctypes.c_int
    executable_buffer = ctypes.create_string_buffer(4096)
    written = proc_pidpath(pid, executable_buffer, len(executable_buffer))
    if written <= 0:
        raise ManifestError("cannot resolve the live server executable from its PID")
    executable_path = str(Path(os.fsdecode(executable_buffer.value)).resolve())

    libc = ctypes.CDLL(None, use_errno=True)
    sysctl = libc.sysctl
    sysctl.argtypes = (ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t)
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, pid
    length = ctypes.c_size_t(0)
    if sysctl(mib, 3, None, ctypes.byref(length), None, 0) != 0 or length.value < 8:
        raise ManifestError("cannot read structured argv for the live server PID")
    raw_buffer = ctypes.create_string_buffer(length.value)
    if sysctl(mib, 3, raw_buffer, ctypes.byref(length), None, 0) != 0:
        raise ManifestError("cannot read structured argv for the live server PID")
    raw = raw_buffer.raw[:length.value]
    argc = struct.unpack_from("i", raw, 0)[0]
    if argc <= 0 or argc > 512:
        raise ManifestError("live server process has an invalid argv count")
    remainder = raw[4:]
    _exec_name, sep, remainder = remainder.partition(b"\0")
    if not sep:
        raise ManifestError("live process argv header is malformed")
    remainder = remainder.lstrip(b"\0")
    argv = []
    for _index in range(argc):
        token, sep, remainder = remainder.partition(b"\0")
        if not sep:
            raise ManifestError("live process argv is truncated")
        argv.append(os.fsdecode(token))
    start_result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True,
                                  text=True, timeout=5, check=False)
    if start_result.returncode != 0 or not start_result.stdout.strip():
        raise ManifestError("cannot read the live server PID start identity")
    try:
        local_start = time.mktime(time.strptime(start_result.stdout.strip(), "%a %b %d %H:%M:%S %Y"))
    except ValueError as exc:
        raise ManifestError("live server PID start identity is malformed") from exc
    started_utc = datetime.datetime.fromtimestamp(local_start, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"pid": pid, "argv": argv, "executable_path": executable_path,
            "executable_sha256": digest_file(executable_path), "started_utc": started_utc}


def _validate_live_process(data, saved_process, *, process_probe=None):
    """Require exact saved/live OS process identity and protocol arguments; return its model path."""
    required_saved = ("pid", "host", "argv", "executable_path", "executable_sha256", "started_utc")
    if not isinstance(saved_process, dict) or any(key not in saved_process for key in required_saved):
        raise ManifestError("saved process evidence lacks structured PID/argv/executable/start identity")
    if process_probe is None:
        process_probe = _mac_process_snapshot
    live = process_probe(data["pid"])
    if not isinstance(live, dict):
        raise ManifestError("live process probe did not return a structured OS snapshot")
    expected_root = {"pid": data["pid"], "host": data["host"],
                     "argv": data.get("server_argv"), "executable_path": data.get("executable_path"),
                     "executable_sha256": data.get("executable_sha256"),
                     "started_utc": data.get("process_started_utc")}
    expected_saved = {key: saved_process.get(key) for key in required_saved}
    expected_live = {key: live.get(key) for key in required_saved}
    expected_live["host"] = data["host"]
    if expected_saved != expected_root or expected_live != expected_saved:
        raise ManifestError("live server PID/start/argv/executable differs from saved provenance")
    argv = expected_live["argv"]
    if (not isinstance(argv, list) or not argv or not all(isinstance(value, str) for value in argv)
            or not isinstance(expected_live["executable_path"], str)
            or not Path(expected_live["executable_path"]).is_absolute()):
        raise ManifestError("live server executable path or argv is malformed")
    try:
        from datetime import datetime, timezone
        started = datetime.fromisoformat(expected_live["started_utc"].replace("Z", "+00:00"))
        if started.tzinfo is None or started.utcoffset().total_seconds() != 0:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as exc:
        raise ManifestError("live server PID start identity must be an explicit UTC timestamp") from exc
    executable_path = str(Path(expected_live["executable_path"]).resolve())
    if not Path(argv[0]).is_absolute() or str(Path(argv[0]).resolve()) != executable_path:
        raise ManifestError("argv[0] does not identify the executable resolved from the live PID")
    if digest_file(executable_path) != expected_live["executable_sha256"]:
        raise ManifestError("live server executable bytes differ from the recorded executable hash")
    if not isinstance(data.get("executable_sha256"), str) or len(data["executable_sha256"]) != 64:
        raise ManifestError("live server executable hash is missing or malformed")

    expected_flags = ["--ctx-size", "32768", "--jinja", "--reasoning-format", "deepseek", "--swa-full",
                      "--gpu-layers", "99", "--parallel", "1"]
    actual_flags, launch_values, i = [], {}, 1
    while i < len(argv):
        token = argv[i]
        if token in ("--model", "--host", "--port"):
            if token in launch_values or i + 1 >= len(argv):
                raise ManifestError("live server launch flags are missing or duplicated")
            launch_values[token] = argv[i + 1]
            i += 2
            continue
        actual_flags.append(token)
        i += 1
    if set(launch_values) != {"--model", "--host", "--port"}:
        raise ManifestError("live server argv must contain exactly model, host and port")
    if launch_values["--host"] not in ("127.0.0.1", "localhost") or launch_values["--port"] != "18131":
        raise ManifestError("live server endpoint differs from the handed-over local endpoint")
    if actual_flags != expected_flags or data.get("server_flags") != expected_flags:
        raise ManifestError("live server argv does not match the exact protocol flags")
    model_path = launch_values["--model"]
    saved_model_path = data.get("evidence", {}).get("model", {}).get("model_path")
    if model_path != saved_model_path:
        raise ManifestError("live server model argument differs from saved model evidence")
    return model_path


def _live_executable_version(executable_path):
    """Query the executable resolved from the serving PID, without contacting the server."""
    import subprocess
    try:
        result = subprocess.run([executable_path, "--version"], capture_output=True, text=True,
                                timeout=5, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError("cannot query live executable version") from exc
    if result.returncode != 0:
        raise ManifestError("live executable version query failed")
    return result.stdout + result.stderr


def validate_server_provenance(path, *, root=ROOT, now=None, process_probe=None, version_probe=None):
    """Validate a private evidence bundle; a caller-supplied claim without matching files is rejected."""
    from datetime import datetime, timezone
    import os
    import platform
    import subprocess
    import time

    p = _resolve_path(path, root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ManifestError("server provenance cannot be read") from exc
    required = ("gguf_sha256", "llama_cpp_commit", "llama_cpp_build", "server_flags", "server_argv",
                "executable_path", "executable_sha256", "process_started_utc", "url", "pid", "host",
                "verified_utc", "app_commit", "source_commit", "evidence")
    if any(key not in data for key in required):
        raise ManifestError("server provenance is missing required actual-evidence fields")
    if data["llama_cpp_commit"] != "f8def7f":
        raise ManifestError("llama.cpp commit differs from EVAL_PROTOCOL_AGENT.md")
    expected_flags = ["--ctx-size", "32768", "--jinja", "--reasoning-format", "deepseek", "--swa-full",
                      "--gpu-layers", "99", "--parallel", "1"]
    if data["server_flags"] != expected_flags:
        raise ManifestError("llama-server flags differ from EVAL_PROTOCOL_AGENT.md")
    if data["url"] != "http://127.0.0.1:18131/v1/chat/completions":
        raise ManifestError("recorded server URL is not the handed-over local endpoint")
    if not isinstance(data["gguf_sha256"], str) or len(data["gguf_sha256"]) != 64:
        raise ManifestError("served GGUF SHA-256 is missing or malformed")
    if type(data["pid"]) is not int or data["pid"] <= 1:
        raise ManifestError("server PID is missing or malformed")
    if data["host"] != platform.node():
        raise ManifestError("server provenance host does not match this machine")
    try:
        verified = datetime.fromisoformat(data["verified_utc"].replace("Z", "+00:00"))
        if verified.tzinfo is None or verified.utcoffset().total_seconds() != 0:
            raise ValueError
    except (AttributeError, TypeError, ValueError) as exc:
        raise ManifestError("server verification time must be an explicit UTC timestamp") from exc
    current = time.time() if now is None else now
    if verified.timestamp() > current + 60 or current - verified.timestamp() > 300:
        raise ManifestError("server provenance is stale")
    evidence = data["evidence"]
    if not isinstance(evidence, dict) or set(("process", "build", "model", "health", "app_quiescent")) - set(evidence):
        raise ManifestError("underlying process/build/model/health/quiescence evidence is incomplete")
    evidence_data = {}
    for name in ("process", "build", "model", "health", "app_quiescent"):
        item = evidence[name]
        if not isinstance(item, dict) or not item.get("path") or not item.get("sha256"):
            raise ManifestError(f"{name} evidence path/hash is missing")
        _verify_file(item["path"], item["sha256"], root)
        evidence_data[name] = _read_json(item["path"], root) if name != "build" else _resolve_path(item["path"], root).read_text()
    process = evidence_data["process"]
    if process.get("pid") != data["pid"] or process.get("host") != data["host"]:
        raise ManifestError("server process evidence does not match recorded PID/host")
    model_path = _validate_live_process(data, process, process_probe=process_probe)
    model_file = Path(model_path)
    if not model_file.is_absolute():
        model_file = Path(root) / model_file
    model_file = model_file.resolve()
    if digest_file(model_file) != data["gguf_sha256"] or evidence_data["model"].get("sha256") != data["gguf_sha256"]:
        raise ManifestError("served GGUF bytes do not match the recorded SHA-256")
    build_text = evidence_data["build"]
    executable = str(Path(data["executable_path"]).resolve())
    version_probe = version_probe or _live_executable_version
    try:
        live_version = version_probe(executable)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError("cannot query live executable version") from exc
    # Check bytes again after the query and recheck the PID to detect replacement or reuse.
    if digest_file(executable) != data["executable_sha256"]:
        raise ManifestError("live executable bytes changed during version query")
    _validate_live_process(data, process, process_probe=process_probe)
    # llama.cpp emits one `version: <build> (<commit>)` line in --version output.
    # Parse that line itself: unrelated output must not supply either field.
    version_lines = ([line for line in live_version.splitlines() if line.strip().startswith("version:")]
                     if isinstance(live_version, str) else [])
    match = (re.fullmatch(r"version: ([0-9]+) \(([0-9a-fA-F]+)\)", version_lines[0])
             if len(version_lines) == 1 else None)
    if (not isinstance(data.get("llama_cpp_build"), str) or not isinstance(build_text, str)
            or not isinstance(live_version, str) or live_version.strip() != build_text.strip()
            or match is None or match.group(1) != data["llama_cpp_build"]
            or match.group(2) != data["llama_cpp_commit"]):
        raise ManifestError("live executable version does not match the pinned llama.cpp build")
    health = evidence_data["health"]
    if health.get("status") != "ok" or health.get("url") != data["url"]:
        raise ManifestError("saved local health evidence does not verify the endpoint")
    quiescent = evidence_data["app_quiescent"]
    if quiescent.get("inflight_llm_calls") != 0 or quiescent.get("queue_checked") is not True:
        raise ManifestError("app quiescence evidence does not show an empty in-flight queue")
    if data["app_commit"] != git_head_for("app", root) or data["source_commit"] != git_head_for("tools/review", root):
        raise ManifestError("app/review source commits changed since provenance capture")
    dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "tools/agent"],
                           capture_output=True, text=True, timeout=10, check=False)
    if dirty.returncode or dirty.stdout.strip():
        raise ManifestError("evaluator source is dirty")
    if "f8def7f" not in build_text:
        raise ManifestError("live server build does not match the required llama.cpp commit")
    return data


def validate_record_output_paths(out, *, root=ROOT, companion=None):
    """Refuse overwrite and keep recorded result paths in the private evaluation directory."""
    base = (Path(root) / "scratch_ds" / "evals").resolve()
    target = Path(out)
    target = target if target.is_absolute() else Path(root) / target
    try:
        target.resolve().relative_to(base)
    except ValueError as exc:
        raise ManifestError("recorded outputs must stay under private scratch_ds/evals") from exc
    paths = [target]
    if companion is not None:
        c = Path(companion)
        c = c if c.is_absolute() else Path(root) / c
        try:
            c.resolve().relative_to(base)
        except ValueError as exc:
            raise ManifestError("recorded companion output must stay under private scratch_ds/evals") from exc
        paths.append(c)
    for p in paths:
        if p.exists():
            raise ManifestError(f"refusing to overwrite existing recorded output: {p.name}")
    return target, (paths[1] if len(paths) > 1 else None)
