"""Pure, versioned provenance identity for product verdict cache entries.

This module is deliberately importable by both the system-Python app and the
Foul Play worker.  It has no Foul Play, model, network, or server dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


IDENTITY_SCHEMA = "hardread.verdict-cache-identity.v3"
IDENTITY_VERSION = 3
RUNTIME_SCHEMA = "hardread.verdict-runtime-artifacts.v2"
RESULT_DIGEST_FIELD = "result_sha256"

PRODUCT_SETTINGS = {
    "reference": "auto",
    "seeds": [11, 12, 13],
    "worlds": 4,
    "iterations": 60000,
    "margin": 0.08,
    "ok_margin": 0.02,
    "calibration": "absolute_human",
    "verdict_source_version": 1,
}

_REVIEW_CODE = (
    "tools/review/verdict.py",
    "tools/review/verdict_identity.py",
    "tools/review/fpenv.py",
    "tools/review/reconstruct.py",
    "tools/review/evaluate.py",
    "tools/review/value.py",
    "tools/review/bridge.py",
    "tools/review/calibrator.json",
    "app/server.py",
    "app/pipeline.py",
    "app/card.py",
    "app/static/app.js",
)


class StaleCacheError(ValueError):
    """A cache entry is corrupt, legacy, or bound to another request identity."""


def canonical_json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_json(value):
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _artifact_row(path):
    p = Path(path).resolve(strict=True)
    if not p.is_file():
        raise ValueError(f"runtime artifact is not a file: {p}")
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p)}


def runtime_artifact_manifest(hardread_root, foul_play_dir, loaded_modules, runtime_config=None,
                              model_artifacts=None):
    """Hash source/data files and the actual loaded poke-engine module(s).

    Hashing the Foul Play package data tree covers the exact set, move, and
    Pokédex inputs used by reconstruction and sampling, including files that
    are loaded lazily after a cache miss.  Loaded poke-engine module origins
    bind the native search implementation actually imported by this worker.
    """
    root = Path(hardread_root).resolve(strict=True)
    fp_root = Path(foul_play_dir).resolve(strict=True) / "fp"
    paths = [root / rel for rel in _REVIEW_CODE]
    if not fp_root.is_dir():
        raise ValueError(f"Foul Play package directory is missing: {fp_root}")
    paths.extend(p for p in fp_root.rglob("*") if p.is_file() and p.suffix.lower() in {".py", ".json"})

    engine_paths = set()
    for name, module in sorted((loaded_modules or {}).items()):
        if name == "poke_engine" or name.startswith(("poke_engine.", "_poke_engine")):
            origin = getattr(module, "__file__", None)
            if origin and Path(origin).is_file():
                engine_paths.add(Path(origin).resolve())
            for item in getattr(module, "__path__", ()) or ():
                base = Path(item)
                if base.is_dir():
                    engine_paths.update(p.resolve() for p in base.rglob("*")
                                        if p.is_file() and p.suffix.lower() in {".py", ".so", ".dylib", ".dll"})
    if not engine_paths:
        raise ValueError("the loaded poke-engine module has no hashable file origin")
    paths.extend(sorted(engine_paths))

    unique = sorted({p.resolve(strict=True) for p in paths}, key=lambda p: str(p))
    rows = [_artifact_row(p) for p in unique]
    runtime = {
        "python_implementation": getattr(sys, "implementation", None).name if getattr(sys, "implementation", None) else "unknown",
        "python_version": sys.version.split()[0],
        "python_executable": str(Path(sys.executable).resolve()),
        "runtime_config": runtime_config or {},
    }
    models = []
    for path in sorted(engine_paths):
        row = _artifact_row(path)
        row["name"] = "poke_engine"
        models.append(row)
    for name, path in sorted((model_artifacts or {}).items()):
        row = _artifact_row(path)
        row["name"] = str(name)
        models.append(row)
    model_digest = sha256_json({"schema": RUNTIME_SCHEMA, "models": models})
    payload = {"schema": RUNTIME_SCHEMA, "runtime": runtime, "artifacts": rows,
               "models": models, "models_sha256": model_digest}
    digest = sha256_json(payload)
    return dict(payload, sha256=digest)


def _calibration_sha256(runtime_manifest):
    matches = [row for row in runtime_manifest.get("artifacts", [])
               if str(row.get("path", "")).replace("\\", "/").endswith("/tools/review/calibrator.json")]
    if len(matches) != 1 or len(matches[0].get("sha256", "")) != 64:
        raise ValueError("the active calibration artifact is missing or ambiguous")
    return matches[0]["sha256"]


def build_request_identity(*, review_id, replay_path, team_path, side, turn, k, chosen, engine_best,
                           belief, settings, runtime_manifest):
    """Return the full canonical app/worker cache identity for one stated read."""
    if side not in {"p1", "p2"}:
        raise ValueError("side must be p1 or p2")
    if not isinstance(turn, int) or turn < 0 or not isinstance(k, int) or k < 0:
        raise ValueError("turn and k must be nonnegative integers")
    if not isinstance(chosen, str) or not chosen or not isinstance(engine_best, str) or not engine_best:
        raise ValueError("chosen and engine_best are required")
    if not isinstance(runtime_manifest, dict) or runtime_manifest.get("schema") != RUNTIME_SCHEMA:
        raise ValueError("runtime artifact manifest is missing or has an unsupported schema")
    manifest_payload = {key: runtime_manifest.get(key) for key in
                        ("schema", "runtime", "artifacts", "models", "models_sha256")}
    if runtime_manifest.get("sha256") != sha256_json(manifest_payload):
        raise ValueError("runtime artifact manifest digest is invalid")
    if runtime_manifest.get("models_sha256") != sha256_json({
            "schema": RUNTIME_SCHEMA, "models": runtime_manifest.get("models")}):
        raise ValueError("runtime loaded-model manifest digest is invalid")
    for key in ("reference", "seeds", "worlds", "iterations", "margin", "ok_margin", "calibration",
                "verdict_source_version"):
        if key not in settings:
            raise ValueError(f"verdict setting is missing: {key}")
    if not isinstance(settings["seeds"], list) or not settings["seeds"]:
        raise ValueError("verdict seeds must be a non-empty list")

    calibration_sha = _calibration_sha256(runtime_manifest)
    if settings.get("calibration_sha256", calibration_sha) != calibration_sha:
        raise ValueError("calibration content hash does not match the runtime manifest")
    pinned_settings = dict(settings)
    pinned_settings["calibration_sha256"] = calibration_sha
    model_sha = runtime_manifest.get("models_sha256")
    if not isinstance(model_sha, str) or len(model_sha) != 64:
        raise ValueError("loaded verdict model hash is missing")

    return {
        "schema": IDENTITY_SCHEMA,
        "identity_version": IDENTITY_VERSION,
        "review_id": str(review_id),
        "inputs": {
            "replay_sha256": sha256_file(replay_path),
            "team_sha256": sha256_file(team_path),
        },
        "decision": {"side": side, "turn": turn, "k": k, "chosen": chosen, "engine_best": engine_best},
        "belief_sha256": sha256_json(belief),
        "settings": pinned_settings,
        "models_sha256": model_sha,
        "runtime": runtime_manifest,
    }


def identity_sha256(identity):
    if not isinstance(identity, dict) or identity.get("schema") != IDENTITY_SCHEMA:
        raise ValueError("unsupported verdict cache identity")
    return sha256_json(identity)


def seal_result(result):
    """Bind every stored result field to an integrity digest after attaching identity/read data."""
    sealed = dict(result)
    sealed.pop(RESULT_DIGEST_FIELD, None)
    sealed[RESULT_DIGEST_FIELD] = sha256_json(sealed)
    return sealed


def cache_filename(turn, k, identity_digest):
    if not isinstance(identity_digest, str) or len(identity_digest) != 64 or any(
            c not in "0123456789abcdef" for c in identity_digest):
        raise ValueError("invalid verdict identity digest")
    return f"{int(turn)}_{int(k)}_{identity_digest}.json"


def validate_result(result, identity, digest, require_app_belief=False):
    """Fail closed unless a result and its three-run receipt match the request."""
    if not isinstance(result, dict):
        raise StaleCacheError("verdict cache is not a JSON object")
    if result.get("cache_identity") != identity or result.get("cache_identity_sha256") != digest:
        raise StaleCacheError("verdict cache identity does not match this read")
    if identity_sha256(identity) != digest:
        raise StaleCacheError("requested verdict identity digest is invalid")
    payload = {key: value for key, value in result.items() if key != RESULT_DIGEST_FIELD}
    if result.get(RESULT_DIGEST_FIELD) != sha256_json(payload):
        raise StaleCacheError("verdict result integrity digest is invalid")
    decision, settings = identity["decision"], identity["settings"]
    if (result.get("turn"), result.get("k"), result.get("chosen"), result.get("X")) != (
            decision["turn"], decision["k"], decision["chosen"], decision["engine_best"]):
        raise StaleCacheError("verdict result decision does not match this read")
    seeds = settings["seeds"]
    expected_references = {
        "auto": {"engine", "later"}, "later": {"engine", "later"},
        "engine": {"engine"}, "truth": {"truth"},
    }.get(settings["reference"], set())
    if result.get("reference") not in expected_references:
        raise StaleCacheError("verdict result reference does not match this request")
    if result.get("seeds") != seeds or result.get("version") != settings["verdict_source_version"]:
        raise StaleCacheError("verdict result source version or seeds do not match this request")
    runs = result.get("runs")
    if (not isinstance(runs, list) or [r.get("seed") for r in runs if isinstance(r, dict)] != seeds
            or len(runs) != len(seeds)):
        raise StaleCacheError("verdict result does not contain the registered runs")
    for condition in ("U", "R"):
        expected_deltas = []
        for run in runs:
            part = run.get(condition)
            if (not isinstance(part, dict) or not isinstance(part.get("wp"), dict) or not part["wp"]
                    or "delta" not in part or not isinstance(part.get("root_wp"), (int, float))
                    or not isinstance(part.get("missing"), list)):
                raise StaleCacheError("verdict result is missing per-run U/R comparisons")
            delta = part["delta"]
            if delta is not None and (not isinstance(delta, (int, float)) or isinstance(delta, bool)):
                raise StaleCacheError("verdict result has invalid per-run delta")
            expected_deltas.append(None if delta is None else round(delta, 4))
        if result.get("delta_" + condition) != expected_deltas:
            raise StaleCacheError("verdict result deltas do not match per-run comparisons")
    if result.get("iterations") != settings["iterations"] or result.get("worlds") != settings["worlds"]:
        raise StaleCacheError("verdict result search settings do not match this read")
    if result.get("margin") != settings["margin"] or result.get("ok_margin") != settings["ok_margin"]:
        raise StaleCacheError("verdict result thresholds do not match this request")
    if result.get("calibration") != settings["calibration"]:
        raise StaleCacheError("verdict result calibration does not match this request")
    if result.get("calibration_sha256") != settings["calibration_sha256"]:
        raise StaleCacheError("verdict result calibration content hash does not match this request")
    if result.get("models_sha256") != identity.get("models_sha256"):
        raise StaleCacheError("verdict result loaded-model hash does not match this request")
    if result.get("runtime_sha256") != identity.get("runtime", {}).get("sha256"):
        raise StaleCacheError("verdict result runtime hash does not match this request")
    has_app_belief = "app_belief" in result
    if require_app_belief and not has_app_belief:
        raise StaleCacheError("stated read is missing from this receipt")
    if has_app_belief and sha256_json(result.get("app_belief")) != identity.get("belief_sha256"):
        raise StaleCacheError("stored stated read does not match this receipt")
    return result


def load_verified_cache(path, identity, digest, require_app_belief=False):
    try:
        result = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise StaleCacheError("verdict cache entry is missing or corrupt") from exc
    return validate_result(result, identity, digest, require_app_belief=require_app_belief)


def write_json_exclusive(path, value):
    """Create a private cache file once; never overwrite an existing result."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value) + b"\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
