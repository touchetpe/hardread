#!/usr/bin/env python3
"""Candidate only: unpack approved, hash-verified crate archives and engine sdist into a fresh run.

Do not invoke until the bounded acquisition/install scope is approved. This script never
contacts a network and refuses any archive member outside its expected root.
"""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def extract_exact(archive_bytes, root_name, destination):
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    with tarfile.open(fileobj=__import__("io").BytesIO(archive_bytes), mode="r:gz") as tar:
        seen, total, count = set(), 0, 0
        for member in tar:
            count += 1
            total += member.size
            if count > 100000 or total > 512 * 1024 * 1024:
                raise RuntimeError("archive extraction bound exceeded")
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or not parts or parts[0] != root_name or any(p in ("", ".", "..") for p in parts):
                raise RuntimeError(f"unsafe archive member: {member.name}")
            relative = Path(*parts[1:])
            if str(relative) in seen:
                raise RuntimeError("duplicate archive path")
            seen.add(str(relative))
            target = destination / relative
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            elif member.isfile():
                source = tar.extractfile(member)
                if source is None:
                    raise RuntimeError(f"unreadable archive member: {member.name}")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with target.open("xb") as out:
                    out.write(source.read())
            else:
                raise RuntimeError(f"unsupported archive member: {member.name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--closure", type=Path, required=True)
    ap.add_argument("--crates", type=Path, required=True)
    ap.add_argument("--engine-archive", type=Path, required=True)
    args = ap.parse_args()
    run = args.run.resolve(strict=True)
    closure = json.loads(args.closure.read_text())
    crates = json.loads(args.crates.read_text())
    if len(crates["registry_packages"]) != 55 or crates["all_registry_checksums_match_lock"] is not True:
        raise RuntimeError("unexpected registry metadata")

    engine = args.engine_archive.read_bytes()
    expected = closure["native_engine"]["approved_existing_sdist"]
    if len(engine) != expected["size_bytes"] or sha256(engine) != expected["sha256"]:
        raise RuntimeError("engine sdist mismatch")
    source = run / "engine-src"
    extract_exact(engine, "poke_engine-0.0.48", source)
    lock = source / "Cargo.lock"
    if sha256(lock.read_bytes()) != "26032d8b855cce6c80722309e27a612529209cfbb16d77983b3905ff1c17276c":
        raise RuntimeError("engine Cargo.lock mismatch")

    vendor = run / "vendor"
    vendor.mkdir(mode=0o700, exist_ok=False)
    for row in crates["registry_packages"]:
        stem = f"{row['name']}-{row['version']}"
        archive = run / "crate-archives" / (stem + ".crate")
        body = archive.read_bytes()
        if len(body) != row["crate_size_bytes"] or sha256(body) != row["lock_checksum"]:
            raise RuntimeError(f"crate mismatch: {stem}")
        target = vendor / stem
        extract_exact(body, stem, target)
        hashes = {}
        for path in target.rglob("*"):
            if path.is_file():
                relative = path.relative_to(target).as_posix()
                if relative == ".cargo-checksum.json":
                    raise RuntimeError(f"unexpected checksum file: {stem}")
                hashes[relative] = sha256(path.read_bytes())
        (target / ".cargo-checksum.json").write_text(
            json.dumps({"files": hashes, "package": row["lock_checksum"]}, sort_keys=True)
        )

    config_dir = source / ".cargo"
    config_dir.mkdir(mode=0o700, exist_ok=True)
    config = config_dir / "config.toml"
    with config.open("x") as out:
        out.write(
        '[source.crates-io]\nreplace-with = "vendored-sources"\n\n'
        '[source.vendored-sources]\ndirectory = '
        + json.dumps(str(vendor)) + '\n\n[net]\noffline = true\n'
        )
    print(json.dumps({"engine_source": str(source), "vendor": str(vendor),
                      "registry_crates": 55, "network": "none"}))


if __name__ == "__main__":
    main()
