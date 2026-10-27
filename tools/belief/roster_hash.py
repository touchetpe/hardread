"""Canonical, fail-closed digests for six-species rosters in the public pool manifest."""
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import re

DOMAIN = b"hardread-roster-v1\0"
EXPECTED_VAL_ROSTERS = 596
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_MANIFEST_FIELDS = {
    "source_split", "rule", "rosters_dev", "rosters_val", "teams",
    "content_sha256", "tarball_sha256", "val_roster_sha256",
}


def toid(value):
    """Use the existing Showdown ID normalization (None stays None)."""
    if value is None:
        return None
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def canonical_roster_key(roster):
    """Return a sorted six-species Showdown key from a sequence or pipe-delimited string."""
    if isinstance(roster, str):
        species = roster.split("|")
    elif isinstance(roster, Sequence) and not isinstance(roster, (bytes, bytearray)):
        species = list(roster)
    else:
        raise TypeError("roster must be a six-species sequence or pipe-delimited string")
    if len(species) != 6:
        raise ValueError("roster must contain exactly six species")
    ids = []
    for item in species:
        if not isinstance(item, str):
            raise TypeError("every roster species must be a string")
        species_id = toid(item)
        if not species_id:
            raise ValueError("roster species must normalize to nonempty IDs")
        ids.append(species_id)
    if len(set(ids)) != 6:
        raise ValueError("roster must contain six distinct canonical species")
    return "|".join(sorted(ids))


def roster_digest(roster):
    """Hash the canonical roster key with the versioned domain separator."""
    key = canonical_roster_key(roster)
    return hashlib.sha256(DOMAIN + key.encode("utf-8")).hexdigest()


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_digest(value):
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def validate_pool_manifest(manifest, expected_count=EXPECTED_VAL_ROSTERS):
    """Validate the public hash-manifest schema and return its held-out digest set."""
    if not isinstance(manifest, dict):
        raise ValueError("pool manifest must be a JSON object")
    if "val_rosters" in manifest:
        raise ValueError("public pool manifest contains the retired raw roster field")
    if set(manifest) != _REQUIRED_MANIFEST_FIELDS:
        raise ValueError("pool manifest fields do not match the required hash schema")
    if not _is_digest(manifest["source_split"]):
        raise ValueError("pool manifest source_split must be a lowercase SHA-256 digest")
    if not isinstance(manifest["rule"], str) or not manifest["rule"].strip():
        raise ValueError("pool manifest rule must be a nonempty string")
    if not _is_count(manifest["rosters_dev"]):
        raise ValueError("pool manifest rosters_dev must be a nonnegative integer")
    if not _is_count(manifest["rosters_val"]):
        raise ValueError("pool manifest rosters_val must be a nonnegative integer")
    if manifest["rosters_val"] != expected_count:
        raise ValueError("pool manifest held-out roster count does not match the required count")
    teams = manifest["teams"]
    if not isinstance(teams, dict) or set(teams) != {"hr_train", "hr_val"}:
        raise ValueError("pool manifest teams must contain exactly hr_train and hr_val counts")
    if any(not _is_count(value) for value in teams.values()):
        raise ValueError("pool manifest team counts must be nonnegative integers")
    content = manifest["content_sha256"]
    if not isinstance(content, dict) or set(content) != {"hr_train", "hr_val"}:
        raise ValueError("pool manifest content_sha256 must contain the two pool hashes")
    if any(not _is_digest(value) for value in content.values()):
        raise ValueError("pool manifest content hashes must be lowercase SHA-256 digests")
    if not _is_digest(manifest["tarball_sha256"]):
        raise ValueError("pool manifest tarball hash must be a lowercase SHA-256 digest")
    digests = manifest["val_roster_sha256"]
    if not isinstance(digests, list) or len(digests) != expected_count:
        raise ValueError("pool manifest must contain the expected number of roster digests")
    if any(not _is_digest(value) for value in digests):
        raise ValueError("pool manifest roster digests must be lowercase SHA-256 strings")
    if len(set(digests)) != expected_count:
        raise ValueError("pool manifest roster digests must be unique")
    if len(digests) != manifest["rosters_val"]:
        raise ValueError("pool manifest roster digest count does not match rosters_val")
    return set(digests)


def load_val_roster_digests(path, expected_count=EXPECTED_VAL_ROSTERS):
    """Load a public pool manifest strictly; absent or malformed input never means no exclusions."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("required public pool manifest is missing")
    try:
        manifest = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("required public pool manifest is unreadable or malformed") from exc
    return validate_pool_manifest(manifest, expected_count=expected_count)
