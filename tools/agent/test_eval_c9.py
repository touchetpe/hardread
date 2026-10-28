"""Synthetic acceptance tests for the C9 pre-record evaluator stage.

All fixture paths live in a fresh temporary directory. No registered dataset,
model, health endpoint, or chat completion is read or called by these tests.
"""
import hashlib
import copy
import gzip
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE_ROOT = next((parent for parent in HERE.parents if (parent / "tools" / "review" / "calib_b.py").is_file()), ROOT)
for dependency_path in (SOURCE_ROOT / "tools" / "review", SOURCE_ROOT / "tools" / "agent", HERE, ROOT):
    if str(dependency_path) not in sys.path:
        sys.path.append(str(dependency_path))


def _load_proposal_module(name):
    """Load each patched module by its isolated path, avoiding same-name live modules."""
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


manifest_tools = _load_proposal_module("build_eval_manifest")
_evallock_module = _load_proposal_module("evallock")
audit_a1 = _load_proposal_module("audit_a1")
score_a0 = _load_proposal_module("score_a0")
eval_a0 = _load_proposal_module("eval_a0")
eval_a1 = _load_proposal_module("eval_a1")
eval_lock = _evallock_module.eval_lock
from tools import ToolSession  # noqa: E402


REVIEW = {
    "meta": {"side": "p1", "end_reason": "ko", "end_turn": 3, "engine": "synthetic"},
    "bridge": {"version": 1, "S": 0.5, "L": 0.0, "R": 0.0, "C": 0.0,
               "s": 50, "l": 0, "c": 0, "result": "win"},
    "summary": {"result": "win", "start_win_prob": 0.5, "total_luck": 0.0,
                "decisions": 1, "win_prob_curve": [[0, 0.5], [2, 0.55], [3, 0.45]]},
    "turns": [{"turn": 2, "win_prob": 0.55, "action": "swordsdance",
                "opponent_action": "shadowball", "luck": 0.0, "luck_events": {},
                "luck_realised_prob": 1.0}],
    "decisions": [{"turn": 2, "k": 0, "kind": "move", "action": "swordsdance",
                    "win_prob": 0.55, "engine_best": "knockoff", "engine_best_win_prob": 0.64,
                    "chosen_win_prob": 0.55, "decision_cost": 0.09,
                    "top_alternatives": [], "opponent_policy_top": []}],
}
CTX = {
    "mode": "draft", "side": "p1", "end_reason": "ko", "turns": 3, "winner": "p1",
    "names": {}, "draft_rows": [{"species_preview": "Great Tusk", "species": "Great Tusk",
                                    "moves": ["swordsdance", "knockoff"], "seen": True}],
    "active": {"2:0": "Great Tusk"}, "opp_active": {}, "private": False,
    "own_species": ["Great Tusk"], "opp_species": ["Pikachu"],
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def write_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value.encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def write_bytes(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class SyntheticWorkspace(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hardread-c9-synthetic-")
        self.root = Path(self._tmp.name)
        self.eval_dir = self.root / "scratch_ds" / "evals"
        self.eval_dir.mkdir(parents=True)
        # These old evaluator fixtures exercise scoring/order; source authenticity and
        # exact app preparation are tested separately by the synthetic adapter suite.
        import c9_farm_source as farm_source
        self._patches = [
            patch.object(manifest_tools, "_require_farm_identity", side_effect=lambda identity, **kw:
                         None if isinstance(identity, dict) and identity.get("games") else
                         (_ for _ in ()).throw(manifest_tools.SourceUnresolvedError("source unresolved"))),
            patch.object(manifest_tools, "_validate_selected_context"),
            patch.object(manifest_tools, "git_repo_head", return_value="synthetic-fork-commit"),
            patch.object(farm_source, "checked", side_effect=lambda root, path, digest: Path(root) / path),
            patch.object(manifest_tools, "CRAWL_PINS", {}),
            patch.object(manifest_tools, "DRAFT_CACHE_SHA", "synthetic-cache"),
        ]
        for item in self._patches: item.start()
        cache = self.root / manifest_tools.DRAFT_CACHE_PATH
        cache.parent.mkdir(parents=True, exist_ok=True); cache.write_bytes(b"cache")
        manifest_tools.DRAFT_CACHE_SHA = hashlib.sha256(b"cache").hexdigest()
        cal = self.root / "tools/review/calibrator.json"
        cal.parent.mkdir(parents=True, exist_ok=True); cal.write_text('{"absolute_human":{"a":1}}')

    def tearDown(self):
        for item in reversed(self._patches): item.stop()
        self._tmp.cleanup()

    def _source_hashes(self, crawl_candidates, farm_candidates):
        paths = {
            ("crawl", "games"): manifest_tools.EXPECTED_CRAWL_SOURCES["games"],
            ("crawl", "sets"): manifest_tools.EXPECTED_CRAWL_SOURCES["sets"],
            ("crawl", "species_profiles"): manifest_tools.EXPECTED_CRAWL_SOURCES["species_profiles"],
            ("hr_val", "games"): "scratch_ds/val/synthetic-games.jsonl",
            ("hr_val", "decisions"): "scratch_ds/val/synthetic-decisions.jsonl",
        }
        result = {}
        for (pool, role), rel in paths.items():
            path = self.root / rel
            if pool == "crawl" and role == "games":
                records = []
                for candidate in crawl_candidates:
                    winner = "A" if candidate["result"] == "win" else "B"
                    records.append({"uid": candidate["uid"], "winner": winner,
                                    "turns": candidate["turns"],
                                    "log": [f"|turn|{turn}" for turn in range(1, candidate["turns"] + 1)]})
                raw = gzip.compress(("\n".join(json.dumps(row, sort_keys=True) for row in records) + "\n").encode())
                digest = write_bytes(path, raw)
            elif pool == "hr_val" and role == "games":
                records = []
                for candidate in farm_candidates:
                    team_exports = {}
                    for side in ("p1", "p2"):
                        rel_team = f"foul-play/fp/teams/teams/{candidate['uid']}-{side}.txt"
                        team_hash = write_text(self.root / rel_team, f"synthetic farm team fixture {side}\n")
                        team_exports[side] = {"member": f"gen9/hr_val/{candidate['uid']}-{side}.txt", "sha256": team_hash}
                        result[("hr_val", "team_export", candidate["uid"], side)] = {
                            "pool": "hr_val", "role": "team_export", "path": rel_team, "sha256": team_hash}
                    winner = "A" if candidate["result"] == "win" else "B"
                    records.append({"uid": candidate["uid"], "winner": winner,
                                    "turns": candidate["turns"], "team_exports": team_exports,
                                    "log": [f"|turn|{turn}" for turn in range(1, candidate["turns"] + 1)]})
                digest = write_text(path, "\n".join(json.dumps(row, sort_keys=True) for row in records) + "\n")
            elif pool == "hr_val" and role == "decisions":
                decisions = [{"uid": candidate["uid"], "sides": {"p1": 1, "p2": 1}}
                             for candidate in farm_candidates]
                digest = write_text(path, "\n".join(json.dumps(row, sort_keys=True) for row in decisions) + "\n")
            else:
                digest = write_text(path, json.dumps({"synthetic_fixture": f"{pool}/{role}"}) + "\n")
            result[(pool, role)] = {"pool": pool, "role": role, "path": rel, "sha256": digest}
        identity = {"pool": "hr_val", "games": result[("hr_val", "games")],
                    "decisions": result[("hr_val", "decisions")]}
        manifest_tools.CRAWL_PINS.update({role: result[("crawl", role)]["sha256"]
                                          for role in manifest_tools.EXPECTED_CRAWL_SOURCES})
        review_source_hashes = {}
        for rel in manifest_tools.REVIEW_SOURCE_PATHS:
            review_source_hashes[rel] = write_bytes(self.root / rel, (SOURCE_ROOT / rel).read_bytes())
        return list(result.values()), result, identity, review_source_hashes

    @staticmethod
    def _crawl_candidates(count=10):
        values, n = [], 0
        while len(values) < count:
            uid = f"synthetic-crawl-{n}"
            n += 1
            if manifest_tools.calib_b.half(uid) != 1:
                continue
            values.append({"pool": "crawl", "uid": uid, "side": "p1", "result": "win",
                           "decided": True, "turns": 12, "empty_context": len(values) == 0})
        return values

    @staticmethod
    def _farm_candidates(count=10):
        return [{"pool": "hr_val", "uid": f"synthetic-farm-{i}", "side": "p1",
                 "result": "loss" if i % 2 else "win", "decided": True, "turns": 13,
                 "both_seats_eligible": True} for i in range(count)]

    @staticmethod
    def _review(_candidate):
        return json.loads(json.dumps(REVIEW))

    @staticmethod
    def _context(candidate, _review):
        ctx = json.loads(json.dumps(CTX))
        if candidate.get("empty_context") or candidate.get("source_record_index") == 0:
            ctx["active"] = {"2:0": "Iron Valiant"}
        return ctx

    def build_manifest(self):
        crawl_candidates = self._crawl_candidates()
        farm_candidates = self._farm_candidates()
        sources, source_by_key, farm_identity, review_source_hashes = self._source_hashes(
            crawl_candidates, farm_candidates)
        import app.pipeline as pipeline

        artifact_rows = []
        selections = {}
        for pool in manifest_tools.POOLS:
            candidates = manifest_tools.enumerate_source_candidates(pool, sources, root=self.root)
            selected = manifest_tools.select_candidates(
                candidates, pool, self._review, self._context, limit=10,
                farm_source_identity=farm_identity if pool == "hr_val" else None,
                offered_fn=lambda review, ctx: pipeline.build_view_with_offered(review, ctx)[1],
                review_code_commit="synthetic-review-commit",
                review_source_sha256=review_source_hashes)
            selections[pool] = selected["attempt_evidence"]
            for index, candidate in enumerate(selected["selected"]):
                prefix = f"scratch_ds/evals/{pool}-{index:02d}"
                replay_rel = prefix + ".replay.json"
                replay_hash = write_json(self.root / replay_rel, {"log": ["synthetic replay fixture"]})
                row = manifest_tools.build_artifact_row(
                    candidate, source_path=candidate["source_path"], source_sha256=candidate["source_sha256"],
                    replay_path=replay_rel, replay_sha256=replay_hash,
                    review_path=prefix + ".review.json", ctx_path=prefix + ".ctx.json",
                    offered_path=prefix + ".offered.json", review_code_commit="synthetic-review-commit",
                    active_calibrator="tools/review/calibrator.json#absolute_human@d4a8b2580ca101bc6f55a8900cb716db59d04d78e0636c6843ed135d6b5642a9", root=self.root,
                    offered_fn=lambda review, ctx: pipeline.build_view_with_offered(review, ctx)[1])
                verdict_rel = prefix + ".verdicts.json"
                verdict_hash = write_json(self.root / verdict_rel,
                                          {"2:0": {"verdict": "visible synthetic verdict"},
                                           "3:0": {"verdict": "hidden synthetic verdict"}})
                team_rel = prefix + ".team.txt"
                team_hash = write_text(self.root / team_rel, "synthetic team")
                row["team_path"] = team_rel
                row["team_sha256"] = team_hash
                row["verdicts_path"] = verdict_rel
                row["verdicts_sha256"] = verdict_hash
                artifact_rows.append(row)
        rows = [r for r in artifact_rows if r["pool"] == "crawl"] + \
               [r for r in artifact_rows if r["pool"] == "hr_val"]
        manifest = {"schema": "c9-selection-manifest-v1", "rows": rows, "sources": sources,
                    "candidate_census": {pool: manifest_tools.enumerate_source_candidates(
                        pool, sources, root=self.root) for pool in manifest_tools.POOLS},
                    "selection_evidence": selections,
                    "farm_source_identity": farm_identity, "review_code_commit": "synthetic-review-commit",
                    "review_source_sha256": review_source_hashes,
                    "active_calibrator": "tools/review/calibrator.json#absolute_human@d4a8b2580ca101bc6f55a8900cb716db59d04d78e0636c6843ed135d6b5642a9",
                    "fork_commit": "synthetic-fork-commit"}
        manifest_rel = "scratch_ds/evals/synthetic-manifest.json"
        write_json(self.root / manifest_rel, manifest)
        a1_doc, a0_doc = manifest_tools._manifest_items(manifest_rel, manifest)
        write_json(self.root / "scratch_ds/evals/synthetic-a1-items.json", a1_doc)
        write_json(self.root / "scratch_ds/evals/synthetic-a0-items.json", a0_doc)
        return manifest, a1_doc, a0_doc


class ConstructorSourceBindingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hardread-c9-source-binding-")
        self.root = Path(self._tmp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Synthetic C9 Test")
        self._git("config", "user.email", "c9-test@example.invalid")
        self.hashes = {}
        for rel in manifest_tools.REVIEW_SOURCE_PATHS:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = ("synthetic constructor source: " + rel + "\n").encode()
            path.write_bytes(raw)
            self.hashes[rel] = hashlib.sha256(raw).hexdigest()
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "synthetic constructor source")
        self.commit = self._git_text("rev-parse", "HEAD")
        self.manifest = {"review_code_commit": "synthetic-review-code",
                         "review_source_sha256": dict(self.hashes)}

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.root, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=check)

    def _git_text(self, *args):
        return self._git(*args).stdout.decode().strip()

    def _frozen_constants(self, manifest=None, *, commit=None):
        manifest = manifest or self.manifest
        return [
            patch.object(manifest_tools, "FROZEN_C9_V1_MANIFEST_DIGEST",
                         manifest_tools.manifest_digest(manifest)),
            patch.object(manifest_tools, "FROZEN_C9_V1_REVIEW_CODE_COMMIT",
                         manifest["review_code_commit"]),
            patch.object(manifest_tools, "FROZEN_C9_V1_CONSTRUCTOR_COMMIT", commit or self.commit),
            patch.object(manifest_tools, "FROZEN_C9_V1_M_SHA256",
                         manifest["review_source_sha256"]["tools/agent/build_eval_manifest.py"]),
        ]

    def _assert_frozen_binding(self, manifest=None):
        manifest = manifest or self.manifest
        patches = self._frozen_constants(self.manifest)
        for item in patches:
            item.start()
        try:
            manifest_tools._verify_frozen_c9_v1_source_binding(
                manifest, manifest["review_source_sha256"], root=self.root)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_historical_blob_helper_reads_exact_bytes_and_rejects_wrong_or_unknown_refs(self):
        manifest_tools._verify_historical_constructor_sources(
            self.hashes, root=self.root, constructor_commit=self.commit)
        changed = dict(self.hashes)
        changed["tools/review/review.py"] = "0" * 64
        with self.assertRaises(manifest_tools.ManifestError):
            manifest_tools._verify_historical_constructor_sources(
                changed, root=self.root, constructor_commit=self.commit)
        with self.assertRaises(manifest_tools.ManifestError):
            manifest_tools._verify_historical_constructor_sources(
                self.hashes, root=self.root, constructor_commit="f" * 40)

    def test_missing_historical_blob_fails_closed(self):
        rel = "tools/review/review.py"
        self._git("rm", "-q", rel)
        self._git("commit", "-q", "-m", "remove a synthetic historical object")
        missing_blob_commit = self._git_text("rev-parse", "HEAD")
        with self.assertRaises(manifest_tools.ManifestError):
            manifest_tools._verify_historical_constructor_sources(
                self.hashes, root=self.root, constructor_commit=missing_blob_commit)

    def test_frozen_binding_rejects_changed_scope_commit_and_source_map(self):
        self._assert_frozen_binding()
        for mutate in (
            lambda m: m.__setitem__("review_code_commit", "wrong-review-commit"),
            lambda m: m["review_source_sha256"].__setitem__("tools/review/review.py", "0" * 64),
        ):
            altered = copy.deepcopy(self.manifest)
            mutate(altered)
            with self.assertRaises(manifest_tools.ManifestError):
                self._assert_frozen_binding(altered)
        attempt_hashes = dict(self.hashes)
        attempt_hashes["tools/review/review.py"] = "0" * 64
        patches = self._frozen_constants(self.manifest)
        for item in patches:
            item.start()
        try:
            with self.assertRaises(manifest_tools.ManifestError):
                manifest_tools._verify_frozen_c9_v1_source_binding(
                    self.manifest, attempt_hashes, root=self.root)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_frozen_binding_rejects_a_changed_constructor_hash_and_dirty_m(self):
        altered = copy.deepcopy(self.manifest)
        altered["review_source_sha256"]["tools/agent/build_eval_manifest.py"] = "0" * 64
        patches = self._frozen_constants(altered)
        # Keep the pinned M hash fixed as it is in production while updating only
        # the test's manifest digest boundary.
        patches[-1] = patch.object(manifest_tools, "FROZEN_C9_V1_M_SHA256",
                                   self.hashes["tools/agent/build_eval_manifest.py"])
        for item in patches:
            item.start()
        try:
            with self.assertRaises(manifest_tools.ManifestError):
                manifest_tools._verify_frozen_c9_v1_source_binding(
                    altered, altered["review_source_sha256"], root=self.root)
        finally:
            for item in reversed(patches):
                item.stop()

        m_path = self.root / "tools/agent/build_eval_manifest.py"
        original = m_path.read_bytes()
        m_path.write_bytes(original + b"dirty")
        with self.assertRaises(manifest_tools.ManifestError):
            self._assert_frozen_binding()
        m_path.write_bytes(original + b"staged")
        self._git("add", "tools/agent/build_eval_manifest.py")
        m_path.write_bytes(original)
        with self.assertRaises(manifest_tools.ManifestError):
            self._assert_frozen_binding()

    def test_every_other_live_source_remains_hash_checked(self):
        self._assert_frozen_binding()
        for rel in manifest_tools.REVIEW_SOURCE_PATHS:
            if rel == "tools/agent/build_eval_manifest.py":
                continue
            path = self.root / rel
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(path=rel):
                with self.assertRaises(manifest_tools.ManifestError):
                    self._assert_frozen_binding()
            path.write_bytes(original)

    def test_actual_frozen_manifest_binds_fb101_blobs_not_review_code_commit(self):
        source_root = SOURCE_ROOT
        manifest_path = source_root / "scratch_ds/evals/c9-freeze-fb10153-v1/selection_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_tools.manifest_digest(manifest),
                         manifest_tools.FROZEN_C9_V1_MANIFEST_DIGEST)
        self.assertEqual(manifest["review_code_commit"],
                         manifest_tools.FROZEN_C9_V1_REVIEW_CODE_COMMIT)
        manifest_tools._verify_frozen_c9_v1_source_binding(
            manifest, manifest["review_source_sha256"], root=source_root)
        review_commit_m = manifest_tools._git_blob_sha256(
            manifest["review_code_commit"], manifest_tools.FROZEN_C9_V1_M_PATH, root=source_root)
        self.assertNotEqual(review_commit_m, manifest_tools.FROZEN_C9_V1_M_SHA256)


class SelectionAndManifestTests(SyntheticWorkspace):
    def _rebuild_items(self, manifest):
        manifest_path = "scratch_ds/evals/synthetic-manifest.json"
        write_json(self.root / manifest_path, manifest)
        return manifest_tools._manifest_items(manifest_path, manifest)

    def _change_crawl_games(self, manifest, mutate):
        source = next(row for row in manifest["sources"] if row["pool"] == "crawl" and row["role"] == "games")
        path = self.root / source["path"]
        records = [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines() if line.strip()]
        mutate(records)
        digest = write_bytes(path, gzip.compress(("\n".join(json.dumps(row, sort_keys=True) for row in records)
                                                   + "\n").encode()))
        source["sha256"] = digest
        manifest_tools.CRAWL_PINS["games"] = digest
        for row in manifest["rows"]:
            if row["pool"] == "crawl":
                row["source_sha256"] = digest
        return path

    def test_selection_is_ranked_unique_and_review_errors_are_counted(self):
        candidates = self._crawl_candidates(3)
        candidates[0]["side"] = "p2"
        candidates.extend([
            {"pool": "crawl", "uid": "synthetic-undecided", "side": "p1", "result": "tie",
             "decided": False, "turns": 12},
            {"pool": "crawl", "uid": "synthetic-short", "side": "p1", "result": "win",
             "decided": True, "turns": 9},
            {"pool": "crawl", "uid": "synthetic-invalid", "side": "bad", "result": "win",
             "decided": True, "turns": 12},
        ])
        ordered = sorted(candidates[:3], key=lambda row: manifest_tools.rank_hash(row["uid"], row["side"]))
        fail_uid = ordered[0]["uid"]
        attempted = []

        def review(row):
            attempted.append((row["uid"], row["side"]))
            if row["uid"] == fail_uid:
                raise RuntimeError("synthetic review error")
            return REVIEW

        with self.assertRaisesRegex(manifest_tools.ManifestError, "exception provenance"):
            manifest_tools.select_candidates(candidates, "crawl", review, self._context, limit=2,
                                              offered_fn=lambda _review, _ctx: [])
        self.assertEqual(len(attempted), 1)
        chosen = manifest_tools.select_candidates(candidates, "crawl", self._review, self._context, limit=2,
                                                   offered_fn=lambda _review, _ctx: [])
        shuffled = manifest_tools.select_candidates(list(reversed(candidates)), "crawl", self._review,
                                                     self._context, limit=2, offered_fn=lambda _review, _ctx: [])
        self.assertEqual([r["uid"] for r in chosen["selected"]], [r["uid"] for r in shuffled["selected"]])

    def test_one_side_per_game_and_context_failure_is_fatal(self):
        candidate = self._crawl_candidates(1)[0]
        candidates = [dict(candidate, side="p1"), dict(candidate, side="p2")]
        result = manifest_tools.select_candidates(candidates, "crawl", self._review, self._context,
                                                  limit=2, offered_fn=lambda _review, _ctx: [])
        self.assertEqual(len(result["selected"]), 1)
        self.assertEqual(result["duplicate_sides_skipped"], 1)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "after successful review"):
            manifest_tools.select_candidates([candidate, self._crawl_candidates(1)[0]], "crawl", self._review,
                                              lambda *_args: (_ for _ in ()).throw(RuntimeError("context")),
                                              limit=1, offered_fn=lambda _review, _ctx: [])

    def test_hr_val_identity_is_fail_closed(self):
        candidate = {"pool": "hr_val", "uid": "val-n1", "side": "p1", "result": "win",
                     "decided": True, "turns": 12, "both_seats_eligible": True}
        with self.assertRaisesRegex(manifest_tools.SourceUnresolvedError, "source unresolved"):
            manifest_tools.select_candidates([candidate], "hr_val", self._review, self._context)

    def test_frozen_manifest_loaders_keep_empty_offered_and_scope_cached_verdicts(self):
        manifest, a1_doc, a0_doc = self.build_manifest()
        validated, digest, a1_items = manifest_tools.load_validated_a1(a1_doc, root=self.root)
        _manifest, _pool, a0_digest, a0_items = self._load_a0(a0_doc)
        self.assertEqual(validated, manifest)
        self.assertEqual(digest, a1_doc["manifest_sha256"])
        self.assertEqual(a0_digest, a0_doc["manifest_sha256"])
        self.assertEqual(len(a1_items), 60)
        self.assertEqual(len(a0_items), 30)
        empty_item = next(item for item in a1_items if item["offered_data"] == [])
        self.assertEqual(empty_item["visible_verdicts"], {})
        visible_item = next(item for item in a1_items if item["offered_data"])
        self.assertEqual(set(visible_item["visible_verdicts"]), {"2:0"})
        self.assertEqual(manifest_tools.keys_for_offered([]), [])

    def _load_a0(self, document):
        manifest, per_pool, digest = manifest_tools.validate_items_document(document, "a0", root=self.root)
        items = manifest_tools.load_validated_a0(document, root=self.root)[2]
        return manifest, per_pool, digest, items

    def test_frozen_artifact_mutation_and_output_overwrite_are_rejected(self):
        manifest, a1_doc, _a0_doc = self.build_manifest()
        ctx_path = self.root / manifest["rows"][0]["ctx_path"]
        ctx_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(manifest_tools.ManifestError, "hash changed"):
            manifest_tools.validate_items_document(a1_doc, "a1", root=self.root)
        target = self.eval_dir / "reserved.json"
        target.write_text("synthetic reservation\n", encoding="utf-8")
        with self.assertRaisesRegex(manifest_tools.ManifestError, "overwrite"):
            manifest_tools.validate_record_output_paths(target, root=self.root)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "private"):
            manifest_tools.validate_record_output_paths(self.root / "outside.json", root=self.root)

    def test_raw_result_turn_census_mutations_and_earlier_or_duplicate_games_are_rejected(self):
        manifest, _a1_doc, _a0_doc = self.build_manifest()
        original_source = self.root / next(row["path"] for row in manifest["sources"]
                                           if row["pool"] == "crawl" and row["role"] == "games")
        original_bytes = original_source.read_bytes()
        original_manifest = copy.deepcopy(manifest)

        mutations = (
            ("raw result", lambda records: records[0].update(winner="B"), "candidate census"),
            ("raw turn count", lambda records: records[0].update(
                log=[f"|turn|{turn}" for turn in range(1, 10)]), "candidate census"),
            ("duplicate game", lambda records: records.append(copy.deepcopy(records[0])), "duplicate game UIDs"),
        )
        for label, mutate, error in mutations:
            original_source.write_bytes(original_bytes)
            manifest = copy.deepcopy(original_manifest)
            self._change_crawl_games(manifest, mutate)
            a1_doc, _a0_doc = self._rebuild_items(manifest)
            with self.subTest(label=label), self.assertRaisesRegex(manifest_tools.ManifestError, error):
                manifest_tools.validate_items_document(a1_doc, "a1", root=self.root)

        original_source.write_bytes(original_bytes)
        manifest = copy.deepcopy(original_manifest)
        def add_earlier(records):
            min_rank = min(row["rank_hash"] for row in manifest["rows"] if row["pool"] == "crawl")
            index = 0
            while True:
                uid = f"synthetic-earlier-{index}"
                if (manifest_tools.calib_b.half(uid) == 1
                        and manifest_tools.rank_hash(uid, "p1") < min_rank):
                    break
                index += 1
            records.append({"uid": uid, "winner": "A", "turns": 10,
                            "log": [f"|turn|{turn}" for turn in range(1, 11)]})
        self._change_crawl_games(manifest, add_earlier)
        a1_doc, _a0_doc = self._rebuild_items(manifest)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "candidate census"):
            manifest_tools.validate_items_document(a1_doc, "a1", root=self.root)

    def test_mutated_selected_side_and_attempt_exception_or_substitution_are_rejected(self):
        manifest, _a1_doc, _a0_doc = self.build_manifest()
        original = copy.deepcopy(manifest)
        row = next(item for item in manifest["rows"] if item["pool"] == "crawl")
        row["side"] = "p2" if row["side"] == "p1" else "p1"
        row["rank_hash"] = manifest_tools.rank_hash(row["uid"], row["side"])
        a1_doc, _a0_doc = self._rebuild_items(manifest)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "census"):
            manifest_tools.validate_items_document(a1_doc, "a1", root=self.root)

        manifest.clear()
        manifest.update(copy.deepcopy(original))
        evidence = manifest["selection_evidence"]["crawl"]
        first = evidence["attempts"][0]
        first["outcome"] = "review_exception"
        first["exception_type"] = "RuntimeError"
        first.pop("selected_row_sha256")
        evidence["attempts_sha256"] = manifest_tools.digest_json(evidence["attempts"])
        a1_doc, _a0_doc = self._rebuild_items(manifest)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "exception attempt"):
            manifest_tools.validate_items_document(a1_doc, "a1", root=self.root)

        manifest.clear()
        manifest.update(copy.deepcopy(original))
        evidence = manifest["selection_evidence"]["crawl"]
        later_selected = next(attempt for attempt in evidence["attempts"][1:]
                              if attempt["outcome"] == "selected")
        evidence["attempts"][0]["selected_row_sha256"] = later_selected["selected_row_sha256"]
        evidence["attempts_sha256"] = manifest_tools.digest_json(evidence["attempts"])
        a1_doc, _a0_doc = self._rebuild_items(manifest)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "selected attempt"):
            manifest_tools.validate_items_document(a1_doc, "a1", root=self.root)

    def test_forged_rehashed_earlier_exception_is_pending_before_either_client(self):
        manifest, _a1_doc, _a0_doc = self.build_manifest()
        crawl_rows = [row for row in manifest["rows"] if row["pool"] == "crawl"]
        first_rank = min(row["rank_hash"] for row in crawl_rows)
        last_rank = max(row["rank_hash"] for row in crawl_rows)
        earlier_uid = None
        for index in range(100000):
            candidate_uid = f"synthetic-forged-exception-{index}"
            if (manifest_tools.calib_b.half(candidate_uid) == 1
                    and manifest_tools.rank_hash(candidate_uid, "p1") < first_rank
                    and manifest_tools.rank_hash(candidate_uid, "p2") > last_rank):
                earlier_uid = candidate_uid
                break
        self.assertIsNotNone(earlier_uid)

        def append_earlier_game(records):
            records.append({"uid": earlier_uid, "winner": "A", "turns": 10,
                            "log": [f"|turn|{turn}" for turn in range(1, 11)]})

        self._change_crawl_games(manifest, append_earlier_game)
        manifest["candidate_census"]["crawl"] = manifest_tools.enumerate_source_candidates(
            "crawl", manifest["sources"], root=self.root)
        crawl_census = manifest["candidate_census"]["crawl"]
        rows_by_identity = {(row["pool"], row["uid"], row["side"]): row
                            for row in manifest["rows"] if row["pool"] == "crawl"}
        for identity, row in rows_by_identity.items():
            source_candidate = next(item for item in crawl_census
                                    if (item["pool"], item["uid"], item["side"]) == identity)
            review = json.loads((self.root / row["review_path"]).read_text(encoding="utf-8"))
            ctx = json.loads((self.root / row["ctx_path"]).read_text(encoding="utf-8"))
            offered = json.loads((self.root / row["offered_path"]).read_text(encoding="utf-8"))
            row["selection_sha256"] = manifest_tools._selected_attempt_digest(
                source_candidate, review, ctx, offered)
        candidate = next(row for row in manifest["candidate_census"]["crawl"]
                         if row["uid"] == earlier_uid and row["side"] == "p1")
        evidence = manifest["selection_evidence"]["crawl"]
        for attempt in evidence["attempts"]:
            source_candidate = next(item for item in crawl_census
                                   if (item["pool"], item["uid"], item["side"])
                                   == ("crawl", attempt["uid"], attempt["side"]))
            attempt["candidate_sha256"] = manifest_tools.digest_json(
                manifest_tools._source_identity(source_candidate))
            if attempt.get("outcome") == "selected":
                identity = ("crawl", attempt["uid"], attempt["side"])
                attempt["selected_row_sha256"] = rows_by_identity[identity]["selection_sha256"]
        forged_receipt = {"candidate_sha256": manifest_tools.digest_json(
            manifest_tools._source_identity(candidate)), "outcome": "review_exception",
                          "exception_type": "RuntimeError", "trace_sha256": "f" * 64}
        forged_attempt = {
            "pool": "crawl", "uid": earlier_uid, "side": "p1",
            "rank_hash": candidate["rank_hash"],
            "candidate_sha256": manifest_tools.digest_json(manifest_tools._source_identity(candidate)),
            "review_code_commit": evidence["review_code_commit"],
            "review_source_sha256": evidence["review_source_sha256"],
            "outcome": "review_exception", "exception_type": "RuntimeError",
            # Rehashed, self-asserted receipt/approval fields must never authorize a skip.
            "receipt": forged_receipt, "receipt_sha256": manifest_tools.digest_json(forged_receipt),
            "approved": True,
        }
        evidence["attempts"].insert(0, forged_attempt)
        evidence["attempts_sha256"] = manifest_tools.digest_json(evidence["attempts"])
        a1_doc, a0_doc = self._rebuild_items(manifest)
        a1_path = self.eval_dir / "forged-exception-a1-items.json"
        a0_path = self.eval_dir / "forged-exception-a0-items.json"
        write_json(a1_path, a1_doc)
        write_json(a0_path, a0_doc)

        for module, item_path, kind in ((eval_a1, a1_path, "a1"), (eval_a0, a0_path, "a0")):
            original_loader = getattr(manifest_tools, "load_validated_a1" if kind == "a1"
                                      else "load_validated_a0")
            bomb = SimpleNamespace(health=lambda: self.fail("health called for pending exception provenance"),
                                   chat=lambda *_args, **_kwargs: self.fail(
                                       "chat called for pending exception provenance"))
            args = ["--items", str(item_path), "--server-provenance",
                    str(self.root / "unused-server-provenance.json"),
                    "--out", f"scratch_ds/evals/forged-exception-{kind}-out.json"]
            with patch.object(module.manifest_tools,
                              "load_validated_a1" if kind == "a1" else "load_validated_a0",
                              side_effect=lambda document, fn=original_loader: fn(document, root=self.root)), \
                    patch.object(module, "GlimmerClient", return_value=bomb) as client_ctor, \
                    patch.object(sys, "argv", [module.__file__, *args]):
                with self.assertRaisesRegex(manifest_tools.ManifestError,
                                            "candidate exception provenance is pending"):
                    module.main()
                client_ctor.assert_not_called()

    def test_missing_candidate_attempt_gap_is_rejected_before_either_client(self):
        manifest, _a1_doc, _a0_doc = self.build_manifest()
        evidence = manifest["selection_evidence"]["crawl"]
        evidence["attempts"].pop(0)
        evidence["attempts_sha256"] = manifest_tools.digest_json(evidence["attempts"])
        a1_doc, a0_doc = self._rebuild_items(manifest)
        a1_path = self.eval_dir / "gap-a1-items.json"
        a0_path = self.eval_dir / "gap-a0-items.json"
        write_json(a1_path, a1_doc)
        write_json(a0_path, a0_doc)

        for module, item_path, kind in ((eval_a1, a1_path, "a1"), (eval_a0, a0_path, "a0")):
            loader_name = "load_validated_a1" if kind == "a1" else "load_validated_a0"
            original_loader = getattr(manifest_tools, loader_name)
            bomb = SimpleNamespace(health=lambda: self.fail("health called after attempt-gap rejection"),
                                   chat=lambda *_args, **_kwargs: self.fail("chat called after attempt-gap rejection"))
            args = ["--items", str(item_path), "--server-provenance",
                    str(self.root / "unused-server-provenance.json"),
                    "--out", f"scratch_ds/evals/gap-{kind}-out.json"]
            with patch.object(module.manifest_tools, loader_name,
                              side_effect=lambda document, fn=original_loader: fn(document, root=self.root)), \
                    patch.object(module, "GlimmerClient", return_value=bomb) as client_ctor, \
                    patch.object(sys, "argv", [module.__file__, *args]):
                with self.assertRaisesRegex(manifest_tools.ManifestError,
                                            "attempt order or candidate source binding changed"):
                    module.main()
                client_ctor.assert_not_called()

    def test_raw_source_census_rejection_happens_before_client_health_or_chat(self):
        manifest, _a1_doc, _a0_doc = self.build_manifest()
        self._change_crawl_games(manifest, lambda records: records[0].update(winner="B"))
        a1_doc, a0_doc = self._rebuild_items(manifest)
        a1_path, a0_path = self.eval_dir / "bad-a1-items.json", self.eval_dir / "bad-a0-items.json"
        write_json(a1_path, a1_doc)
        write_json(a0_path, a0_doc)
        missing_provenance = self.root / "not-created-provenance.json"
        for module, item_path, loader_name, kind in (
                (eval_a1, a1_path, "load_validated_a1", "a1"),
                (eval_a0, a0_path, "load_validated_a0", "a0")):
            original_loader = getattr(manifest_tools, loader_name)
            bomb = SimpleNamespace(health=lambda: self.fail("health called after raw census rejection"),
                                   chat=lambda *_args, **_kwargs: self.fail("chat called after raw census rejection"))
            args = ["--items", str(item_path), "--server-provenance", str(missing_provenance),
                    "--out", f"scratch_ds/evals/should-not-write-{kind}.json"]
            with patch.object(module.manifest_tools, loader_name,
                              side_effect=lambda document, fn=original_loader: fn(document, root=self.root)), \
                    patch.object(module, "GlimmerClient", return_value=bomb) as client_ctor, \
                    patch.object(sys, "argv", [module.__file__, *args]):
                with self.assertRaisesRegex(manifest_tools.ManifestError, "candidate census"):
                    module.main()
                client_ctor.assert_not_called()


class AppOfferedParityTests(unittest.TestCase):
    def test_empty_and_visible_decision_cache_scope_match_app_policy(self):
        import app.pipeline as pipeline
        from app.server import _visible_verdicts

        view, offered = pipeline.build_view_with_offered(REVIEW, CTX)
        self.assertEqual([{"turn": x["turn"], "k": x["k"]} for x in offered],
                         [{"turn": 2, "k": 0}])
        self.assertEqual([(x["turn"], x["k"]) for x in view["disagreements"]], [(2, 0)])
        hidden_ctx = dict(CTX, active={"2:0": "Iron Valiant"})
        _hidden_view, empty_offered = pipeline.build_view_with_offered(REVIEW, hidden_ctx)
        self.assertEqual(empty_offered, [])
        scoped = _visible_verdicts(offered, {"2:0": {"verdict": "visible"},
                                             "2:1": {"verdict": "wrong k"},
                                             "3:0": {"verdict": "wrong turn"}})
        self.assertEqual(set(scoped), {"2:0"})
        hidden = _visible_verdicts(empty_offered, {"2:0": {"verdict": "cached but not shown"}})
        self.assertEqual(hidden, {})

        session = ToolSession(REVIEW, verdicts={"2:0": {"verdict": "cached"}}, offered=empty_offered)
        self.assertEqual(session.call("list_key_moments", {"kind": "disagreement"})["moments"], [])
        self.assertNotIn("gap_pts", session.call("get_turn", {"turn": 2}))
        self.assertNotIn("verdict", session.call("get_verdict", {"turn": 2, "k": 0}))
        self.assertEqual(manifest_tools.keys_for_offered([]), [])


class LockAndPreflightTests(SyntheticWorkspace):
    def test_lock_is_exclusive_and_cleanup_only_removes_owned_lock(self):
        lock = self.root / "private" / "EVAL_LOCK"
        with eval_lock("synthetic fixture", 30, lock_path=lock) as first_line:
            self.assertEqual(lock.read_text(encoding="utf-8").strip(), first_line)
            with self.assertRaisesRegex(RuntimeError, "already present"):
                with eval_lock("second fixture", 30, lock_path=lock):
                    self.fail("a competing synthetic lock should not be acquired")
            replacement = "replacement owner\n"
            lock.write_text(replacement, encoding="utf-8")
        self.assertEqual(lock.read_text(encoding="utf-8"), replacement)

    def test_recorded_a0_a1_invalid_manifest_never_constructs_or_calls_client(self):
        bad_items = self.root / "bad-items.json"
        bad_items.write_text("{}\n", encoding="utf-8")
        bad_provenance = self.root / "missing-provenance.json"
        for module, args in ((eval_a1, ["--items", str(bad_items), "--server-provenance", str(bad_provenance),
                                       "--out", "scratch_ds/evals/synthetic-a1-out.json"]),
                             (eval_a0, ["--items", str(bad_items), "--server-provenance", str(bad_provenance),
                                       "--out", "scratch_ds/evals/synthetic-a0-out.json"])):
            bomb = SimpleNamespace(health=lambda: self.fail("health called before invalid manifest was rejected"),
                                   chat=lambda *_args, **_kwargs: self.fail("chat called before invalid manifest was rejected"))
            with patch.object(module, "GlimmerClient", return_value=bomb) as client_ctor, \
                 patch.object(sys, "argv", [module.__file__, *args]):
                with self.assertRaises(manifest_tools.ManifestError):
                    module.main()
                client_ctor.assert_not_called()

    def test_server_provenance_rejects_missing_actual_evidence_without_process_probe(self):
        provenance = self.root / "synthetic-provenance.json"
        write_json(provenance, {"gguf_sha256": "0" * 64})
        with self.assertRaisesRegex(manifest_tools.ManifestError, "required actual-evidence"):
            manifest_tools.validate_server_provenance(str(provenance), root=self.root,
                                                      process_probe=lambda _pid: self.fail("process probe called"))

    def test_structured_live_process_identity_and_exact_argv_are_fail_closed(self):
        executable = self.root / "bin" / "llama-server"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"synthetic executable bytes")
        model = self.root / "base.gguf"
        model.write_bytes(b"synthetic model bytes")
        flags = ["--ctx-size", "32768", "--jinja", "--reasoning-format", "deepseek", "--swa-full",
                 "--gpu-layers", "99", "--parallel", "1"]
        argv = [str(executable), "--model", str(model), "--host", "127.0.0.1", "--port", "18131", *flags]
        started = "2026-10-27T17:00:00Z"
        process = {"pid": 31415, "host": "synthetic-host", "argv": argv,
                   "executable_path": str(executable),
                   "executable_sha256": manifest_tools.digest_file(executable), "started_utc": started}
        data = {"pid": 31415, "host": "synthetic-host", "server_argv": argv,
                "executable_path": str(executable), "executable_sha256": process["executable_sha256"],
                "process_started_utc": started, "server_flags": flags,
                "evidence": {"model": {"model_path": str(model)}}}
        self.assertEqual(manifest_tools._validate_live_process(
            data, process, process_probe=lambda _pid: dict(process)), str(model))

        mutations = (
            ("pid", lambda live: live.update(pid=99999)),
            ("start", lambda live: live.update(started_utc="2026-10-27T17:01:00Z")),
            ("argv flag", lambda live: live["argv"].__setitem__(live["argv"].index("32768"), "16384")),
            ("extra arg", lambda live: live["argv"].append("--unknown-flag")),
            ("executable", lambda live: live.update(executable_path=str(model))),
        )
        for label, mutate in mutations:
            live = dict(process, argv=list(argv))
            mutate(live)
            with self.subTest(label=label), self.assertRaises(manifest_tools.ManifestError):
                manifest_tools._validate_live_process(data, process,
                                                      process_probe=lambda _pid, value=live: value)

        with self.assertRaisesRegex(manifest_tools.ManifestError, "structured OS snapshot"):
            manifest_tools._validate_live_process(data, process, process_probe=lambda _pid: "ps text")

    def _server_process_fixture(self, argv):
        executable = self.root / "bin" / "llama-server"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"synthetic executable bytes")
        model = self.root / "base.gguf"
        model.write_bytes(b"synthetic model bytes")
        argv = list(argv)
        argv[0] = str(executable)
        argv[argv.index("--model") + 1] = str(model)
        started = "2026-10-27T17:00:00Z"
        process = {"pid": 31415, "host": "synthetic-host", "argv": argv,
                   "executable_path": str(executable),
                   "executable_sha256": manifest_tools.digest_file(executable), "started_utc": started}
        flags, i = [], 1
        launch = {"--model", "--host", "--port"}
        boolean = {"--jinja", "--swa-full", "--no-webui"}
        while i < len(argv):
            if argv[i] in launch:
                i += 2
            elif argv[i] in boolean:
                flags.append(argv[i]); i += 1
            else:
                flags.extend(argv[i:i + 2]); i += 2
        data = {"pid": process["pid"], "host": process["host"], "server_argv": argv,
                "server_flags": flags, "executable_path": process["executable_path"],
                "executable_sha256": process["executable_sha256"], "process_started_utc": started,
                "evidence": {"model": {"model_path": str(model)}}}
        return executable, model, data, process, flags

    def _mixed_exact_server_argv(self):
        return ["unused-executable", "--alias", "muse-glimmer", "--ctx-size", "32768",
                "--model", "unused-model", "--temp", "0", "--top-k", "40", "--host", "127.0.0.1",
                "--jinja", "--reasoning-format", "deepseek", "--top-p", "1", "--swa-full",
                "--seed", "42", "--gpu-layers", "99", "--chat-template-kwargs",
                '{"reasoning_strength":"low"}', "--parallel", "1", "--no-webui", "--port", "18131"]

    def test_mixed_order_full_extras_and_core_only_permutations_pass(self):
        full_argv = self._mixed_exact_server_argv()
        _exe, _model, data, process, expected_flags = self._server_process_fixture(full_argv)
        model_path = manifest_tools._validate_live_process(
            data, process, process_probe=lambda _pid: dict(process))
        self.assertEqual(model_path, data["evidence"]["model"]["model_path"])
        self.assertEqual(data["server_flags"], expected_flags)
        launch, parsed_flags = manifest_tools._parse_server_argv(process["argv"])
        self.assertEqual(launch["--host"], "127.0.0.1")
        self.assertEqual(launch["--port"], "18131")
        self.assertEqual(parsed_flags, expected_flags)

        permuted_subset = ["unused-executable", "--parallel", "1", "--model", "unused-model",
                           "--gpu-layers", "99", "--host", "localhost", "--swa-full",
                           "--reasoning-format", "deepseek", "--ctx-size", "32768", "--port", "18131",
                           "--jinja", "--temp", "0"]
        _exe, _model, subset_data, subset_process, subset_flags = self._server_process_fixture(permuted_subset)
        manifest_tools._validate_live_process(
            subset_data, subset_process, process_probe=lambda _pid: dict(subset_process))
        self.assertEqual(subset_data["server_flags"], subset_flags)
        self.assertEqual(subset_flags, ["--parallel", "1", "--gpu-layers", "99", "--swa-full",
                                        "--reasoning-format", "deepseek", "--ctx-size", "32768",
                                        "--jinja", "--temp", "0"])

    def test_each_invalid_server_option_fails_closed(self):
        valid = self._mixed_exact_server_argv()
        value_mutations = {
            "alias": ("--alias", "muse-glimmer-v2"), "temperature": ("--temp", "0.1"),
            "top_k": ("--top-k", "41"), "top_p": ("--top-p", "0.9"), "seed": ("--seed", "43"),
            "template_kwargs": ("--chat-template-kwargs", '{"reasoning_strength":"high"}'),
        }
        bad_cases = []
        for label, (option, wrong_value) in value_mutations.items():
            bad = list(valid); bad[bad.index(option) + 1] = wrong_value; bad_cases.append((label, bad))
        bad = list(valid); bad.extend(("--lora", "adapter.gguf")); bad_cases.append(("lora", bad))
        bad = list(valid); bad.extend(("--unknown", "value")); bad_cases.append(("unknown", bad))
        bad = list(valid); bad.extend(("--ctx-size", "32768")); bad_cases.append(("duplicate_core", bad))
        bad = list(valid); bad.extend(("--temp", "0")); bad_cases.append(("duplicate_extra", bad))
        bad = list(valid); bad.extend(("--port", "18131")); bad_cases.append(("duplicate_launch", bad))
        bad = list(valid); del bad[bad.index("--swa-full")]; bad_cases.append(("missing_core", bad))
        bad = list(valid); del bad[bad.index("--seed") + 1]; bad_cases.append(("missing_value", bad))
        bad = list(valid); bad.append("--ctx-size=32768"); bad_cases.append(("equals_spelling", bad))
        bad = list(valid); bad.append("unexpected-positional"); bad_cases.append(("positional", bad))
        bad = list(valid); bad.append("true"); bad_cases.append(("boolean_value", bad))
        for label, argv in bad_cases:
            with self.subTest(label=label):
                _exe, _model, data, process, _flags = self._server_process_fixture(argv)
                with self.assertRaises(manifest_tools.ManifestError):
                    manifest_tools._validate_live_process(
                        data, process, process_probe=lambda _pid, value=process: dict(value))

        _exe, _model, data, process, flags = self._server_process_fixture(valid)
        data["server_flags"] = flags[:-2]
        with self.assertRaisesRegex(manifest_tools.ManifestError, "full ordered live non-launch argv"):
            manifest_tools._validate_live_process(data, process, process_probe=lambda _pid: dict(process))
        data["server_flags"] = flags
        data["server_argv"] = list(data["server_argv"])
        data["server_argv"].append("--no-webui")
        with self.assertRaisesRegex(manifest_tools.ManifestError, "differs from saved provenance"):
            manifest_tools._validate_live_process(data, process, process_probe=lambda _pid: dict(process))

    def test_invalid_optional_flag_stops_before_client_health_chat_or_output_reservation(self):
        bad_argv = self._mixed_exact_server_argv()
        bad_argv[bad_argv.index("--temp") + 1] = "0.1"
        _exe, _model, data, process, _flags = self._server_process_fixture(bad_argv)
        items = self.root / "items.json"
        write_json(items, {})
        def reject_bad_live_flags(_path):
            return manifest_tools._validate_live_process(
                data, process, process_probe=lambda _pid: dict(process))
        for module, loader_name, kind in ((eval_a0, "load_validated_a0", "a0"),
                                          (eval_a1, "load_validated_a1", "a1")):
            with self.subTest(kind=kind), \
                    patch.object(module.manifest_tools, loader_name, return_value=({}, "hash", [])), \
                    patch.object(manifest_tools, "validate_server_provenance", side_effect=reject_bad_live_flags), \
                    patch.object(manifest_tools, "validate_record_output_paths") as reserve, \
                    patch.object(module, "GlimmerClient") as client_ctor, \
                    patch.object(sys, "argv", [module.__file__, "--items", str(items),
                                                "--server-provenance", str(self.root / "provenance.json"),
                                                "--out", "scratch_ds/evals/should-not-be-reserved.json"]):
                with self.assertRaisesRegex(manifest_tools.ManifestError, "registered setting"):
                    module.main()
                client_ctor.assert_not_called()
                reserve.assert_not_called()

    def test_consistent_wrong_binary_build_claim_rejected_before_recorded_client(self):
        import platform
        from datetime import datetime, timezone
        executable = self.root / "llama-server"
        executable.write_text("#!/bin/sh\nprintf 'version: 9876 (deadbee)\\n'\n")
        executable.chmod(0o700)
        model = self.root / "base.gguf"
        model.write_bytes(b"synthetic model")
        argv = [str(executable), "--alias", "muse-glimmer", "--ctx-size", "32768",
                "--model", str(model), "--temp", "0", "--top-k", "40", "--host", "127.0.0.1",
                "--jinja", "--reasoning-format", "deepseek", "--top-p", "1", "--swa-full",
                "--seed", "42", "--gpu-layers", "99", "--chat-template-kwargs",
                '{"reasoning_strength":"low"}', "--parallel", "1", "--no-webui", "--port", "18131"]
        flags = ["--alias", "muse-glimmer", "--ctx-size", "32768", "--temp", "0", "--top-k", "40",
                 "--jinja", "--reasoning-format", "deepseek", "--top-p", "1", "--swa-full", "--seed", "42",
                 "--gpu-layers", "99", "--chat-template-kwargs", '{"reasoning_strength":"low"}',
                 "--parallel", "1", "--no-webui"]
        process = {"pid": 31415, "host": platform.node(), "argv": argv,
                   "executable_path": str(executable), "executable_sha256": manifest_tools.digest_file(executable),
                   "started_utc": "2026-10-27T17:00:00Z"}
        url = "http://127.0.0.1:18131/v1/chat/completions"
        records = {"process": process, "model": {"model_path": str(model), "sha256": manifest_tools.digest_file(model)},
                   "health": {"status": "ok", "url": url},
                   "app_quiescent": {"inflight_llm_calls": 0, "queue_checked": True}}
        evidence = {}
        for name, record in records.items():
            path = self.root / (name + ".json")
            write_json(path, record)
            evidence[name] = {"path": str(path), "sha256": manifest_tools.digest_file(path)}
        evidence["model"]["model_path"] = str(model)
        build = self.root / "build.txt"
        build.write_text("version: 1234 (f8def7f)\n")
        evidence["build"] = {"path": str(build), "sha256": manifest_tools.digest_file(build)}
        now = datetime.now(timezone.utc)
        data = {"gguf_sha256": manifest_tools.digest_file(model), "llama_cpp_commit": "f8def7f",
                "llama_cpp_build": "1234", "server_flags": flags, "server_argv": argv,
                "executable_path": str(executable), "executable_sha256": process["executable_sha256"],
                "process_started_utc": process["started_utc"], "url": url, "pid": process["pid"],
                "host": process["host"], "verified_utc": now.isoformat(), "app_commit": "app",
                "source_commit": "review", "evidence": evidence}
        provenance = self.root / "provenance.json"
        write_json(provenance, data)
        probe = lambda _pid: dict(process)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "live executable version"):
            manifest_tools.validate_server_provenance(str(provenance), root=self.root, now=now.timestamp(),
                process_probe=probe)
        # Drive both actual recorded entry points through this same preflight failure.
        items = self.root / "items.json"
        write_json(items, {})
        original = manifest_tools.validate_server_provenance
        def validate(path):
            return original(path, root=self.root, now=now.timestamp(), process_probe=probe)
        for module in (eval_a0, eval_a1):
            loader = "load_validated_a0" if module is eval_a0 else "load_validated_a1"
            with patch.object(manifest_tools, loader, return_value=({}, "hash", [])), \
                 patch.object(manifest_tools, "validate_server_provenance", side_effect=validate), \
                 patch.object(module, "GlimmerClient") as client, \
                 patch.object(sys, "argv", [module.__file__, "--items", str(items),
                                             "--server-provenance", str(provenance), "--out", "ignored"]):
                with self.assertRaisesRegex(manifest_tools.ManifestError, "live executable version"):
                    module.main()
                client.assert_not_called()
        # A stray pinned hash outside the actual version line is not build provenance.
        stray = "version: 9876 (deadbee)\nextra text mentions f8def7f\n"
        data["llama_cpp_build"] = "9876"
        build.write_text(stray)
        data["evidence"]["build"]["sha256"] = manifest_tools.digest_file(build)
        write_json(provenance, data)
        with self.assertRaisesRegex(manifest_tools.ManifestError, "live executable version"):
            original(str(provenance), root=self.root, now=now.timestamp(), process_probe=probe,
                     version_probe=lambda _path: stray)
        # Ambiguous or malformed version records fail even with the correct saved bytes.
        for bad in ("version: 1234 (f8def7f)\nversion: 1234 (f8def7f)\n",
                    "version: 1234 (f8def7f) extra\n"):
            data["llama_cpp_build"] = "1234"
            build.write_text(bad)
            data["evidence"]["build"]["sha256"] = manifest_tools.digest_file(build)
            write_json(provenance, data)
            with self.assertRaisesRegex(manifest_tools.ManifestError, "live executable version"):
                original(str(provenance), root=self.root, now=now.timestamp(), process_probe=probe,
                         version_probe=lambda _path, value=bad: value)
        build.write_text("version: 1234 (f8def7f)\n")
        data["evidence"]["build"]["sha256"] = manifest_tools.digest_file(build)
        write_json(provenance, data)
        # Matching output from the same probe can progress beyond the version gate.
        with patch.object(manifest_tools, "git_head_for", side_effect=["app", "review"]), \
             patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="")):
            accepted = original(str(provenance), root=self.root, now=now.timestamp(), process_probe=probe,
                                version_probe=lambda _path: "version: 1234 (f8def7f)\n")
        self.assertEqual(accepted["executable_sha256"], process["executable_sha256"])


class A0FullAnswerScoringTests(SyntheticWorkspace):
    def _score_fixture(self):
        _manifest, _a1, a0_doc = self.build_manifest()
        items_path = self.eval_dir / "synthetic-a0-items.json"
        write_json(items_path, a0_doc)
        rows, annotations = [], []
        values = [45, 55, 56] + [50] * 27
        for i, item in enumerate(a0_doc["items"]):
            text = f"{values[i]}%"
            rows.append({"i": i, "question": item["question"], "review": item["review"],
                         "log": item["log"], "answer": text,
                         "error": None, "finish_reason": "stop",
                         "candidate_claims": [{"claim": text, "sentence": text}]})
            annotations.append({"answer_index": i, "complete": True, "unresolved_claims": [],
                                "claims": [{"claim_id": f"claim-{i}", "start": 0, "end": len(text),
                                            "text_span": text, "checkable": True,
                                            "claimed_value_pp": values[i],
                                            "engine_ref": "summary.start_win_prob"}]})
        owner = [{"answer_index": i, "complete": True, "unresolved_claims": [], "corrections": []}
                 for i in range(10)]
        output = {"pilot": False, "n": 30, "manifest_sha256": a0_doc["manifest_sha256"],
                  "items_sha256": manifest_tools.digest_file(items_path), "rows": rows,
                  "provenance": {"synthetic": True}}
        answers_path = self.eval_dir / "noengine_v1.json"
        answer_bytes = (json.dumps(output, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
        answers_path.write_bytes(answer_bytes)
        receipt_path = Path(str(answers_path) + ".receipt.json")
        write_json(receipt_path, {"schema": "c9.a0-answer-receipt-v1", "n": 30,
                                  "answer_sha256": hashlib.sha256(answer_bytes).hexdigest(),
                                  "manifest_sha256": a0_doc["manifest_sha256"],
                                  "items_sha256": manifest_tools.digest_file(items_path)})
        return a0_doc, items_path, output, annotations, owner, answers_path, receipt_path

    def _score(self, fixture, annotations=None, owner=None):
        doc, items_path, output, default_annotations, default_owner, answers_path, receipt_path = fixture
        return score_a0.score_answers(output, doc,
                                      (default_annotations if annotations is None else annotations, 0),
                                      (default_owner if owner is None else owner, 0),
                                      root=self.root, items_path=items_path, answers_path=answers_path,
                                      receipt_path=receipt_path)

    def test_all_30_numeric_claims_use_review_refs_and_five_point_boundary(self):
        fixture = self._score_fixture()
        result = self._score(fixture)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["answer_count"], 30)
        self.assertEqual(result["numeric_claim_count"], 30)
        self.assertEqual(result["checkable_count"], 30)
        self.assertEqual(result["within_5_count"], 29)  # 45 and 55 pass; 56 is outside.
        self.assertEqual(result["owner_check_count"], 10)
        self.assertIn("all numeric claims in all 30 complete answers", result["checkable_denominator"])

    def test_missing_annotation_or_owner_check_withholds_all_rates(self):
        fixture = self._score_fixture()
        pending = self._score(fixture, annotations=fixture[3][:-1], owner=fixture[4][:-1])
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["checkable_share"])
        self.assertIsNone(pending["within_5_share"])
        self.assertEqual(pending["owner_check_count"], 9)

    def test_unresolved_claim_or_bad_reference_never_yields_a_rate(self):
        fixture = self._score_fixture()
        annotations = fixture[3]
        annotations[0]["claims"][0]["engine_ref"] = "unregistered.secret"
        pending = self._score(fixture, annotations=annotations)
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["numeric_claim_count"])
        self.assertEqual(pending["unresolved_count"], 1)

    def test_missing_and_malformed_claim_ids_are_pending_for_annotations_and_owner_corrections(self):
        fixture = self._score_fixture()
        original_claim = copy.deepcopy(fixture[3][0]["claims"][0])
        original_owner = copy.deepcopy(fixture[4][0])
        for malformed in ("missing", None, "", 17):
            annotations = fixture[3]
            claim = copy.deepcopy(original_claim)
            if malformed == "missing":
                claim.pop("claim_id")
            else:
                claim["claim_id"] = malformed
            annotations[0]["claims"] = [claim]
            result = self._score(fixture, annotations=annotations)
            self.assertEqual(result["status"], "pending", malformed)
            self.assertIsNone(result["checkable_share"], malformed)
            self.assertIsNone(result["within_5_share"], malformed)
            annotations[0]["claims"] = [copy.deepcopy(original_claim)]

            owner = fixture[4]
            corrected = copy.deepcopy(original_claim)
            if malformed == "missing":
                corrected.pop("claim_id")
            else:
                corrected["claim_id"] = malformed
            owner[0]["corrections"] = [{"action": "replace", "claim_id": "claim-0", "claim": corrected}]
            result = self._score(fixture, owner=owner)
            self.assertEqual(result["status"], "pending", f"owner {malformed}")
            self.assertIsNone(result["checkable_share"], f"owner {malformed}")
            self.assertIsNone(result["within_5_share"], f"owner {malformed}")
            owner[0] = copy.deepcopy(original_owner)

        annotations = fixture[3]
        duplicate = copy.deepcopy(original_claim)
        annotations[0]["claims"].append(duplicate)
        pending = self._score(fixture, annotations=annotations)
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["checkable_share"])

    def test_answer_receipt_detects_byte_mutation_and_missing_receipt(self):
        fixture = self._score_fixture()
        answer_path, receipt_path = fixture[5], fixture[6]
        original = answer_path.read_bytes()
        answer_path.write_bytes(original.replace(b"45%", b"46%", 1))
        with self.assertRaisesRegex(score_a0.ScoreError, "do not match"):
            self._score(fixture)
        answer_path.write_bytes(original)
        receipt_path.unlink()
        with self.assertRaisesRegex(score_a0.ScoreError, "receipt"):
            self._score(fixture)

    def test_empty_error_and_unfinished_answer_rows_withhold_rates(self):
        fixture = self._score_fixture()
        original_output = copy.deepcopy(fixture[2])
        for field, value in (("answer", "  "), ("error", "synthetic failure"),
                             ("finish_reason", "length")):
            output = fixture[2]
            output.clear()
            output.update(copy.deepcopy(original_output))
            output["rows"][0][field] = value
            raw = (json.dumps(output, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
            fixture[5].write_bytes(raw)
            write_json(fixture[6], {"schema": "c9.a0-answer-receipt-v1", "n": 30,
                                    "answer_sha256": hashlib.sha256(raw).hexdigest(),
                                    "manifest_sha256": fixture[0]["manifest_sha256"],
                                    "items_sha256": manifest_tools.digest_file(fixture[1])})
            result = self._score(fixture)
            self.assertEqual(result["status"], "pending", field)
            self.assertIsNone(result["checkable_share"], field)
            self.assertIsNone(result["within_5_share"], field)


class A1BlindAuditTests(SyntheticWorkspace):
    def _make_sheet(self, *, trace_count=60, sentence_count=60, sheet_name="audit-sheet.jsonl"):
        if not hasattr(self, "_a1_doc"):
            _manifest, self._a1_doc, _a0_doc = self.build_manifest()
        a1_doc = self._a1_doc
        items_path = self.eval_dir / "synthetic-a1-items.json"
        if not items_path.exists():
            write_json(items_path, a1_doc)
        trace_path = self.root / "synthetic-a1-trace.jsonl"
        lines = []
        for i, item in enumerate(a1_doc["items"][:trace_count]):
            sentences = ([{"i": 0, "text": f"synthetic sentence {i}", "citations": []}]
                         if i < sentence_count else [])
            result = {"final": "glimmer_raw", "trace": [],
                      "rendered": {"sentences": sentences}}
            binding = {"expected_items": 60, "items_path": str(items_path.resolve()),
                       "items_sha256": manifest_tools.digest_file(items_path),
                       "manifest_sha256": a1_doc["manifest_sha256"],
                       "item": {key: item[key] for key in ("row_index", "pool", "uid", "side",
                                                            "rank_hash", "q")}}
            lines.append(json.dumps({"i": i, "review": item["review"], "q": item["q"],
                                     "question": item["question"], "final": "glimmer_raw",
                                     "trace_binding": binding,
                                     "result": result}, ensure_ascii=False))
        trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        sheet_path = self.root / sheet_name
        audit_a1.sheet(str(trace_path), str(sheet_path), root=self.root)
        return sheet_path, trace_path

    @staticmethod
    def _labels(sheet_path):
        rows = [json.loads(line) for line in sheet_path.read_text(encoding="utf-8").splitlines() if line]
        agent = [{"audit_id": row["audit_id"], "misleading": False, "category": None} for row in rows]
        owner = [label for label, row in zip(agent, rows) if row["owner_spot_check"]]
        return rows, agent, owner

    def test_audit_requires_every_blinded_label_and_all_15_seeded_owner_checks(self):
        sheet_path, _trace_path = self._make_sheet(sheet_name="audit-sheet-2.jsonl")
        rows, labels, owner = self._labels(sheet_path)
        self.assertEqual(len(rows), 60)
        self.assertEqual(sum(row["owner_spot_check"] for row in rows), 15)
        agent_path, owner_path = self.root / "labels.jsonl", self.root / "owner.jsonl"
        output, pending_output = self.root / "score-complete.json", self.root / "score-pending.json"
        agent_path.write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["n_audited"], 60)
        self.assertEqual(result["misleading"], 0)
        owner_path.write_text("\n".join(json.dumps(x) for x in owner[:-1]) + "\n", encoding="utf-8")
        audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(pending_output), root=self.root)
        pending = json.loads(pending_output.read_text(encoding="utf-8"))
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["n_audited"])
        self.assertIsNone(pending["gate_misleading_le_5pct"])

    def test_missing_agent_label_or_duplicate_id_is_pending(self):
        sheet_path, _trace_path = self._make_sheet(sheet_name="audit-sheet-2.jsonl")
        _rows, labels, owner = self._labels(sheet_path)
        agent_path, owner_path = self.root / "labels.jsonl", self.root / "owner.jsonl"
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        for index, broken in enumerate((labels[:-1], labels + [labels[0]])):
            output = self.root / f"score-{index}.json"
            agent_path.write_text("\n".join(json.dumps(x) for x in broken) + "\n", encoding="utf-8")
            audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "pending")
            self.assertIsNone(result["rate"])

    def test_owner_subset_must_match_the_seeded_sheet_positions(self):
        sheet_path, _trace_path = self._make_sheet()
        rows, labels, owner = self._labels(sheet_path)
        marked = [i for i, row in enumerate(rows) if row["owner_spot_check"]]
        unmarked = next(i for i, row in enumerate(rows) if not row["owner_spot_check"])
        rows[marked[0]]["owner_spot_check"] = False
        rows[unmarked]["owner_spot_check"] = True
        sheet_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        agent_path, owner_path, output = self.root / "labels.jsonl", self.root / "owner.jsonl", self.root / "score.json"
        agent_path.write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["owner_subset_valid"])
        self.assertIsNone(result["gate_misleading_le_5pct"])

    def test_audit_sheet_and_score_files_refuse_overwrite(self):
        sheet_path, trace_path = self._make_sheet()
        original = sheet_path.read_text(encoding="utf-8")
        with self.assertRaises(FileExistsError):
            audit_a1.sheet(str(trace_path), str(sheet_path), root=self.root)
        self.assertEqual(sheet_path.read_text(encoding="utf-8"), original)
        _rows, labels, owner = self._labels(sheet_path)
        agent_path, owner_path, output = self.root / "labels.jsonl", self.root / "owner.jsonl", self.root / "score.json"
        agent_path.write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
        with self.assertRaises(FileExistsError):
            audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)

    def test_trace_sheet_and_key_mutations_invalidate_the_audit_binding(self):
        sheet_path, trace_path = self._make_sheet()
        _rows, labels, owner = self._labels(sheet_path)
        agent_path, owner_path = self.root / "labels.jsonl", self.root / "owner.jsonl"
        agent_path.write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        trace_path.write_bytes(trace_path.read_bytes().replace(b"synthetic sentence 0", b"mutated sentence 0", 1))
        output = self.root / "trace-mutated-score.json"
        result = audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["trace_binding_valid"])
        self.assertIsNone(result["gate_misleading_le_5pct"])

        sheet_path, _trace_path = self._make_sheet(sheet_name="audit-sheet-2.jsonl")
        _rows, labels, owner = self._labels(sheet_path)
        agent_path.write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        key_path = Path(str(sheet_path) + ".key.jsonl")
        key_path.write_bytes(key_path.read_bytes().replace(b'"item": 0', b'"item": 9', 1))
        output = self.root / "key-mutated-score.json"
        result = audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["trace_binding_valid"])
        self.assertIsNone(result["gate_misleading_le_5pct"])

    def test_malformed_rendered_sentences_in_full_trace_cannot_shrink_population(self):
        for malformed in (None, {}, {"i": 0}, {"text": "text"}, {"i": True, "text": "text"},
                          {"i": 0, "text": ""}):
            with self.subTest(malformed=malformed):
                sheet, trace = self._make_sheet(sheet_name="initial.jsonl")
                rows = [json.loads(line) for line in trace.read_text().splitlines()]
                for row in rows:
                    row["result"]["rendered"]["sentences"] = [malformed]
                trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                out_sheet = self.root / "malformed.jsonl"
                audit_a1.sheet(str(trace), str(out_sheet), root=self.root)
                result = audit_a1.score(str(out_sheet), None, None, str(self.root / "malformed-score.json"), root=self.root)
                self.assertEqual(result["status"], "pending")
                self.assertFalse(result["trace_valid"])
                self.assertIsNone(result["population_sentences"])
                self.assertIsNone(result["gate_misleading_le_5pct"])
                for path in (sheet, Path(str(sheet) + ".key.jsonl"), Path(str(sheet) + ".meta.json"),
                             out_sheet, Path(str(out_sheet) + ".key.jsonl"), Path(str(out_sheet) + ".meta.json"),
                             self.root / "malformed-score.json"):
                    path.unlink()

    def test_short_population_and_first_twenty_trace_are_pending_without_gate_boolean(self):
        sheet_path, _trace_path = self._make_sheet(sentence_count=14)
        rows, labels, owner = self._labels(sheet_path)
        agent_path, owner_path = self.root / "labels.jsonl", self.root / "owner.jsonl"
        agent_path.write_text("\n".join(json.dumps(x) for x in labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in owner) + "\n", encoding="utf-8")
        output = self.root / "short-pop-score.json"
        result = audit_a1.score(str(sheet_path), str(agent_path), str(owner_path), str(output), root=self.root)
        self.assertEqual(result["status"], "pending")
        self.assertTrue(result["review_required"])
        self.assertEqual(result["population_sentences"], 14)
        self.assertIsNone(result["gate_misleading_le_5pct"])

        partial_sheet, _trace_path = self._make_sheet(trace_count=20, sheet_name="partial-sheet.jsonl")
        partial_rows, partial_labels, partial_owner = self._labels(partial_sheet)
        agent_path.write_text("\n".join(json.dumps(x) for x in partial_labels) + "\n", encoding="utf-8")
        owner_path.write_text("\n".join(json.dumps(x) for x in partial_owner) + "\n", encoding="utf-8")
        output = self.root / "partial-trace-score.json"
        result = audit_a1.score(str(partial_sheet), str(agent_path), str(owner_path), str(output), root=self.root)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["trace_valid"])
        self.assertIsNone(result["gate_misleading_le_5pct"])


if __name__ == "__main__":
    unittest.main()
