"""Synthetic tests for the public roster-hash manifest and exclusion consumers."""
import copy
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

from tools.belief import human_prep, targets
from tools.belief.roster_hash import (
    EXPECTED_VAL_ROSTERS, canonical_roster_key, load_val_roster_digests,
    roster_digest, validate_pool_manifest,
)
from tools.make_pools import replace_val_rosters_with_hashes


ROSTER_A = ["Pikachu", "Great Tusk", "Ogerpon-Wellspring", "Gholdengo", "Kingambit", "Dragonite"]
ROSTER_B = ["Ribombee", "Toxapex", "Garchomp", "Corviknight", "Rotom-Wash", "Weavile"]
ROSTER_C = ["Clefable", "Glimmora", "Dondozo", "Garganacl", "Meowscarada", "Volcarona"]


def fixture_manifest(rosters):
    digests = sorted(roster_digest(roster) for roster in rosters)
    n = len(digests)
    return {
        "source_split": "a" * 64,
        "rule": "synthetic fixture; no source teams or roster lists",
        "rosters_dev": 10,
        "rosters_val": n,
        "teams": {"hr_train": 20, "hr_val": 4},
        "content_sha256": {"hr_train": "b" * 64, "hr_val": "c" * 64},
        "tarball_sha256": "d" * 64,
        "val_roster_sha256": digests,
    }


def import_direct(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RosterHashManifestTests(unittest.TestCase):
    def test_canonicalizer_matches_order_case_and_punctuation(self):
        variants = [
            ROSTER_A,
            ["dragonite", "KINGAMBIT", "Gholdengo", "Ogerpon—Wellspring", "Great Tusk", "Pikachu"],
            "Dragonite|Kingambit|Gholdengo|Ogerpon-Wellspring|Great Tusk|Pikachu",
        ]
        keys = {canonical_roster_key(roster) for roster in variants}
        self.assertEqual(len(keys), 1)
        self.assertEqual(targets.norm_roster(variants[-1]), next(iter(keys)))
        self.assertEqual(roster_digest(variants[0]), roster_digest(variants[-1]))

    def test_canonicalizer_rejects_invalid_six_species(self):
        invalid = [
            ["Pikachu"] * 5,
            ["Pikachu", "Great Tusk", "Ogerpon-Wellspring", "Gholdengo", "Kingambit", ""],
            ["Pikachu", "PIKA-CHU", "Ogerpon-Wellspring", "Gholdengo", "Kingambit", "Dragonite"],
            ["Pikachu", "Great Tusk", "Ogerpon-Wellspring", "Gholdengo", "Kingambit", None],
            None,
        ]
        for roster in invalid:
            with self.subTest(roster_type=type(roster).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    canonical_roster_key(roster)

    def test_generator_consumer_digest_agreement_and_metadata_preservation(self):
        raw = {
            "source_split": "a" * 64, "rule": "synthetic", "rosters_dev": 12, "rosters_val": 2,
            "teams": {"hr_train": 30, "hr_val": 5},
            "content_sha256": {"hr_train": "b" * 64, "hr_val": "c" * 64},
            "tarball_sha256": "d" * 64,
            "val_rosters": ["|".join(ROSTER_A), ROSTER_B],
        }
        result = replace_val_rosters_with_hashes(raw)
        self.assertNotIn("val_rosters", result)
        self.assertEqual(result["val_roster_sha256"], sorted([roster_digest(ROSTER_A), roster_digest(ROSTER_B)]))
        self.assertEqual({k: v for k, v in raw.items() if k != "val_rosters"},
                         {k: v for k, v in result.items() if k != "val_roster_sha256"})
        self.assertEqual(validate_pool_manifest(result, expected_count=2),
                         {roster_digest(ROSTER_A), roster_digest(ROSTER_B)})
        both_fields = copy.deepcopy(raw); both_fields["val_roster_sha256"] = []
        with self.assertRaises(ValueError):
            replace_val_rosters_with_hashes(both_fields)

    def test_direct_script_and_package_imports_share_normalization(self):
        proposal_tools = Path(__file__).resolve().parents[1]
        belief_dir = proposal_tools / "belief"
        sys.path.insert(0, str(proposal_tools))
        sys.path.insert(0, str(belief_dir))
        direct_generator = import_direct("make_pools_direct_for_test", proposal_tools / "make_pools.py")
        direct_targets = import_direct("targets_direct_for_test", belief_dir / "targets.py")
        direct_human_prep = import_direct("human_prep_direct_for_test", belief_dir / "human_prep.py")
        key = canonical_roster_key(ROSTER_A)
        self.assertEqual(direct_targets.norm_roster("|".join(ROSTER_A)), key)
        self.assertEqual(direct_human_prep.roster_digest(ROSTER_A), roster_digest(ROSTER_A))
        self.assertEqual(direct_generator.replace_val_rosters_with_hashes(
            {"rosters_val": 1, "val_rosters": ["|".join(ROSTER_A)]}
        )["val_roster_sha256"], [roster_digest(ROSTER_A)])

    def test_proposed_real_manifest_is_digest_only_and_validates(self):
        proposed = Path(__file__).resolve().parents[2] / "data" / "pools-v1.json"
        digests = load_val_roster_digests(proposed)
        self.assertEqual(len(digests), EXPECTED_VAL_ROSTERS)

    def test_manifest_validation_fails_closed(self):
        good = fixture_manifest([ROSTER_A, ROSTER_B])
        self.assertEqual(validate_pool_manifest(good, expected_count=2),
                         {roster_digest(ROSTER_A), roster_digest(ROSTER_B)})
        bad_cases = []
        raw_key = copy.deepcopy(good); raw_key["val_rosters"] = ["synthetic raw roster"]; bad_cases.append(raw_key)
        missing_field = copy.deepcopy(good); del missing_field["source_split"]; bad_cases.append(missing_field)
        wrong_count = copy.deepcopy(good); wrong_count["rosters_val"] = 3; bad_cases.append(wrong_count)
        count_list = copy.deepcopy(good); count_list["val_roster_sha256"] = count_list["val_roster_sha256"][:1]; bad_cases.append(count_list)
        duplicate = copy.deepcopy(good); duplicate["val_roster_sha256"] = ["a" * 64, "a" * 64]; bad_cases.append(duplicate)
        uppercase = copy.deepcopy(good); uppercase["val_roster_sha256"][0] = uppercase["val_roster_sha256"][0].upper(); bad_cases.append(uppercase)
        bad_hash = copy.deepcopy(good); bad_hash["tarball_sha256"] = "bad"; bad_cases.append(bad_hash)
        bad_teams = copy.deepcopy(good); bad_teams["teams"]["hr_val"] = True; bad_cases.append(bad_teams)
        extra = copy.deepcopy(good); extra["unexpected"] = 1; bad_cases.append(extra)
        for manifest in bad_cases:
            with self.subTest(keys=tuple(sorted(manifest))):
                with self.assertRaises(ValueError):
                    validate_pool_manifest(manifest, expected_count=2)
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with self.assertRaises(FileNotFoundError):
                load_val_roster_digests(missing, expected_count=2)
            malformed = Path(tmp) / "bad.json"; malformed.write_text("{")
            with self.assertRaises(ValueError):
                load_val_roster_digests(malformed, expected_count=2)
            wrong_expected_count = Path(tmp) / "good.json"; wrong_expected_count.write_text(json.dumps(good))
            with self.assertRaises(ValueError):
                load_val_roster_digests(wrong_expected_count, expected_count=3)

    def test_hr_val_and_reserved_exclusions_are_hash_only_and_diagnostic_is_hr_val_only(self):
        hr_val_set = {"|".join(ROSTER_A)}
        expected_hr_val = {roster_digest(ROSTER_A)}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pools = root / "pools.json"; pools.write_text(json.dumps(fixture_manifest(hr_val_set)))
            split = root / "split.json"; split.write_text(json.dumps({"reserved_files": ["sealed-fixture.json"]}))
            validated = root / "validated.jsonl"
            validated.write_text(json.dumps({"file": "data/validated/sealed-fixture.json", "species": ROSTER_C}) + "\n")
            excluded = targets.excluded_rosters(pools, split, validated, expected_count=1)
            hr_only = targets.hr_val_roster_digests(pools, expected_count=1)
            self.assertEqual(hr_only, expected_hr_val)
            self.assertEqual(excluded, expected_hr_val | {roster_digest(ROSTER_C)})
            diagnostic = Counter()
            self.assertTrue(targets.record_hr_val_diagnostic(diagnostic, "|".join(ROSTER_A), hr_only))
            self.assertFalse(targets.record_hr_val_diagnostic(diagnostic, "|".join(ROSTER_C), hr_only))
            self.assertFalse(targets.record_hr_val_diagnostic(diagnostic, "|".join(ROSTER_B), hr_only))
            self.assertEqual(diagnostic["examples_from_hr_val_rosters"], 1)
            rows = [
                {"roster": "|".join(ROSTER_A), "split": "dev", "scored": {"item": True}, "label": "hr-val"},
                {"roster": "|".join(ROSTER_C), "split": "train", "scored": {"item": False}, "label": "reserved"},
                {"roster": "|".join(ROSTER_B), "split": "test", "scored": {"item": True}, "label": "nonmember"},
            ]
            original = copy.deepcopy(rows)
            kept = [row for row in rows if not human_prep.is_excluded_example(row, excluded)]
            self.assertEqual(kept, [original[2]])
            with self.assertRaises(ValueError):
                human_prep.is_excluded_example({"roster": "Pikachu"}, excluded)
            self.assertEqual(kept[0]["split"], "test")
            self.assertEqual(kept[0]["scored"], {"item": True})
            self.assertEqual(kept[0]["label"], "nonmember")
            self.assertFalse((root / "data" / "validated" / "sealed-fixture.json").exists())

    def test_reserved_metadata_missing_malformed_or_incomplete_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            split = root / "split.json"; split.write_text(json.dumps({"reserved_files": ["reserved.json"]}))
            validated = root / "validated.jsonl"; validated.write_text("")
            with self.assertRaises(ValueError):
                targets.reserved_roster_digests(split, validated)
            split.write_text(json.dumps({"reserved_files": "reserved.json"}))
            with self.assertRaises(ValueError):
                targets.reserved_roster_digests(split, validated)
            split.write_text(json.dumps({"reserved_files": ["reserved.json"]}))
            validated.write_text(json.dumps({"file": "reserved.json", "species": ["Pikachu"]}) + "\n")
            with self.assertRaises(ValueError):
                targets.reserved_roster_digests(split, validated)
            split.write_text("{")
            with self.assertRaises(ValueError):
                targets.reserved_roster_digests(split, validated)
            with self.assertRaises(FileNotFoundError):
                targets.reserved_roster_digests(root / "missing.json", validated)
            with self.assertRaises(FileNotFoundError):
                targets.reserved_roster_digests(split, root / "missing-validated.jsonl")

    def test_capping_and_example_fields_remain_unchanged(self):
        self.assertEqual(targets.split_of(canonical_roster_key(ROSTER_A)), "train")
        self.assertEqual(targets.split_of(canonical_roster_key(ROSTER_B)), "test")
        self.assertEqual(targets.split_of(canonical_roster_key(ROSTER_C)), "dev")
        rows = [{"game": "synthetic/game", "side": "p1", "skey": "pikachu", "turn": i, "k": i,
                 "split": "dev", "y": {"item": "fixture", "moves": ["fixture-move"]}} for i in range(1, 6)]
        before = copy.deepcopy(rows)
        capped = list(targets.capped(rows, max_per_mon=2, seed=17))
        self.assertEqual(len(capped), 2)
        self.assertTrue(all(row["split"] == "dev" and row["y"] == before[row["turn"] - 1]["y"] for row in capped))
        self.assertEqual(rows, before)


if __name__ == "__main__":
    unittest.main()
